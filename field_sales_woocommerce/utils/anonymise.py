# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Anonymise retailers and the personal fields of their orders and visits, keeping amounts,
quantities and dates for reporting.

Used by store privacy requests (``field_sales/shopify/privacy.py``) and by the daily
``anonymise_inactive_retailers()`` (Store Connection ``retailer_inactive_anonymise_days``).
Writes go straight to the database: no controllers run, so nothing is pushed to the store.

Everything else that holds the old values goes too: edit history (Version), emails
(Communication), comments and notes (Comment), bell notifications (Notification Log), the
request and response bodies of their Sync Logs and the payloads of their Razorpay webhook
events. The retailer's login user is disabled and anonymised in place: renamed to a
``redacted-…`` address (links stay valid) with its personal fields blanked.
"""

from __future__ import annotations

import frappe
from frappe.model.rename_doc import rename_doc
from frappe.sessions import clear_sessions
from frappe.utils import add_days, cint, now_datetime

from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync
from field_sales_woocommerce.utils.db import safe_commit

REDACTED = "Redacted"
REDACTED_PHONE = "0000000000"  # Retailer.phone is mandatory and validated as a phone number
REDACTED_EMAIL_DOMAIN = "redacted.invalid"
# A retailer login holds only this role (plus Frappe's automatic ones); any other role is staff.
RETAILER_LOGIN_ROLES = {"Field Sales Retailer", "All", "Guest"}
# User fields that describe the person. Blanked when the login is anonymised.
USER_PERSONAL_FIELDS = (
	"middle_name", "last_name", "username", "user_image", "gender", "phone", "mobile_no",
	"birth_date", "location", "interest", "bio",
)  # fmt: skip


def anonymise(retailers: list[str], orders: list[str]) -> dict:
	"""Anonymise the given Retailers, their Visits and the given Field Orders."""
	logins = []
	for name in retailers:
		# The login user holds the person's email and keeps working after the retailer
		# is redacted: disable it and drop the link. Left enabled, an anonymised
		# retailer could still sign in and see its orders.
		login = frappe.db.get_value("Retailer", name, "user")
		if login and login not in ("Administrator", "Guest"):
			frappe.db.set_value("User", login, "enabled", 0, update_modified=False)
			clear_sessions(user=login, force=True)
			logins.append(login)
		frappe.db.set_value(
			"Retailer",
			name,
			{
				"retailer_name": f"{REDACTED} {name}",
				"owner_name": None,
				"phone": REDACTED_PHONE,
				"email": None,
				"gstin": None,
				"user": None,
				"address_line1": REDACTED,
				"address_line2": None,
				"city": REDACTED,
				"state": None,
				"pincode": None,
				"latitude": 0,
				"longitude": 0,
				"location_map": None,
				"status": "Inactive",
				"sync_error": None,
			},
			update_modified=False,
		)

	visits = (
		frappe.get_all("Visit", filters={"retailer": ("in", retailers)}, fields=["name", "retailer"])
		if retailers
		else []
	)
	for visit in visits:
		frappe.db.set_value(
			"Visit",
			visit.name,
			{
				"retailer_name": f"{REDACTED} {visit.retailer}",
				"city": REDACTED,
				"retailer_latitude": 0,
				"retailer_longitude": 0,
				"notes": None,
				"no_order_remarks": None,
			},
			update_modified=False,
		)

	order_rows = (
		frappe.get_all(
			"Field Order",
			filters={"name": ("in", orders)},
			fields=["name", "retailer", "confirmation_status"],
		)
		if orders
		else []
	)
	for order in order_rows:
		frappe.db.set_value(
			"Field Order",
			order.name,
			{
				"retailer_name": f"{REDACTED} {order.retailer}",
				"remarks": None,
				# The status names the address the confirmation went to.
				"confirmation_status": REDACTED if order.confirmation_status else None,
			},
			update_modified=False,
		)

	records = {"Retailer": retailers, "Visit": [v.name for v in visits], "Field Order": orders}
	result = {
		"retailers": retailers,
		"visits": len(visits),
		"field_orders": len(orders),
		"history_deleted": 0,
		"comments_deleted": 0,
		"notifications_deleted": 0,
		"sync_log_bodies_cleared": 0,
		"webhook_payloads_cleared": _clear_webhook_payloads(orders),
		"users_anonymised": 0,
		"users_not_renamed": 0,
	}
	for doctype, names in records.items():
		result["history_deleted"] += _delete_history(doctype, names)
		result["comments_deleted"] += _delete_comments(doctype, names)
		result["notifications_deleted"] += _delete_notifications(doctype, names)
		result["sync_log_bodies_cleared"] += _clear_sync_log_bodies(doctype, names)
	for login in logins:
		renamed = _anonymise_login(login)
		if renamed is not None:
			result["users_anonymised"] += 1
			result["users_not_renamed"] += 0 if renamed else 1
	return result


def _anonymise_login(user: str) -> bool | None:
	"""
	Blank a retailer login's personal fields and rename it to a ``redacted-…`` address, so its
	email is gone too (the User's name is the email). Renaming keeps every link valid, and
	Frappe queues its own job that rewrites ``owner`` / ``modified_by`` columns to the new name.

	:return: ``None`` when the user is not only a retailer login (staff are never touched),
		``True`` when renamed, ``False`` when the rename was refused (e.g. Frappe is still
		applying an earlier rename); the personal fields are blanked either way.
	"""
	if not frappe.db.exists("User", user) or set(frappe.get_roles(user)) - RETAILER_LOGIN_ROLES:
		return None
	frappe.db.set_value(
		"User",
		user,
		{"first_name": REDACTED, "full_name": REDACTED, **dict.fromkeys(USER_PERSONAL_FIELDS)},
		update_modified=False,
	)
	new_name = f"redacted-{frappe.generate_hash(length=12)}@{REDACTED_EMAIL_DOMAIN}"
	try:
		# The privacy job runs as whoever queued it (a webhook: Guest), so no permission check.
		rename_doc("User", user, new_name, force=True, ignore_permissions=True, show_alert=False)
	except frappe.ValidationError:
		return False
	frappe.db.set_value("User", new_name, "email", new_name, update_modified=False)
	return True


def _delete_history(doctype: str, names: list[str]) -> int:
	"""Delete Version and Communication rows of ``names`` (they hold the old personal values)."""
	if not names:
		return 0
	count = 0
	for chunk in _chunks(names, 500):
		filters = {"ref_doctype": doctype, "docname": ("in", chunk)}
		count += frappe.db.count("Version", filters)
		frappe.db.delete("Version", filters)
		comm = {"reference_doctype": doctype, "reference_name": ("in", chunk)}
		for name in frappe.get_all("Communication", filters=comm, pluck="name"):
			frappe.delete_doc("Communication", name, ignore_permissions=True, force=True)
			count += 1
	return count


def _delete_comments(doctype: str, names: list[str]) -> int:
	"""Delete Comment rows (notes, the app's own messages) and the records' cached ``_comments``."""
	count = 0
	for chunk in _chunks(names, 500):
		filters = {"reference_doctype": doctype, "reference_name": ("in", chunk)}
		count += frappe.db.count("Comment", filters)
		frappe.db.delete("Comment", filters)
		table = frappe.qb.DocType(doctype)
		frappe.qb.update(table).set(table._comments, None).where(table.name.isin(chunk)).run()
	return count


def _delete_notifications(doctype: str, names: list[str]) -> int:
	"""Delete bell notifications about the records (their subjects name the retailer)."""
	count = 0
	for chunk in _chunks(names, 500):
		filters = {"document_type": doctype, "document_name": ("in", chunk)}
		count += frappe.db.count("Notification Log", filters)
		frappe.db.delete("Notification Log", filters)
	return count


def _clear_sync_log_bodies(doctype: str, names: list[str]) -> int:
	"""Blank the request and response bodies of the records' Sync Logs (customer and order pushes)."""
	count = 0
	log = frappe.qb.DocType("Sync Log")
	for chunk in _chunks(names, 500):
		where = (log.reference_doctype == doctype) & log.reference_name.isin(chunk)
		count += frappe.db.count("Sync Log", {"reference_doctype": doctype, "reference_name": ("in", chunk)})
		frappe.qb.update(log).set(log.request_payload, None).set(log.response_payload, None).where(where).run()
	return count


def _clear_webhook_payloads(orders: list[str]) -> int:
	"""Blank the raw bodies of the orders' Razorpay webhook events (they hold the payer's contact)."""
	count = 0
	event = frappe.qb.DocType("Razorpay Webhook Event")
	for chunk in _chunks(orders, 500):
		count += frappe.db.count("Razorpay Webhook Event", {"reference_order": ("in", chunk)})
		frappe.qb.update(event).set(event.payload, None).where(event.reference_order.isin(chunk)).run()
	return count


def _chunks(values: list, size: int):
	for i in range(0, len(values), size):
		yield values[i : i + size]


def anonymise_inactive_retailers() -> None:
	"""
	Daily: anonymise Inactive retailers (and their orders' and visits' personal fields) not
	changed for ``retailer_inactive_anonymise_days``. 0 (the default) turns it off.
	"""
	try:
		days = cint(frappe.db.get_single_value("Store Connection", "retailer_inactive_anonymise_days"))
		if days <= 0:
			return
		cutoff = add_days(now_datetime(), -days)
		retailers = frappe.get_all(
			"Retailer",
			filters={
				"status": "Inactive",
				"modified": ("<", cutoff),
				"retailer_name": ("not like", f"{REDACTED} %"),
			},
			pluck="name",
			limit=500,
		)
		if not retailers:
			return
		orders = frappe.get_all("Field Order", filters={"retailer": ("in", retailers)}, pluck="name")
		result = anonymise(retailers, orders)
		log_sync(
			platform=frappe.db.get_single_value("Store Connection", "platform") or "Shopify",
			operation="Privacy Request",
			status="Success",
			response_payload=frappe.as_json({"job": "anonymise_inactive_retailers", "days": days, **result}),
		)
		safe_commit()
	except Exception:
		# Keep a record of the failure (the job rolls back), then fail the job as before.
		frappe.db.rollback()
		frappe.log_error(title="Field Sales scheduled job failed: anonymise_inactive_retailers")
		safe_commit()
		raise

