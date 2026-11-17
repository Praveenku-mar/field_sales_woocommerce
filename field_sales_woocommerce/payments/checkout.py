# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Razorpay on the WooCommerce checkout (the Agamsale plugin's "Razorpay" payment gateway).

WordPress holds no Razorpay secret. The plugin calls these endpoints, signed with the secret it
shares with Store Connection (``wp_shared_secret``, see ``wordpress_embed.py``):

- ``ping``: connection check and status for the plugin's settings page.
- ``test_razorpay``: checks the Razorpay keys saved in Agamsale.
- ``create_checkout_order``: a Razorpay order for a WooCommerce order. The amount and currency
  are read from WooCommerce here, never taken from the request.
- ``verify_checkout_payment``: checks Razorpay's checkout signature, then the payment itself
  (order, amount, currency, captured) before anything is marked paid.
- ``refund_checkout_payment``: a WooCommerce refund, sent to Razorpay.

Razorpay's webhook (``payments/razorpay.py -> webhook``) passes ``payment.*`` and ``refund.*``
events here, so a payment is recorded even if the customer closes the browser before returning
to the store. Everything lands in the payment history (``Payment Transaction``).

Request signing: ``X-Agamsale-Timestamp`` (unix seconds), ``X-Agamsale-Nonce`` (random, used
once) and ``X-Agamsale-Signature`` = hex HMAC-SHA256 of ``"{timestamp}\\n{nonce}\\n{path}\\n{body}"``
with the shared secret. Requests older than 5 minutes, or with a nonce seen before, are refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, now_datetime

from field_sales_woocommerce.payments import refunds, transactions
from field_sales_woocommerce.payments.razorpay import (
	PAYMENT_EVENT,
	RazorpayError,
	_from_unix,
	_logged,
	_payment_fields,
	get_settings,
	notify_payment_received,
	request,
	webhook_url,
)

SIGNATURE_WINDOW_SECONDS = 300
NONCE_TTL_SECONDS = 15 * 60
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
GATEWAY_ID = "agamsale_razorpay"  # the plugin's WC_Payment_Gateway id
# WooCommerce statuses an order can still be paid in.
PAYABLE_STATUSES = {"pending", "failed", "on-hold"}
CHECKOUT_EVENTS = ("payment.captured", "payment.failed", "order.paid", "refund.processed", "refund.failed")
RAZORPAY_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")


class CheckoutError(Exception):
	"""A refused checkout call: ``http_status`` and ``code`` go back to the plugin."""

	def __init__(self, http_status: int, code: str, message: str):
		super().__init__(message)
		self.http_status = http_status
		self.code = code
		self.message = message


# ----------------------------------------------------------------------
# Endpoints (called by the WordPress plugin, signed)
# ----------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ping() -> dict:
	"""Whether the plugin can reach Agamsale, and how Razorpay is set up (no secrets)."""
	return _endpoint(_ping)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def test_razorpay() -> dict:
	return _endpoint(_test_razorpay)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def create_checkout_order() -> dict:
	return _endpoint(_create_checkout_order, needs_razorpay=True)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_checkout_payment() -> dict:
	return _endpoint(_verify_checkout_payment, needs_razorpay=True)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def refund_checkout_payment() -> dict:
	return _endpoint(_refund_checkout_payment, needs_razorpay=True)


def _endpoint(handler, needs_razorpay: bool = False) -> dict:
	"""Verify the plugin's signature, run ``handler(body)`` as Administrator, shape errors."""
	try:
		body = _verified_body()
		if needs_razorpay and not cint(get_settings().enabled):
			raise CheckoutError(409, "RAZORPAY_DISABLED", _("Razorpay is turned off in Agamsale"))
		user = frappe.session.user
		frappe.set_user("Administrator")
		try:
			result = handler(body)
		finally:
			frappe.set_user(user)
		return {"ok": True, **result}
	# No rollback: what was written before a refusal is meant to stay (a Failed payment in
	# the history, an outdated Razorpay order marked Cancelled).
	except CheckoutError as e:
		frappe.local.response["http_status_code"] = e.http_status
		return {"ok": False, "code": e.code, "message": e.message}
	except RazorpayError as e:
		frappe.local.response["http_status_code"] = 502
		return {"ok": False, "code": e.code or "RAZORPAY_ERROR", "message": _("Razorpay: {0}").format(e.message)}


def _verified_body() -> dict:
	from field_sales_woocommerce.wordpress_embed import shared_secret

	secret = shared_secret()
	if not secret:
		raise CheckoutError(503, "NOT_CONNECTED", _("Set the WordPress plugin secret on Agamsale's Store Connection"))
	if (frappe.db.get_single_value("Store Connection", "platform") or "") != "WooCommerce":
		raise CheckoutError(409, "NOT_WOOCOMMERCE", _("Agamsale is not connected to a WooCommerce store"))

	headers = frappe.request.headers
	raw = frappe.request.get_data() or b""
	timestamp = (headers.get("X-Agamsale-Timestamp") or "").strip()
	nonce = (headers.get("X-Agamsale-Nonce") or "").strip()
	signature = (headers.get("X-Agamsale-Signature") or "").strip()
	if not (timestamp.isdigit() and NONCE_RE.fullmatch(nonce) and signature):
		raise CheckoutError(401, "UNSIGNED", _("The request is not signed by the Agamsale plugin"))
	if abs(time.time() - int(timestamp)) > SIGNATURE_WINDOW_SECONDS:
		raise CheckoutError(401, "EXPIRED", _("The request is too old; check the WordPress server's clock"))
	message = b"\n".join([timestamp.encode(), nonce.encode(), frappe.request.path.encode(), raw])
	expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected, signature):
		raise CheckoutError(401, "BAD_SIGNATURE", _("The request signature does not match the plugin secret"))
	# A nonce works once (a captured request cannot be replayed within the window).
	key = frappe.cache.make_key(f"agamsale:wp-nonce:{nonce}")
	if not frappe.cache.set(key, 1, ex=NONCE_TTL_SECONDS, nx=True):
		raise CheckoutError(401, "REPLAYED", _("This request was already received"))

	try:
		body = json.loads(raw or b"{}")
	except ValueError:
		body = None
	if not isinstance(body, dict):
		raise CheckoutError(400, "BAD_REQUEST", _("The request body must be a JSON object"))
	return body


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


def _ping(body: dict) -> dict:
	settings = get_settings()
	last = frappe.db.get_value(
		"Razorpay Webhook Event", {"status": "Processed"}, "creation", order_by="creation desc"
	)
	return {
		"store": frappe.db.get_single_value("Store Connection", "store_url"),
		"razorpay": {
			"enabled": bool(cint(settings.enabled)),
			"mode": settings.mode or "Test",
			"keys_set": bool((settings.key_id or "").strip() and settings.get_password("key_secret", raise_exception=False)),
			"webhook_secret_set": bool(settings.get_password("webhook_secret", raise_exception=False)),
			"webhook_url": webhook_url(),
			"webhook_events": ["payment_link.paid", "payment_link.partially_paid", "payment_link.expired", "payment_link.cancelled", *CHECKOUT_EVENTS],
			"last_webhook_at": str(last) if last else None,
		},
	}


def _test_razorpay(body: dict) -> dict:
	settings = get_settings()
	request("GET", "/orders", params={"count": 1}, settings=settings)
	return {"message": _("Connected to Razorpay ({0} mode)").format(settings.mode or "Test")}


def _create_checkout_order(body: dict) -> dict:
	store_order_id = _store_order_id(body)
	order = _payable_order(store_order_id)
	amount = transactions.paise(order["total"])
	currency = order["currency"]
	field_order = _field_order_for(store_order_id)

	# A reload, a retry after a cancelled popup or a failed payment reuses the open Razorpay
	# order: Razorpay accepts new attempts on it until one succeeds.
	open_rows = frappe.get_all(
		"Payment Transaction",
		filters={"transaction_type": "Checkout Order", "store_order_id": store_order_id, "status": "Created"},
		fields=["name", "razorpay_order_id", "amount", "currency"],
		order_by="creation desc",
	)
	reuse = None
	for row in open_rows:
		if reuse is None and transactions.paise(row.amount) == amount and row.currency == currency:
			reuse = row
		else:
			# The WooCommerce order changed since (amount or currency): never use that one again.
			frappe.db.set_value("Payment Transaction", row.name, "status", "Cancelled")
	if reuse:
		razorpay_order_id = reuse.razorpay_order_id
	else:
		attempt = frappe.db.count(
			"Payment Transaction", {"transaction_type": "Checkout Order", "store_order_id": store_order_id}
		)
		payload = {
			"amount": amount,
			"currency": currency,
			"receipt": f"woo-{store_order_id}-{attempt + 1}"[:40],
			"notes": {
				"store_order_id": store_order_id,
				"store_order_number": str(order.get("number") or store_order_id),
				"field_order": field_order or "",
				"source": "woocommerce-checkout",
			},
		}
		created = _logged(
			"Create Checkout Order", field_order, payload, lambda: request("POST", "/orders", json=payload)
		)
		razorpay_order_id = created["id"]
		transactions.record(
			f"rzorder:{razorpay_order_id}",
			transaction_type="Checkout Order",
			source="Store Checkout",
			status="Created",
			amount=amount / 100,
			currency=currency,
			store_order_id=store_order_id,
			reference_order=field_order,
			razorpay_order_id=razorpay_order_id,
			reference=f"#{order.get('number') or store_order_id}",
		)

	settings = get_settings()
	billing = order.get("billing") or {}
	return {
		"key_id": (settings.key_id or "").strip(),
		"razorpay_order_id": razorpay_order_id,
		"amount": amount,
		"currency": currency,
		"order_number": str(order.get("number") or store_order_id),
		"prefill": {
			"name": " ".join(filter(None, [billing.get("first_name"), billing.get("last_name")])),
			"email": billing.get("email") or "",
			"contact": billing.get("phone") or "",
		},
	}


def _verify_checkout_payment(body: dict) -> dict:
	store_order_id = _store_order_id(body)
	razorpay_order_id = _razorpay_id(body, "razorpay_order_id")
	payment_id = _razorpay_id(body, "razorpay_payment_id")
	signature = str(body.get("razorpay_signature") or "").strip()

	row = _checkout_row(razorpay_order_id)
	if not row or row.store_order_id != store_order_id:
		raise CheckoutError(400, "ORDER_MISMATCH", _("This payment is not for WooCommerce order {0}").format(store_order_id))

	settings = get_settings()
	secret = settings.get_password("key_secret", raise_exception=False) or ""
	expected = hmac.new(secret.encode(), f"{razorpay_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
	if not signature or not hmac.compare_digest(expected, signature):
		raise CheckoutError(400, "INVALID_SIGNATURE", _("The payment signature from Razorpay is not valid"))

	# The signature proves Razorpay issued this pair; the payment itself says whether money came in.
	payment = request("GET", f"/payments/{payment_id}", settings=settings)
	if payment.get("status") == "authorized":
		payment = _logged(
			"Capture Payment",
			row.reference_order,
			None,
			lambda: request(
				"POST",
				f"/payments/{payment_id}/capture",
				json={"amount": payment.get("amount"), "currency": payment.get("currency")},
				settings=settings,
			),
		)
	result = apply_checkout_payment(row, payment)
	if result["status"] != "paid":
		raise CheckoutError(402, "NOT_PAID", result["message"])
	return result


def _refund_checkout_payment(body: dict) -> dict:
	# The refund itself (idempotent per refund_request_id) is payments/refunds.py's, shared with
	# the Refund button on the order page.
	store_order_id = _store_order_id(body)
	try:
		return refunds.refund_payment(
			amount=body.get("amount"),
			request_id=body.get("refund_request_id"),
			reason=body.get("reason"),
			field_order=_field_order_for(store_order_id),
			store_order_id=store_order_id,
		)
	except refunds.RefundError as e:
		raise CheckoutError(400 if e.code in ("BAD_REQUEST", "BAD_AMOUNT") else 409, e.code, e.message) from e


# ----------------------------------------------------------------------
# Payment application (verify call and webhook)
# ----------------------------------------------------------------------


def apply_checkout_payment(row, payment: dict) -> dict:
	"""
	Record a Razorpay payment on a checkout order after checking it belongs to it: same
	Razorpay order, same amount and currency, captured. Safe to repeat.

	:return: ``{"status": "paid" | "failed" | "pending", "payment_id", "message"}``
	"""
	payment_id = payment.get("id")
	problem = None
	if payment.get("order_id") != row.razorpay_order_id:
		problem = _("The payment is for another Razorpay order")
	elif cint(payment.get("amount")) != transactions.paise(row.amount):
		problem = _("Paid {0} but the order is {1}").format(cint(payment.get("amount")) / 100, row.amount)
	elif (payment.get("currency") or "") != (row.currency or ""):
		problem = _("Paid in {0} but the order is in {1}").format(payment.get("currency"), row.currency)
	if problem:
		transactions.record(
			f"payment:{payment_id}",
			transaction_type="Payment",
			source="Store Checkout",
			status="Failed",
			store_order_id=row.store_order_id,
			reference_order=row.reference_order,
			razorpay_order_id=row.razorpay_order_id,
			razorpay_payment_id=payment_id,
			amount=cint(payment.get("amount")) / 100,
			currency=payment.get("currency"),
			error=problem,
		)
		frappe.log_error(title=f"Razorpay checkout payment refused: {payment_id}", message=problem)
		return {"status": "failed", "payment_id": payment_id, "message": problem}

	status = payment.get("status")
	history_status = {"captured": "Captured", "failed": "Failed", "authorized": "Authorized"}.get(status, "Pending")
	transactions.record(
		f"payment:{payment_id}",
		transaction_type="Payment",
		source="Store Checkout",
		status=history_status,
		amount=cint(payment.get("amount")) / 100,
		currency=payment.get("currency"),
		method=payment.get("method"),
		store_order_id=row.store_order_id,
		reference_order=row.reference_order,
		razorpay_order_id=row.razorpay_order_id,
		razorpay_payment_id=payment_id,
		error=payment.get("error_description"),
		occurred_on=_from_unix(payment.get("created_at")),
	)
	if status != "captured":
		message = payment.get("error_description") or _("The payment is {0}").format(status or _("not complete"))
		return {"status": "failed" if status == "failed" else "pending", "payment_id": payment_id, "message": message}

	frappe.db.set_value("Payment Transaction", row.name, "status", "Paid")
	if row.reference_order:
		_mark_field_order_paid(row.reference_order, payment)
	return {"status": "paid", "payment_id": payment_id, "message": _("Payment received")}


def _mark_field_order_paid(field_order: str, payment: dict) -> None:
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	if doc.payment_status in ("Paid", "Partially Refunded", "Refunded") or doc.docstatus != 1:
		return
	paid = cint(payment.get("amount")) / 100
	doc.db_set(
		{
			"payment_status": "Paid" if paid >= flt(doc.grand_total) else "Partially Paid",
			"amount_paid": paid,
			"paid_on": _from_unix(payment.get("created_at")) or now_datetime(),
			"razorpay_payment_id": payment.get("id"),
			# Paid on the store's own checkout: the store order is paid there already.
			"store_marked_paid_on": now_datetime(),
		},
		update_modified=False,
	)
	doc.add_comment(
		"Info",
		_("Paid on the store checkout with Razorpay: {0} ({1}).").format(
			fmt_money(paid, currency=payment.get("currency")), frappe.utils.escape_html(payment.get("id") or "")
		),
	)
	notify_payment_received(doc)
	frappe.publish_realtime(PAYMENT_EVENT, _payment_fields(doc), doctype="Field Order", docname=doc.name, after_commit=True)


# ----------------------------------------------------------------------
# Webhook events (from payments/razorpay.py -> webhook)
# ----------------------------------------------------------------------


def handle_webhook_event(event: str, payload: dict, record: dict) -> tuple[str, str | None]:
	"""
	Apply a ``payment.*`` / ``order.paid`` / ``refund.*`` event.

	:return: ``(Webhook Event status, note)``: "Processed", or "Ignored" for events about
		payments this app did not start (e.g. payment links, handled by their own events).
	"""
	payment = (payload.get("payment") or {}).get("entity") or {}
	if event in refunds.REFUND_EVENTS:
		return refunds.handle_webhook_event(event, payload, record)

	row = _checkout_row(payment.get("order_id")) if payment.get("order_id") else None
	if not row:
		if event == "payment.failed":
			return refunds.webhook_link_payment_failed(payment, record)
		return "Ignored", "not a store checkout payment"
	record["reference_order"] = row.reference_order
	if event == "payment.failed":
		apply_checkout_payment(row, {**payment, "status": "failed"})
		return "Processed", None

	result = apply_checkout_payment(row, payment)
	if result["status"] == "paid":
		enqueue_mark_checkout_paid(row.store_order_id, payment.get("id"))
	return "Processed", None if result["status"] == "paid" else result["message"]


# ----------------------------------------------------------------------
# Store side (a payment the customer finished but never returned to the store for)
# ----------------------------------------------------------------------


def enqueue_mark_checkout_paid(store_order_id: str, payment_id: str | None) -> None:
	frappe.enqueue(
		"field_sales_woocommerce.payments.checkout.mark_checkout_paid",
		queue="short",
		store_order_id=store_order_id,
		payment_id=payment_id,
		job_id=f"field_sales:checkout_paid:{store_order_id}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def mark_checkout_paid(store_order_id: str, payment_id: str | None) -> None:
	"""Mark the WooCommerce order paid with the Razorpay payment, unless it already is."""
	from field_sales_woocommerce.connectors import get_connector

	get_connector().mark_checkout_paid(store_order_id, payment_id, GATEWAY_ID)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _store_order_id(body: dict) -> str:
	value = str(body.get("store_order_id") or "").strip()
	if not value.isdigit():
		raise CheckoutError(400, "BAD_REQUEST", _("store_order_id must be a WooCommerce order number"))
	return value


def _razorpay_id(body: dict, key: str) -> str:
	value = str(body.get(key) or "").strip()
	if not RAZORPAY_ID_RE.fullmatch(value):
		raise CheckoutError(400, "BAD_REQUEST", _("{0} is missing or invalid").format(key))
	return value


def _payable_order(store_order_id: str) -> dict:
	from field_sales_woocommerce.connectors import get_connector
	from field_sales_woocommerce.connectors.base import ConnectorError

	try:
		order = get_connector().get_payable_order(store_order_id)
	except ConnectorError as e:
		if e.http_status == 404:
			raise CheckoutError(404, "ORDER_NOT_FOUND", _("WooCommerce order {0} was not found").format(store_order_id)) from e
		raise CheckoutError(502, "STORE_UNAVAILABLE", _("Agamsale could not read the order from WooCommerce: {0}").format(e.message)) from e
	if order.get("date_paid") or order.get("status") not in PAYABLE_STATUSES:
		raise CheckoutError(409, "NOT_PAYABLE", _("WooCommerce order {0} is {1} and cannot be paid now").format(store_order_id, order.get("status") or _("paid")))
	if flt(order.get("total")) <= 0:
		raise CheckoutError(409, "NOT_PAYABLE", _("The order total must be more than zero"))
	return order


def _field_order_for(store_order_id: str) -> str | None:
	return frappe.db.get_value("Field Order", {"external_order_id": store_order_id, "docstatus": 1}, "name")


def _checkout_row(razorpay_order_id: str | None):
	if not razorpay_order_id:
		return None
	return frappe.db.get_value(
		"Payment Transaction",
		{"transaction_type": "Checkout Order", "razorpay_order_id": razorpay_order_id},
		["name", "store_order_id", "reference_order", "razorpay_order_id", "amount", "currency", "status"],
		as_dict=True,
		for_update=True,
	)
