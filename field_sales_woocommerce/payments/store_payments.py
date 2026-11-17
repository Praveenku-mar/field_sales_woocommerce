# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
A payment the customer made on the store itself (its checkout, its pay page, a payment the
merchant recorded there) marks the Field Order paid. The same file runs in every edition; each
connector's ``get_store_payment`` says what the store counts as paid:

- Shopify: the order's financial status is paid / partially paid (amount received).
- WooCommerce: a paid date with a real payment method (not an Agamsale order merely confirmed,
  not Agamsale's own Razorpay gateway, cash on delivery only once completed).
- Odoo: what is paid on the order's posted invoices, else a done payment transaction.

Called from the store's order events (webhooks on Shopify / WooCommerce, polling on Odoo).
An order Agamsale already shows as paid (Razorpay link, manual payment, the WooCommerce
gateway) is left alone, so nothing is counted twice.
"""

from __future__ import annotations

from datetime import timezone

import frappe
from frappe import _
from frappe.utils import convert_utc_to_system_timezone, escape_html, flt, fmt_money, get_datetime, now_datetime

from field_sales_woocommerce.payments import transactions
from field_sales_woocommerce.payments.razorpay import (
	OPEN_LINK_STATES,
	PAID_STATES,
	PAYMENT_EVENT,
	RazorpayError,
	_payment_fields,
	cancel_payment_link,
	fetch_payment_link,
	get_settings,
	notify_payment_received,
)


def apply_store_payment(conn, external_order_id: str) -> dict:
	"""
	Read the store order's payment and mark its Field Order Paid / Partially Paid. Safe to repeat.

	:return: ``{"note": ...}`` when nothing changed, else ``{"field_order", "payment_status", "amount_paid"}``
	"""
	external_order_id = str(external_order_id or "")
	name = frappe.db.get_value("Field Order", {"external_order_id": external_order_id, "docstatus": 1}, "name")
	if not name:
		return {"note": "Ignored: not a submitted Field Sales order"}
	if frappe.db.get_value("Field Order", name, "payment_status") in PAID_STATES:
		return {"note": "Ignored: already paid in Agamsale"}

	payment = conn.get_store_payment(external_order_id)
	if not payment or flt(payment.get("amount")) <= 0:
		return {"note": "Ignored: not paid on the store"}

	doc = frappe.get_doc("Field Order", name, for_update=True)
	if doc.payment_status in PAID_STATES:
		return {"note": "Ignored: already paid in Agamsale"}
	amount = min(flt(payment["amount"], 2), flt(doc.grand_total, 2))
	if amount <= flt(doc.amount_paid):
		return {"note": "Ignored: nothing new"}

	if doc.payment_status == "Link Sent" and doc.razorpay_payment_link_id:
		if not _retire_open_link(doc):
			# The link itself took money: its own webhook records that payment instead.
			return {"note": "Ignored: the payment link is already paid"}

	paid_in_full = amount >= flt(doc.grand_total) - 0.005
	values = {
		"payment_status": "Paid" if paid_in_full else "Partially Paid",
		"amount_paid": amount,
		"paid_on": _when(payment.get("paid_at")),
		# Paid on the store: there is nothing to mark paid there.
		"store_marked_paid_on": now_datetime(),
	}
	doc.db_set(values, update_modified=False)
	transactions.record(
		f"store:{external_order_id}",
		transaction_type="Payment",
		source="Store Checkout",
		status="Captured",
		amount=amount,
		currency=payment.get("currency"),
		method=payment.get("method"),
		reference=payment.get("reference"),
		reference_order=doc.name,
		store_order_id=external_order_id,
		occurred_on=values["paid_on"],
	)
	doc.add_comment(
		"Info",
		_("Paid on the store: {0}{1}.").format(
			fmt_money(amount, currency=payment.get("currency")),
			f" ({escape_html(payment['method'])})" if payment.get("method") else "",
		),
	)
	notify_payment_received(doc)
	frappe.publish_realtime(PAYMENT_EVENT, _payment_fields(doc), doctype="Field Order", docname=doc.name, after_commit=True)
	return {"field_order": doc.name, "payment_status": values["payment_status"], "amount_paid": amount}


def _retire_open_link(doc) -> bool:
	"""Cancel the order's open Razorpay link so the customer cannot pay twice; False if it was paid."""
	try:
		settings = get_settings()
		link = fetch_payment_link(doc.razorpay_payment_link_id, settings=settings)
		if link.get("status") == "paid" or frappe.utils.cint(link.get("amount_paid")) > 0:
			return False
		if link.get("status") in OPEN_LINK_STATES:
			cancel_payment_link(doc.razorpay_payment_link_id, field_order=doc.name, settings=settings)
	except RazorpayError as e:
		# The store payment still counts; the link stays open on Razorpay, said on the timeline.
		doc.add_comment(
			"Info",
			_("Payment link {0} could not be cancelled on Razorpay: {1}. Cancel it in the Razorpay Dashboard.").format(
				escape_html(doc.razorpay_payment_link_id or ""), escape_html(e.message or "")
			),
		)
	return True


def _when(value):
	"""The store's payment time (UTC, with or without a zone) in the system time zone, naive."""
	if not value:
		return now_datetime()
	try:
		moment = get_datetime(str(value).replace("Z", "+00:00"))
	except Exception:
		return now_datetime()
	if moment.tzinfo is None:
		moment = moment.replace(tzinfo=timezone.utc)
	return convert_utc_to_system_timezone(moment).replace(tzinfo=None)
