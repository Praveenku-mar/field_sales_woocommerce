# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Refunds of Razorpay payments taken for Field Orders, and Razorpay's refund / failed-payment
events. The same file runs in every Field Sales edition.

- ``refund_order_payment``: a manager's Refund button on the order page (web app).
- ``refund_payment``: the refund itself, also used by the WooCommerce plugin's refunds
  (payments/checkout.py there). Idempotent per ``request_id``: the same refund sent twice
  (a timeout, a double click) reaches Razorpay once. Never more than what is left on the
  payment.
- ``apply_refund_to_order``: Field Order Amount Refunded and Partially Refunded / Refunded,
  from the refund history (Payment Transaction).
- ``handle_webhook_event``: ``refund.processed`` / ``refund.failed`` (including refunds made in
  the Razorpay Dashboard) and ``payment.failed`` on a payment link (history only).
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money

from field_sales_woocommerce.payments import transactions
from field_sales_woocommerce.payments.razorpay import (
	PAYMENT_EVENT,
	RazorpayError,
	_from_unix,
	_logged,
	_payment_fields,
	get_settings,
	request,
)
from field_sales_woocommerce.utils.money import site_currency

REFUND_EVENTS = ("refund.processed", "refund.failed")
WEBHOOK_EVENTS = (*REFUND_EVENTS, "payment.failed")
REFUND_ROLES = ("System Manager", "Sales Manager")
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


class RefundError(Exception):
	"""A refused refund; ``code`` says why (BAD_REQUEST, BAD_AMOUNT, NO_RAZORPAY_PAYMENT, AMOUNT_TOO_HIGH)."""

	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code
		self.message = message


@frappe.whitelist(methods=["POST"])
def refund_order_payment(field_order: str, amount: float, request_id: str, reason: str | None = None) -> dict:
	"""
	Refund part or all of a Field Order's Razorpay payment (Sales / System Managers).

	:param request_id: a random id the page makes once per refund it shows, so a retry of the
		same click is not refunded twice.
	:return: ``{"refund_id", "status": "processed" | "pending", "amount", ...payment fields}``
	"""
	frappe.only_for(REFUND_ROLES)
	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("write")
	if not cint(get_settings().enabled):
		frappe.throw(_("Razorpay is turned off"), title=_("Refund not made"))
	try:
		result = refund_payment(
			amount=amount,
			request_id=request_id,
			reason=reason,
			field_order=doc.name,
			store_order_id=doc.external_order_id,
		)
	except RefundError as e:
		frappe.throw(e.message, title=_("Refund not made"))
	except RazorpayError as e:
		frappe.throw(_("Razorpay: {0}").format(e.message), title=_("Refund not made"))
	doc.reload()
	return {**result, **_payment_fields(doc)}


def refund_payment(
	*,
	amount,
	request_id: str,
	reason: str | None = None,
	field_order: str | None = None,
	store_order_id: str | None = None,
) -> dict:
	"""
	Refund ``amount`` of the order's captured Razorpay payment (the newest with enough left).

	:raises RefundError: bad request, nothing to refund, or more than is left.
	:raises RazorpayError: Razorpay refused or could not be reached.
	"""
	request_id = str(request_id or "").strip()
	if not REQUEST_ID_RE.fullmatch(request_id):
		raise RefundError("BAD_REQUEST", _("A refund needs a request id"))
	amount = flt(amount, 2)
	if amount <= 0:
		raise RefundError("BAD_AMOUNT", _("The refund amount must be more than zero"))
	reason = str(reason or "")[:200]

	done = frappe.db.get_value(
		"Payment Transaction",
		{"transaction_key": f"refundreq:{request_id}"},
		["razorpay_refund_id", "status", "amount"],
		as_dict=True,
	)
	if done:
		return {"refund_id": done.razorpay_refund_id, "status": done.status.lower(), "amount": done.amount}

	payment = refundable_payment(store_order_id, field_order, amount)
	payload = {
		"amount": transactions.paise(amount),
		"receipt": request_id[:40],
		"notes": {"store_order_id": store_order_id or "", "reason": reason, "field_order": field_order or ""},
	}
	refund = _logged(
		"Refund Payment",
		field_order,
		payload,
		lambda: request("POST", f"/payments/{payment.razorpay_payment_id}/refund", json=payload),
	)
	status = "Processed" if refund.get("status") == "processed" else "Pending"
	transactions.record(
		f"refundreq:{request_id}",
		transaction_type="Refund",
		source=payment.source,
		status=status,
		amount=amount,
		currency=payment.currency,
		store_order_id=store_order_id,
		reference_order=field_order,
		razorpay_payment_id=payment.razorpay_payment_id,
		razorpay_refund_id=refund.get("id"),
		reference=reason,
	)
	if field_order:
		apply_refund_to_order(field_order)
	return {"refund_id": refund.get("id"), "status": status.lower(), "amount": amount}


def refundable_payment(store_order_id: str | None, field_order: str | None, amount: float):
	"""The captured Razorpay payment with enough left to refund (newest first)."""
	or_filters = {}
	if store_order_id:
		or_filters["store_order_id"] = store_order_id
	if field_order:
		or_filters["reference_order"] = field_order
	if not or_filters:
		raise RefundError("NO_RAZORPAY_PAYMENT", _("This order has no Razorpay payment to refund"))
	payments = frappe.get_all(
		"Payment Transaction",
		filters={"transaction_type": "Payment", "status": "Captured", "razorpay_payment_id": ("is", "set")},
		or_filters=or_filters,
		fields=["razorpay_payment_id", "amount", "currency", "source"],
		order_by="occurred_on desc",
	)
	payments = [p for p in payments if p.source in ("Store Checkout", "Payment Link")]
	if not payments:
		raise RefundError("NO_RAZORPAY_PAYMENT", _("This order has no Razorpay payment to refund"))
	for payment in payments:
		if flt(payment.amount) - refunded_on(payment.razorpay_payment_id) + 0.005 >= amount:
			return payment
	raise RefundError("AMOUNT_TOO_HIGH", _("The refund is more than what is left to refund on the Razorpay payment"))


def refunded_on(razorpay_payment_id: str) -> float:
	"""Refunded so far on one payment (pending and processed refunds)."""
	return sum(
		flt(r.amount)
		for r in frappe.get_all(
			"Payment Transaction",
			filters={
				"transaction_type": "Refund",
				"razorpay_payment_id": razorpay_payment_id,
				"status": ("in", ("Pending", "Processed")),
			},
			fields=["amount"],
		)
	)


def refundable_amount(field_order: str, store_order_id: str | None = None) -> float:
	"""What can still be refunded on the order's Razorpay payments (for the Refund button)."""
	or_filters = {"reference_order": field_order}
	if store_order_id:
		or_filters["store_order_id"] = store_order_id
	payments = frappe.get_all(
		"Payment Transaction",
		filters={"transaction_type": "Payment", "status": "Captured", "razorpay_payment_id": ("is", "set")},
		or_filters=or_filters,
		fields=["razorpay_payment_id", "amount", "source"],
	)
	return round(
		sum(
			max(0.0, flt(p.amount) - refunded_on(p.razorpay_payment_id))
			for p in payments
			if p.source in ("Store Checkout", "Payment Link")
		),
		2,
	)


def apply_refund_to_order(field_order: str) -> None:
	"""Field Order Amount Refunded and status from its refund history (Pending + Processed)."""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	refunded = sum(
		flt(r.amount)
		for r in frappe.get_all(
			"Payment Transaction",
			filters={"reference_order": field_order, "transaction_type": "Refund", "status": ("in", ("Pending", "Processed"))},
			fields=["amount"],
		)
	)
	values = {"amount_refunded": refunded}
	if refunded > 0 and flt(doc.amount_paid) > 0:
		values["payment_status"] = "Refunded" if refunded >= flt(doc.amount_paid) else "Partially Refunded"
	elif refunded <= 0 and doc.payment_status in ("Refunded", "Partially Refunded"):
		values["payment_status"] = "Paid"
	changed = {k: v for k, v in values.items() if doc.get(k) != v}
	if not changed:
		return
	doc.db_set(changed, update_modified=False)
	doc.add_comment(
		"Info",
		_("Refunded {0} of {1}. Payment status: {2}.").format(
			fmt_money(refunded, currency=site_currency()),
			fmt_money(doc.amount_paid, currency=site_currency()),
			_(doc.payment_status),
		),
	)
	frappe.publish_realtime(PAYMENT_EVENT, _payment_fields(doc), doctype="Field Order", docname=doc.name, after_commit=True)


# ----------------------------------------------------------------------
# Webhook events (from payments/razorpay.py -> webhook)
# ----------------------------------------------------------------------


def handle_webhook_event(event: str, payload: dict, record: dict) -> tuple[str, str | None]:
	""":return: ``(Webhook Event status, note)``."""
	if event in REFUND_EVENTS:
		return _webhook_refund(event, (payload.get("refund") or {}).get("entity") or {}, record)
	if event == "payment.failed":
		return webhook_link_payment_failed((payload.get("payment") or {}).get("entity") or {}, record)
	return "Ignored", "not a refund or failed payment"


def webhook_link_payment_failed(payment: dict, record: dict | None = None) -> tuple[str, str | None]:
	"""A failed attempt on a payment link: history only (the link stays payable)."""
	notes = payment.get("notes") if isinstance(payment.get("notes"), dict) else {}
	field_order = notes.get("field_order")
	if not field_order or not frappe.db.exists("Field Order", field_order) or not payment.get("id"):
		return "Ignored", "payment is not for a known order"
	if record is not None:
		record["reference_order"] = field_order
	transactions.record(
		f"payment:{payment['id']}",
		transaction_type="Payment",
		source="Payment Link",
		status="Failed",
		amount=cint(payment.get("amount")) / 100,
		currency=payment.get("currency"),
		method=payment.get("method"),
		reference_order=field_order,
		store_order_id=frappe.db.get_value("Field Order", field_order, "external_order_id"),
		razorpay_order_id=payment.get("order_id"),
		razorpay_payment_id=payment["id"],
		error=payment.get("error_description"),
		occurred_on=_from_unix(payment.get("created_at")),
	)
	return "Processed", None


def _webhook_refund(event: str, refund: dict, record: dict | None = None) -> tuple[str, str | None]:
	refund_id = refund.get("id")
	status = "Processed" if event == "refund.processed" else "Failed"
	row = refund_id and frappe.db.get_value(
		"Payment Transaction", {"razorpay_refund_id": refund_id}, ["name", "reference_order", "status"], as_dict=True
	)
	if row:
		if record is not None:
			record["reference_order"] = row.reference_order
		if row.status != status:
			frappe.db.set_value("Payment Transaction", row.name, "status", status)
			if row.reference_order:
				apply_refund_to_order(row.reference_order)
		return "Processed", None

	# Refunded in the Razorpay Dashboard: record it against the payment if it is ours.
	payment = frappe.db.get_value(
		"Payment Transaction",
		{"razorpay_payment_id": refund.get("payment_id"), "transaction_type": "Payment"},
		["reference_order", "store_order_id", "source", "currency"],
		as_dict=True,
	)
	if not payment:
		return "Ignored", "refund for a payment this app does not know"
	if record is not None:
		record["reference_order"] = payment.reference_order
	transactions.record(
		f"refund:{refund_id}",
		transaction_type="Refund",
		source=payment.source,
		status=status,
		amount=cint(refund.get("amount")) / 100,
		currency=refund.get("currency") or payment.currency,
		reference_order=payment.reference_order,
		store_order_id=payment.store_order_id,
		razorpay_payment_id=refund.get("payment_id"),
		razorpay_refund_id=refund_id,
		reference=_("Refunded on Razorpay"),
	)
	if payment.reference_order:
		apply_refund_to_order(payment.reference_order)
	return "Processed", None
