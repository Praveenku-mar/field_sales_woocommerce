# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral customer sync, both ways.

- Push: Retailer -> store customer (``push_retailer``); the connector decides
  whether a push is a create or an update.
- Import: store customers -> Retailer (``import_customers``).

Talks to the store only through ``get_connector()``.
"""

import time

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from field_sales_woocommerce.connectors import ConnectorError, get_connector
from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync
from field_sales_woocommerce.sync.notify import notify_customer_sync_failure, notify_sync_result

# Retailer fields sent to the store; a change to any of them triggers a push.
SYNCED_FIELDS = (
	"retailer_name",
	"owner_name",
	"phone",
	"email",
	"retailer_type",
	"address_line1",
	"address_line2",
	"city",
	"state",
	"pincode",
	"country",
)


def enqueue_push(retailer_name: str):
	"""
	Queue ``push_retailer`` once the current transaction commits.

	Deduplicated per retailer: if a push for it is already queued, no second
	one is added.
	"""
	frappe.enqueue(
		"field_sales_woocommerce.sync.customers.push_retailer",
		retailer_name=retailer_name,
		job_id=f"field_sales:push_retailer:{retailer_name}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def push_retailers(names: list[str]):
	"""Queue a push for each retailer."""
	for name in names:
		enqueue_push(name)


def push_retailer(retailer_name: str):
	"""
	Push one Retailer to the store as a customer and record the outcome on it.

	A platform error (``ConnectorError``) marks the retailer Failed with the
	message and does not raise, so the job itself succeeds. Any other error
	also marks it Failed, then re-raises so it shows in the job's error log.
	"""
	conn = get_connector()
	platform = conn.settings.platform

	frappe.db.set_value(
		"Retailer",
		retailer_name,
		{"sync_status": "Pending", "last_sync_attempt": now_datetime()},
		update_modified=False,
	)
	frappe.db.commit()

	doc = frappe.get_doc("Retailer", retailer_name)
	operation = "Update Customer" if doc.external_customer_id else "Create Customer"
	start = time.monotonic()

	try:
		result = conn.create_customer(doc)
	except Exception as e:
		frappe.db.rollback()
		message = e.message if isinstance(e, ConnectorError) else str(e)
		_record_result(
			retailer_name,
			{"sync_status": "Failed", "sync_error": message, "sync_attempts": cint(doc.sync_attempts) + 1},
		)
		log_sync(
			platform=platform,
			operation=operation,
			reference_doctype="Retailer",
			reference_name=retailer_name,
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=getattr(e, "http_status", None),
			error_code=getattr(e, "code", None) or type(e).__name__,
			error_message=message,
		)
		frappe.db.commit()
		if not isinstance(e, ConnectorError):
			raise
		return

	_record_result(
		retailer_name,
		{
			"external_customer_id": result["id"],
			"platform": platform,
			"sync_status": "Synced",
			"last_synced": now_datetime(),
			"sync_attempts": 0,
			# Synced, but with a caveat worth showing (e.g. email left out).
			"sync_error": result.get("warning") or "",
		},
	)
	log_sync(
		platform=platform,
		operation=operation,
		reference_doctype="Retailer",
		reference_name=retailer_name,
		status="Success",
		duration_ms=_elapsed_ms(start),
		external_id=result["id"],
		error_message=result.get("warning"),
	)
	frappe.db.commit()


def _record_result(retailer_name: str, values: dict):
	"""
	Save sync outcome fields on a freshly loaded Retailer.

	Re-loaded so edits made while the store call was running are not
	overwritten; ``ignore_sync`` stops this save from queueing another push.
	"""
	doc = frappe.get_doc("Retailer", retailer_name)
	doc.update(values)
	doc.flags.ignore_sync = True
	doc.save(ignore_permissions=True)
	notify_sync_result("retailer_synced", "Retailer", retailer_name, values.get("sync_status"))
	if values.get("sync_status") == "Failed":
		notify_customer_sync_failure(doc)


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)


# ----------------------------------------------------------------------
# Import: store customers -> Retailer
# ----------------------------------------------------------------------

IMPORT_JOB_ID = "field_sales:customer_import"
# Retailer fields an import fills, from the normalized customer.
IMPORT_FIELDS = ("retailer_name", "owner_name", "phone", "email", "address_line1", "address_line2", "city", "state", "pincode", "country")
# Emails the push invents for retailers without one (connectors/woocommerce.py _placeholder_email).
PLACEHOLDER_EMAIL_DOMAIN = "@customers.fieldsales.local"
# Failures listed on the import's Sync Log.
MAX_LISTED_FAILURES = 20


def enqueue_import():
	"""
	Queue ``import_customers`` on the long queue.

	:return: The queued job, or ``None`` if an import is already queued or running.
	"""
	return frappe.enqueue(
		"field_sales_woocommerce.sync.customers.import_customers",
		queue="long",
		timeout=1500,
		job_id=IMPORT_JOB_ID,
		deduplicate=True,
	)


def import_customers() -> dict:
	"""
	Pull every store customer into Retailer.

	For each customer, the Retailer is found by ``external_customer_id``, else by email
	(a retailer not yet linked to the store), else a new one is created:

	- New: created Synced and linked, with no Sales Rep (a manager assigns one). Missing
	  mandatory details (phone, address, city) are left blank to fill in later, and a phone
	  or email the Retailer rules reject is dropped.
	- Existing: only its empty fields are filled. Field data entered in the app wins, so an
	  import never overwrites it (the push sends it to the store instead).

	Imported saves never push back to the store. Commits after each page, so a failure keeps
	the pages already done; one customer that fails is counted and skipped. Writes one
	"Customer Sync" Sync Log (``reference_doctype`` Retailer) with the summary.

	A customer with the push's placeholder email and no matching Retailer is skipped (its
	retailer was deleted here).

	:return: ``{"created", "linked", "updated", "unchanged", "skipped", "failed"}`` counts.
	"""
	conn = get_connector()
	platform = conn.settings.platform
	summary = {"created": 0, "linked": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
	failures: list[str] = []
	start = time.monotonic()

	try:
		cursor = None
		while True:
			page = conn.get_customers(cursor)
			for customer in page["items"]:
				frappe.db.savepoint("customer_import")
				try:
					summary[_import_customer(platform, customer)] += 1
				except Exception as e:
					frappe.db.rollback(save_point="customer_import")
					summary["failed"] += 1
					if len(failures) < MAX_LISTED_FAILURES:
						failures.append(f"#{customer['external_customer_id']}: {_plain(e)}")
			frappe.db.commit()

			cursor = page["next_cursor"]
			if not cursor:
				break

	except Exception as e:
		frappe.db.rollback()
		log_sync(
			platform=platform,
			operation="Customer Sync",
			reference_doctype="Retailer",
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=getattr(e, "http_status", None),
			error_code=getattr(e, "code", None) or type(e).__name__,
			error_message=_plain(e),
			response_payload=frappe.as_json(summary),
		)
		frappe.db.commit()
		raise

	log_sync(
		platform=platform,
		operation="Customer Sync",
		reference_doctype="Retailer",
		status="Success",
		duration_ms=_elapsed_ms(start),
		error_message="\n".join(failures) or None,
		response_payload=frappe.as_json(summary),
	)
	frappe.db.commit()
	return summary


def _import_customer(platform: str, customer: dict) -> str:
	"""Create, link or fill one Retailer from a normalized customer; returns the summary key."""
	external_id = customer["external_customer_id"]
	name = frappe.db.get_value("Retailer", {"external_customer_id": external_id}, "name")
	if not name and _is_placeholder(customer.get("email")):
		# Pushed from a retailer that has since been deleted here; nothing to import.
		return "skipped"

	values = _retailer_values(customer)
	outcome = "updated"
	if not name and values.get("email"):
		name = frappe.db.get_value(
			"Retailer", {"email": values["email"], "external_customer_id": ("in", ("", None))}, "name"
		)
		outcome = "linked"

	if not name:
		doc = frappe.new_doc("Retailer")
		doc.update(values)
		doc.update({"platform": platform, "external_customer_id": external_id, "status": "Active"})
		_mark_synced(doc)
		_drop_rejected_contact(doc, ("phone", "email"))
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return "created"

	doc = frappe.get_doc("Retailer", name)
	filled = [field for field, value in values.items() if value and not doc.get(field)]
	for field in filled:
		doc.set(field, values[field])
	_drop_rejected_contact(doc, filled)
	filled = [field for field in filled if doc.get(field)]

	if outcome == "linked":
		doc.update({"platform": platform, "external_customer_id": external_id})
		_mark_synced(doc)
	elif not filled:
		return "unchanged"

	doc.flags.ignore_sync = True
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return outcome


def _is_placeholder(email: str | None) -> bool:
	return (email or "").lower().endswith(PLACEHOLDER_EMAIL_DOMAIN)


def _retailer_values(customer: dict) -> dict:
	"""Retailer fields for a normalized customer (only the ones it has)."""
	person = " ".join(p for p in (customer["first_name"], customer["last_name"]) if p).strip()
	email = "" if _is_placeholder(customer.get("email")) else (customer.get("email") or "").lower()
	country = None
	if customer.get("country_code"):
		country = frappe.db.get_value("Country", {"code": customer["country_code"].lower()}, "name")

	values = {
		# A shop is named after its business; a plain person after themselves.
		"retailer_name": customer.get("company") or person or email or _("Customer {0}").format(customer["external_customer_id"]),
		"owner_name": person or None,
		"phone": customer.get("phone"),
		"email": email or None,
		"address_line1": customer.get("address_line1"),
		"address_line2": customer.get("address_line2"),
		"city": customer.get("city"),
		"state": customer.get("state"),
		"pincode": customer.get("pincode"),
		"country": country,
	}
	return {k: v for k, v in values.items() if v}


def _mark_synced(doc) -> None:
	"""Mark a retailer as matching its store customer; ``ignore_sync`` stops a push back."""
	doc.update({"sync_status": "Synced", "last_synced": now_datetime(), "sync_attempts": 0, "sync_error": ""})
	doc.flags.ignore_sync = True


def _drop_rejected_contact(doc, fields) -> None:
	"""
	Clear an imported phone or email (of ``fields``) that the Retailer rules reject, e.g. a
	number in another format, so one bad detail does not keep the whole customer out.
	"""
	if not doc.country and doc.is_new():
		from field_sales_woocommerce.field_sales_woocommerce.doctype.retailer.retailer import get_default_country

		doc.country = get_default_country()
	for field, check in (("phone", doc._validate_phone), ("email", doc._validate_email)):
		if field not in fields or not doc.get(field):
			continue
		try:
			check()
		except frappe.ValidationError:
			doc.set(field, None)
			frappe.clear_last_message()


def _plain(e: Exception) -> str:
	"""An exception's message without HTML, for the Sync Log."""
	return frappe.utils.strip_html(getattr(e, "message", None) or str(e))[:500]
