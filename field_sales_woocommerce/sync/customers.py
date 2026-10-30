# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral customer sync: Retailer -> store customer.

Talks to the store only through ``get_connector()``; the connector decides
whether a push is a create or an update.
"""

import time

import frappe
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
