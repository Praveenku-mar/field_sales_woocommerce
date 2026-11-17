# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Payment history: one ``Payment Transaction`` row per Razorpay object (payment link, checkout
order, payment, refund) or manual payment, keyed by ``transaction_key`` so a repeated webhook,
refresh or retry updates the row instead of adding another.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, now_datetime

from field_sales_woocommerce.utils.money import site_currency

# A row in one of these states keeps it: a late "created" or "failed" must not undo money in.
FINAL_STATES = {"Captured", "Paid", "Processed"}

FIELDS = (
	"transaction_type",
	"status",
	"source",
	"amount",
	"currency",
	"occurred_on",
	"reference_order",
	"store_order_id",
	"method",
	"reference",
	"razorpay_payment_link_id",
	"razorpay_order_id",
	"razorpay_payment_id",
	"razorpay_refund_id",
	"error",
)


def record(key: str, **values) -> str:
	"""
	Create or update the history row for ``key`` (e.g. ``payment:pay_X``); returns its name.

	Empty values never blank a stored one, and a row in a final state (Captured / Paid /
	Processed) keeps its status. Safe to call again for the same event.
	"""
	values = {k: v for k, v in values.items() if k in FIELDS and v not in (None, "")}
	name = frappe.db.get_value("Payment Transaction", {"transaction_key": key}, "name", for_update=True)
	if name:
		current = frappe.db.get_value("Payment Transaction", name, ["status"], as_dict=True)
		if current.status in FINAL_STATES and values.get("status") not in (None, current.status):
			values.pop("status")
		if values:
			frappe.db.set_value("Payment Transaction", name, values, update_modified=True)
		return name

	doc = frappe.new_doc("Payment Transaction")
	doc.update({"currency": site_currency(), "occurred_on": now_datetime(), **values, "transaction_key": key})
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# Another worker wrote the same key first: update that row instead.
		if not frappe.db.exists("Payment Transaction", {"transaction_key": key}):
			raise
		return record(key, **values)
	return doc.name


def history(field_order: str | None = None, store_order_id: str | None = None) -> list[dict]:
	"""An order's payment history, newest first (for the order screens and the APIs)."""
	filters = []
	if field_order:
		filters.append(["reference_order", "=", field_order])
	if store_order_id:
		filters.append(["store_order_id", "=", str(store_order_id)])
	if not filters:
		return []
	or_filters = filters if len(filters) > 1 else None
	return frappe.get_all(
		"Payment Transaction",
		filters=None if or_filters else filters,
		or_filters=or_filters,
		fields=[
			"name",
			"transaction_type",
			"status",
			"source",
			"amount",
			"currency",
			"occurred_on",
			"method",
			"reference",
			"razorpay_payment_link_id",
			"razorpay_order_id",
			"razorpay_payment_id",
			"razorpay_refund_id",
			"error",
		],
		order_by="occurred_on desc, creation desc",
		limit=100,
	)


def paise(amount) -> int:
	"""Rupees (or the currency's unit) to Razorpay's smallest unit."""
	return round(flt(amount) * 100)


def rupees(paise_amount) -> float:
	return cint(paise_amount) / 100
