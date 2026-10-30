# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Anonymise retailers and the personal fields of their orders and visits, keeping amounts,
quantities and dates for reporting.

Used by store privacy requests (``field_sales/shopify/privacy.py``) and by the daily
``anonymise_inactive_retailers()`` (Store Connection ``retailer_inactive_anonymise_days``).
Writes go straight to the database: no controllers run, so nothing is pushed to the store.
Edit history (Version) and emails (Communication) of anonymised records are deleted, since
they hold the old values.
"""

from __future__ import annotations

import frappe
from frappe.sessions import clear_sessions
from frappe.utils import add_days, cint, now_datetime

from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync

REDACTED = "Redacted"
REDACTED_PHONE = "0000000000"  # Retailer.phone is mandatory and validated as a phone number


def anonymise(retailers: list[str], orders: list[str]) -> dict:
	"""Anonymise the given Retailers, their Visits and the given Field Orders."""
	for name in retailers:
		# The login user holds the person's email and keeps working after the retailer
		# is redacted: disable it and drop the link. Left enabled, an anonymised
		# retailer could still sign in and see its orders.
		login = frappe.db.get_value("Retailer", name, "user")
		if login and login not in ("Administrator", "Guest"):
			frappe.db.set_value("User", login, "enabled", 0, update_modified=False)
			clear_sessions(user=login, force=True)
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

	history = _delete_history("Retailer", retailers) + _delete_history("Visit", [v.name for v in visits])
	history += _delete_history("Field Order", orders)
	return {
		"retailers": retailers,
		"visits": len(visits),
		"field_orders": len(orders),
		"history_deleted": history,
	}


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


def _chunks(values: list, size: int):
	for i in range(0, len(values), size):
		yield values[i : i + size]


def anonymise_inactive_retailers() -> None:
	"""
	Daily: anonymise Inactive retailers (and their orders' and visits' personal fields) not
	changed for ``retailer_inactive_anonymise_days``. 0 (the default) turns it off.
	"""
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
	frappe.db.commit()
