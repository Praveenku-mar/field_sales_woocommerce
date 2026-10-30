# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Razorpay REST API (payment links for Field Orders).

Credentials live in the Razorpay Settings Single; the secrets are read only
with ``get_password()`` on the server and never returned to the client.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.query_builder.functions import Coalesce
from frappe.utils import (
	cint,
	escape_html,
	flt,
	fmt_money,
	get_datetime,
	get_system_timezone,
	now_datetime,
)

from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync, log_sync_durable
from field_sales_woocommerce.sync.notify import users_with_roles
from field_sales_woocommerce.utils.money import site_currency

API_BASE = "https://api.razorpay.com/v1"
REQUEST_TIMEOUT = 20  # seconds
WEBHOOK_PATH = "/api/method/field_sales_woocommerce.payments.razorpay.webhook"
SETTINGS_ROLES = ("System Manager", "Sales Manager")
PLATFORM = "Razorpay"  # Sync Log platform
MIN_EXPIRY_SECONDS = 16 * 60  # Razorpay wants expire_by at least 15 minutes ahead
MAX_REFERENCE_TRIES = 10
# Razorpay link states that can still be paid, so must be cancelled before a new link.
OPEN_LINK_STATES = ("created", "partially_paid")
WEBHOOK_EVENTS = (
	"payment_link.paid",
	"payment_link.partially_paid",
	"payment_link.expired",
	"payment_link.cancelled",
)
INVALID_LOG_INTERVAL_SECONDS = 60
PAYMENT_NOTIFY_ROLES = ("Sales Manager",)
PAYMENT_EVENT = "field_order_payment_updated"  # realtime; open forms reload on it
PAYMENT_FIELDS = (
	"payment_status",
	"amount_paid",
	"razorpay_payment_id",
	"paid_on",
	"razorpay_payment_link_id",
	"razorpay_payment_link_url",
	"payment_link_created_on",
	"payment_link_expires_on",
	"store_marked_paid_on",
)
RECONCILE_DAYS = 7
RECONCILE_BATCH = 50


class RazorpayError(Exception):
	"""A failed Razorpay call; ``message`` is Razorpay's ``error.description`` when it sent one."""

	def __init__(self, message: str, http_status: int | None = None, code: str | None = None):
		super().__init__(message)
		self.message = message
		self.http_status = http_status
		self.code = code


def get_settings():
	return frappe.get_single("Razorpay Settings")


def _credentials(settings) -> tuple[str, str]:
	key_id = (settings.key_id or "").strip()
	key_secret = settings.get_password("key_secret", raise_exception=False)
	if not key_id or not key_secret:
		raise RazorpayError(_("Razorpay Key ID and Key Secret are not set"), code="NOT_CONFIGURED")
	return key_id, key_secret


def request(
	method: str, path: str, *, params: dict | None = None, json: dict | None = None, settings=None
) -> dict:
	"""
	Call the Razorpay API with basic auth and return the JSON body.

	:raises RazorpayError: no credentials, network failure, or a 4xx/5xx answer.
	"""
	auth = _credentials(settings or get_settings())
	try:
		response = requests.request(
			method, f"{API_BASE}{path}", params=params, json=json, auth=auth, timeout=REQUEST_TIMEOUT
		)
	except requests.RequestException as e:
		raise RazorpayError(
			_("Could not reach Razorpay: {0}").format(type(e).__name__), code="NETWORK_ERROR"
		) from e

	try:
		body = response.json()
	except ValueError:
		body = {}
	if not isinstance(body, dict):
		body = {}

	if response.status_code >= 400:
		error = body.get("error") or {}
		raise RazorpayError(
			error.get("description") or _("Razorpay returned HTTP {0}").format(response.status_code),
			http_status=response.status_code,
			code=error.get("code"),
		)
	return body


@frappe.whitelist(methods=["GET"])
def get_webhook_url() -> str:
	"""The URL to enter in the Razorpay Dashboard (the SPA loads settings without ``onload``)."""
	frappe.only_for(SETTINGS_ROLES)
	return webhook_url()


# A bare host with an optional port, as a proxy puts it in X-Forwarded-Host.
_FORWARDED_HOST = re.compile(r"[A-Za-z0-9.-]+(?::\d{1,5})?")


def webhook_url() -> str:
	"""
	The webhook URL as the person viewing it reached the site.

	Behind a tunnel (ngrok with ``--host-header``) the Host is the local site name, so the public
	address only comes in X-Forwarded-Host / X-Forwarded-Proto. Without them: ``host_name``.
	``host_name`` itself must stay local (PDF generation fetches CSS from it).
	"""
	request = getattr(frappe.local, "request", None)
	headers = request.headers if request else {}
	host = (headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
	if host and _FORWARDED_HOST.fullmatch(host):
		proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
		return f"{proto if proto in ('http', 'https') else 'https'}://{host}{WEBHOOK_PATH}"
	return frappe.utils.get_url(WEBHOOK_PATH)


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:
	"""
	Check the saved keys by listing one payment link.

	:return: ``{"ok": bool, "message": str}``; ``message`` is Razorpay's error on failure.
	"""
	frappe.only_for(SETTINGS_ROLES)

	settings = get_settings()
	try:
		request("GET", "/payment_links", params={"count": 1}, settings=settings)
	except RazorpayError as e:
		return {"ok": False, "message": e.message}
	return {"ok": True, "message": _("Connected to Razorpay ({0} mode)").format(settings.mode or "Test")}


# ----------------------------------------------------------------------
# Payment link client
# ----------------------------------------------------------------------


def create_payment_link(doc, reference_id: str | None = None, settings=None, notify: bool = True) -> dict:
	"""
	Create a Razorpay payment link for a Field Order's grand total and return Razorpay's link object.

	:param reference_id: Unique per link on Razorpay; defaults to the order name.
	:param notify: False stops Razorpay's own SMS/email whatever the settings say.
	"""
	settings = settings or get_settings()
	payload = _link_payload(doc, reference_id or doc.name, settings, notify=notify)
	return _logged(
		"Create Payment Link",
		doc.name,
		payload,
		lambda: request("POST", "/payment_links", json=payload, settings=settings),
	)


def fetch_payment_link(link_id: str, settings=None) -> dict:
	return request("GET", f"/payment_links/{_safe_id(link_id)}", settings=settings)


def cancel_payment_link(link_id: str, field_order: str | None = None, settings=None) -> dict:
	return _logged(
		"Cancel Payment Link",
		field_order,
		None,
		lambda: request("POST", f"/payment_links/{_safe_id(link_id)}/cancel", settings=settings),
	)


def _link_payload(doc, reference_id: str, settings, notify: bool = True) -> dict:
	retailer = (
		frappe.db.get_value("Retailer", doc.retailer, ["retailer_name", "phone", "email"], as_dict=True) or {}
	)
	contact = indian_phone(retailer.get("phone"))
	email = (retailer.get("email") or "").strip()
	customer = {
		"name": doc.retailer_name or retailer.get("retailer_name"),
		"contact": contact,
		"email": email,
	}

	payload = {
		"amount": int(round(flt(doc.grand_total) * 100)),  # paise  # noqa: RUF046
		"currency": site_currency(),
		"accept_partial": False,
		"description": f"Field Order {doc.name}",
		"reference_id": reference_id,
		"customer": {k: v for k, v in customer.items() if v},
		"notify": {
			"sms": bool(notify and settings.notify_sms and contact),
			"email": bool(notify and settings.notify_email and email),
		},
		"reminder_enable": True,
		"notes": {"field_order": doc.name, "sales_rep": doc.sales_rep or "", "retailer": doc.retailer or ""},
	}
	hours = cint(settings.link_expiry_hours)
	if hours > 0:
		payload["expire_by"] = int(time.time()) + max(hours * 3600, MIN_EXPIRY_SECONDS)
	callback_url = (settings.callback_url or "").strip()
	if callback_url:
		payload["callback_url"] = callback_url
		payload["callback_method"] = "get"
	return payload


def indian_phone(phone: str | None) -> str | None:
	"""``+91XXXXXXXXXX`` for a 10-digit Indian mobile (with or without +91 / 0), else None."""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	return f"+91{digits}" if len(digits) == 10 and digits[0] in "6789" else None


def _safe_id(link_id: str) -> str:
	# Ids go into the URL path; Razorpay ids are plink_ + alphanumerics.
	if not re.fullmatch(r"[A-Za-z0-9_]+", link_id or ""):
		raise RazorpayError(_("Invalid payment link id"), code="INVALID_ID")
	return link_id


def _logged(operation: str, field_order: str | None, payload: dict | None, call):
	"""Run one Razorpay call and write a Sync Log (a failed one survives the rollback)."""
	start = time.monotonic()
	fields = {
		"platform": PLATFORM,
		"operation": operation,
		"reference_doctype": "Field Order" if field_order else None,
		"reference_name": field_order,
	}
	try:
		result = call()
	except RazorpayError as e:
		log_sync_durable(
			**fields,
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=e.http_status,
			error_code=e.code or "RazorpayError",
			error_message=e.message,
			request_payload=frappe.as_json(payload) if payload else None,
		)
		raise
	log_sync(**fields, status="Success", duration_ms=_elapsed_ms(start), external_id=result.get("id"))
	return result


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)


def _from_unix(ts) -> datetime | None:
	"""Unix timestamp -> naive datetime in the system time zone (how Frappe stores Datetime)."""
	if not cint(ts):
		return None
	return datetime.fromtimestamp(cint(ts), tz=ZoneInfo(get_system_timezone())).replace(tzinfo=None)


# ----------------------------------------------------------------------
# Whitelisted
# ----------------------------------------------------------------------


def payment_links_disabled_reason() -> str | None:
	return None


@frappe.whitelist(methods=["POST"])
def create_link_for_order(field_order: str, replace: bool = False) -> dict:
	"""
	Create (or return the still-valid) Razorpay payment link for a submitted Field Order.

	The order row is locked for the whole call, so a double click cannot create two links.
	An older link that can still be paid is cancelled first.

	:param replace: Make a new link even when the current one can still be paid (it is cancelled).

	:return: ``{"url", "id", "expires_on"}``
	"""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	doc.check_permission("write")

	disabled = payment_links_disabled_reason()
	if disabled:
		frappe.throw(disabled, title=_("Payment link not created"))
	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Razorpay payment links are not enabled"))
	if doc.docstatus != 1:
		frappe.throw(_("Submit the order before creating a payment link"))
	if flt(doc.grand_total) <= 0:
		frappe.throw(_("The order total must be more than zero"))
	if doc.payment_status == "Paid":
		frappe.throw(_("This order is already paid"))

	if (
		not replace
		and doc.payment_status == "Link Sent"
		and doc.razorpay_payment_link_url
		and not _expired(doc)
	):
		return _link_result(doc)

	try:
		return _replace_link(doc, settings)
	except RazorpayError as e:
		frappe.throw(_("Razorpay: {0}").format(e.message), title=_("Payment link not created"))


def link_for_confirmation(doc) -> dict | None:
	"""
	Payment link for the order confirmation email: the stored one if it can still be paid, else a new one.

	Razorpay is told not to notify the customer, so the email is the only place the link is sent.
	Never raises: without a link the email still goes out. A failure is put on the order's timeline
	and in ``doc.flags.payment_link_error`` so the rep knows to share a link another way.

	:return: ``{"url", "id", "expires_on"}`` or None
	"""
	if payment_links_disabled_reason() is not None:
		return None
	settings = get_settings()
	if not settings.enabled or flt(doc.grand_total) <= 0 or doc.payment_status in ("Paid", "Partially Paid"):
		return None
	if doc.payment_status == "Link Sent" and doc.razorpay_payment_link_url and not _expired(doc):
		return _link_result(doc)
	try:
		return _replace_link(doc, settings, notify=False)
	except Exception as e:
		frappe.log_error(title=f"Payment link for confirmation failed: {doc.name}")
		reason = getattr(e, "message", None) or str(e) or e.__class__.__name__
		doc.flags.payment_link_error = reason
		doc.add_comment(
			"Comment",
			_(
				"The order confirmation email went out without a payment link: Razorpay refused a link for {0} ({1}). Create one from the Payment card and share it."
			).format(fmt_money(doc.grand_total, currency=site_currency()), escape_html(reason)),
		)
		return None


def _replace_link(doc, settings, notify: bool = True) -> dict:
	"""
	Retire the order's old link, then create and store a new one.

	:raises RazorpayError: if the old link was already cancelled, the order is committed as Link Cancelled first.
	"""
	cancelled = False
	try:
		previous_reference = None
		if doc.razorpay_payment_link_id:
			previous_reference, cancelled = _retire_link(doc, settings)
		link = _create_unique(doc, settings, previous_reference, notify=notify)
	except RazorpayError:
		if cancelled:
			# The old link is gone on Razorpay: record that even though creating failed.
			frappe.db.rollback()
			doc.db_set("payment_status", "Link Cancelled", update_modified=False)
			frappe.db.commit()
		raise

	doc.db_set(
		{
			"razorpay_payment_link_id": link.get("id"),
			"razorpay_payment_link_url": link.get("short_url"),
			"payment_link_expires_on": _from_unix(link.get("expire_by")),
			"payment_link_created_on": now_datetime(),
			"payment_status": "Link Sent",
		},
		update_modified=False,
	)
	doc.add_comment(
		"Info",
		_("Razorpay payment link created: {0}").format(
			escape_html(link.get("short_url") or link.get("id") or "")
		),
	)
	return _link_result(doc)


@frappe.whitelist(methods=["GET"])
def get_payment_config() -> dict:
	"""Whether payment links can be created; never returns the keys."""
	return {
		"enabled": bool(cint(frappe.db.get_single_value("Razorpay Settings", "enabled"))),
		"available": payment_links_disabled_reason() is None,
	}


def _expired(doc) -> bool:
	return bool(doc.payment_link_expires_on) and get_datetime(doc.payment_link_expires_on) <= now_datetime()


def _link_result(doc) -> dict:
	return {
		"url": doc.razorpay_payment_link_url,
		"id": doc.razorpay_payment_link_id,
		"expires_on": doc.payment_link_expires_on,
	}


def _retire_link(doc, settings) -> tuple[str | None, bool]:
	"""
	Cancel the order's previous link if it can still be paid.

	:return: ``(its reference_id, whether it was cancelled now)``
	:raises frappe.ValidationError: the previous link was already paid.
	"""
	old = fetch_payment_link(doc.razorpay_payment_link_id, settings=settings)
	status = old.get("status")
	if status == "paid":
		frappe.throw(
			_(
				"The previous payment link {0} is already paid; the order will show as paid once Razorpay confirms it."
			).format(doc.razorpay_payment_link_id)
		)
	cancelled = False
	if status in OPEN_LINK_STATES:
		cancel_payment_link(doc.razorpay_payment_link_id, field_order=doc.name, settings=settings)
		cancelled = True
	return old.get("reference_id"), cancelled


def _create_unique(doc, settings, previous_reference: str | None, notify: bool = True) -> dict:
	"""Create the link with the next free reference id: NAME, then NAME-2, NAME-3, ..."""
	n = _reference_number(doc.name, previous_reference) + 1 if previous_reference else 1
	for _try in range(MAX_REFERENCE_TRIES):
		reference_id = doc.name if n == 1 else f"{doc.name}-{n}"
		try:
			return create_payment_link(doc, reference_id=reference_id, settings=settings, notify=notify)
		except RazorpayError as e:
			# Taken by a link this order no longer stores (e.g. created but not saved here).
			if e.http_status == 400 and "reference" in (e.message or "").lower():
				n += 1
				continue
			raise
	raise RazorpayError(_("Could not find a free reference id for this order"), code="REFERENCE_ID")


def _reference_number(name: str, reference_id: str) -> int:
	"""``NAME`` -> 1, ``NAME-3`` -> 3; anything else -> 1."""
	suffix = reference_id[len(name) + 1 :] if reference_id.startswith(f"{name}-") else ""
	return cint(suffix) if suffix.isdigit() else 1


# ----------------------------------------------------------------------
# Payment state: webhook and refresh
# ----------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook():
	"""
	Razorpay payment link events -> the Field Order's payment fields.

	400 when payments are off, 401 when the signature is missing or wrong (both change nothing);
	200 for duplicates, other events and links no order has; 500 on an unexpected error so
	Razorpay retries. Every delivery past the signature check gets a Razorpay Webhook Event row.
	"""
	# Read before anything touches the form data; the signature covers these exact bytes.
	raw_body = frappe.request.get_data()
	headers = frappe.request.headers

	settings = get_settings()
	if not settings.enabled:
		_log_rejected(400, "DISABLED", "Payment webhook received while Razorpay is disabled")
		return _respond(400, "payments are not enabled")
	if not _valid_signature(raw_body, headers.get("X-Razorpay-Signature"), settings):
		_log_rejected(401, "INVALID_SIGNATURE", "Payment webhook signature is missing or wrong")
		return _respond(401, "invalid signature")

	event_id = (headers.get("X-Razorpay-Event-Id") or "").strip() or None
	if _already_handled(event_id):
		return _respond(200, "duplicate")

	try:
		body = json.loads(raw_body)
	except ValueError:
		body = None
	body = body if isinstance(body, dict) else {}
	payload = body.get("payload") or {}
	link = (payload.get("payment_link") or {}).get("entity") or {}
	payment = (payload.get("payment") or {}).get("entity")
	record = {
		"event_id": event_id,
		"event": body.get("event"),
		"payment_link_id": link.get("id"),
		"payload": raw_body.decode("utf-8", "replace"),
	}

	field_order = None
	if record["event"] in WEBHOOK_EVENTS and link.get("id"):
		field_order = frappe.db.get_value(
			"Field Order", {"razorpay_payment_link_id": link["id"], "docstatus": ("!=", 0)}, "name"
		)
	if not field_order:
		_record_event(record, "Ignored")
		return _respond(200, "ignored")
	record["reference_order"] = field_order

	user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Field Order", field_order, for_update=True)
		# Another delivery of this event may have finished while this one waited for the lock.
		if _already_handled(event_id):
			return _respond(200, "duplicate")
		if _stale_link(doc, link):
			# The order moved to a new link while this event waited for the lock
			# (New Link cancels the old one, and Razorpay reports that cancel).
			_record_event(record, "Ignored")
			return _respond(200, "ignored: not the order's current link")
		_apply_link_state(doc, link, payment)
		_record_event(record, "Processed")
		log_sync(
			platform=PLATFORM,
			operation="Payment Webhook",
			reference_doctype="Field Order",
			reference_name=field_order,
			external_id=link["id"],
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Razorpay webhook failed: {event_id or link['id']}")
		_record_event(record, "Error", error=str(e)[:1000])
		frappe.db.commit()  # keep the Error row; the 500 asks Razorpay to retry
		return _respond(500, "error")
	finally:
		frappe.set_user(user)
	return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def refresh_payment_status(field_order: str) -> dict:
	"""
	Read the order's payment link from Razorpay and apply its state: the fallback for a missed webhook.

	:return: the order's payment fields
	"""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	doc.check_permission("read")
	if not doc.razorpay_payment_link_id:
		frappe.throw(_("This order has no payment link"))
	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Razorpay payment links are not enabled"))
	try:
		link = fetch_payment_link(doc.razorpay_payment_link_id, settings=settings)
	except RazorpayError as e:
		frappe.throw(_("Razorpay: {0}").format(e.message), title=_("Payment status not refreshed"))
	_apply_link_state(doc, link, _latest_payment(link))
	return _payment_fields(doc)


def cancel_link_for_cancelled_order(doc) -> None:
	"""
	Field Order on_cancel: cancel its open link on Razorpay and mark it Link Cancelled.

	A failed call never blocks the cancel; it is in Sync Log and on the timeline, the order
	stays Link Sent so a payment on it is still picked up (webhook / reconcile_open_links()).
	"""
	try:
		cancel_payment_link(doc.razorpay_payment_link_id, field_order=doc.name)
	except RazorpayError as e:
		doc.add_comment(
			"Info",
			_("Payment link {0} could not be cancelled on Razorpay: {1}. Cancel it in the Razorpay Dashboard.").format(
				escape_html(doc.razorpay_payment_link_id or ""), escape_html(e.message or "")
			),
		)
		return
	doc.db_set("payment_status", "Link Cancelled", update_modified=False)
	doc.add_comment("Info", _("Payment link cancelled on Razorpay with the order."))


def reconcile_open_links() -> None:
	"""
	Hourly: re-read Link Sent links made in the last 7 days (oldest first, 50 a run) and apply their
	state, for webhooks that never arrived. One order's failure does not stop the others.
	"""
	if not cint(frappe.db.get_single_value("Razorpay Settings", "enabled")):
		return
	cutoff = frappe.utils.add_days(now_datetime(), -RECONCILE_DAYS)
	fo = frappe.qb.DocType("Field Order")
	link_created = Coalesce(fo.payment_link_created_on, fo.creation)
	names = (
		frappe.qb.from_(fo)
		.select(fo.name)
		.where(fo.docstatus != 0)
		.where(fo.payment_status == "Link Sent")
		.where(fo.razorpay_payment_link_id.isnotnull())
		.where(link_created >= cutoff)
		.orderby(link_created)
		.limit(RECONCILE_BATCH)
	).run(pluck=True)

	settings = get_settings()
	for name in names:
		try:
			doc = frappe.get_doc("Field Order", name, for_update=True)
			if doc.payment_status == "Link Sent" and doc.razorpay_payment_link_id:
				link = fetch_payment_link(doc.razorpay_payment_link_id, settings=settings)
				_apply_link_state(doc, link, _latest_payment(link))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Razorpay reconcile failed: {name}")


def _apply_link_state(doc, link_entity: dict, payment_entity: dict | None = None) -> bool:
	"""
	Copy a payment link's state onto its Field Order (webhook and refresh_payment_status()).

	Safe to repeat; an order that is already Paid is never changed.

	:return: whether anything changed
	"""
	if doc.payment_status == "Paid" or _stale_link(doc, link_entity):
		return False

	link_status = link_entity.get("status")
	if link_status in ("paid", "partially_paid"):
		paid_paise = cint(link_entity.get("amount_paid"))
		if paid_paise <= 0:
			return False
		payment_entity = payment_entity or {}
		full = paid_paise >= round(flt(doc.grand_total) * 100)
		values = {
			"payment_status": "Paid" if full else "Partially Paid",
			"amount_paid": paid_paise / 100,
			"paid_on": _from_unix(payment_entity.get("created_at")) or doc.paid_on or now_datetime(),
		}
		if payment_entity.get("id"):
			values["razorpay_payment_id"] = payment_entity["id"]
	elif link_status == "expired":
		values = {"payment_status": "Link Expired"}
	elif link_status == "cancelled":
		values = {"payment_status": "Link Cancelled"}
	elif link_status == "created":
		# Still payable on Razorpay: repairs an order wrongly shown as cancelled or expired.
		values = {"payment_status": "Link Sent"}
	else:
		return False

	changed = {k: v for k, v in values.items() if doc.get(k) != v}
	if not changed:
		return False
	money_in = "amount_paid" in changed
	doc.db_set(changed, update_modified=False)

	if money_in:
		doc.add_comment(
			"Info",
			_("Razorpay payment received: {0} of {1} ({2}). Payment status: {3}.").format(
				fmt_money(doc.amount_paid, currency=site_currency()),
				fmt_money(doc.grand_total, currency=site_currency()),
				escape_html(doc.razorpay_payment_id or link_entity.get("id") or ""),
				_(doc.payment_status),
			),
		)
		_notify_payment(doc)
		if doc.payment_status == "Paid":
			# Imported here: sync.orders imports sync.confirmation, which imports this module.
			from field_sales_woocommerce.sync.orders import enqueue_mark_store_paid

			enqueue_mark_store_paid(doc.name)
	elif "payment_status" in changed:
		doc.add_comment("Info", _("Razorpay payment link status: {0}.").format(_(doc.payment_status)))
	frappe.publish_realtime(
		PAYMENT_EVENT, _payment_fields(doc), doctype="Field Order", docname=doc.name, after_commit=True
	)
	return True


def _stale_link(doc, link_entity: dict) -> bool:
	"""
	Whether this is news about an old link that no longer matters: its expiry or
	cancel must not mark the order's current link. Money paid on an old link is
	still recorded, so paid states are never stale.
	"""
	link_id = link_entity.get("id")
	return bool(
		link_id
		and doc.razorpay_payment_link_id
		and link_id != doc.razorpay_payment_link_id
		and link_entity.get("status") not in ("paid", "partially_paid")
	)


def _notify_payment(doc) -> None:
	"""Bell notification and a realtime event for the order's rep and every Sales Manager."""
	users = {doc.sales_rep, *users_with_roles(PAYMENT_NOTIFY_ROLES)} - {None, "", "Guest", "Administrator"}
	users = [u for u in sorted(users) if frappe.db.get_value("User", u, "enabled")]
	if not users:
		return
	subject = _("Payment received: {0} — {1} from {2}").format(
		f"<b>{escape_html(doc.name)}</b>",
		fmt_money(doc.amount_paid, currency=site_currency()),
		escape_html(doc.retailer_name or doc.retailer or ""),
	)
	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": "Field Order",
			"document_name": doc.name,
			"from_user": "Administrator",
			"subject": subject,
			"email_content": subject,
		},
	)
	message = _payment_fields(doc)
	for user in users:
		frappe.publish_realtime(PAYMENT_EVENT, message, user=user, after_commit=True)


def _payment_fields(doc) -> dict:
	return {"name": doc.name, **{f: doc.get(f) for f in PAYMENT_FIELDS}}


def _latest_payment(link: dict) -> dict | None:
	"""The newest captured payment of a fetched link, shaped like a webhook payment entity."""
	captured = [p for p in link.get("payments") or [] if p.get("status") == "captured"]
	if not captured:
		return None
	latest = max(captured, key=lambda p: cint(p.get("created_at")))
	return {"id": latest.get("payment_id"), "created_at": latest.get("created_at")}


def _valid_signature(raw_body: bytes, signature: str | None, settings) -> bool:
	"""``X-Razorpay-Signature`` is the hex HMAC-SHA256 of the raw body with the webhook secret."""
	secret = settings.get_password("webhook_secret", raise_exception=False)
	if not secret or not signature:
		return False
	expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected.encode(), signature.strip().encode())


def _already_handled(event_id: str | None) -> bool:
	if not event_id:
		return False
	status = frappe.db.get_value("Razorpay Webhook Event", {"event_id": event_id}, "status")
	return status in ("Processed", "Ignored")


def _record_event(values: dict, status: str, error: str | None = None) -> None:
	"""Insert the event row, or update it when Razorpay retries an event that failed."""
	values = {**values, "status": status, "error": error}
	name = values.get("event_id") and frappe.db.get_value(
		"Razorpay Webhook Event", {"event_id": values["event_id"]}
	)
	if name:
		frappe.db.set_value("Razorpay Webhook Event", name, values)
	else:
		frappe.get_doc({"doctype": "Razorpay Webhook Event", **values}).insert(ignore_permissions=True)


def _respond(status: int, message: str) -> dict:
	"""Set the HTTP status and return the JSON body."""
	frappe.local.response.http_status_code = status
	return {"status": message}


def _log_rejected(http_status: int, code: str, message: str) -> None:
	"""Log a rejected delivery, at most once a minute so a flood cannot fill the log."""
	key = frappe.cache.make_key("field_sales:razorpay_webhook_rejected_logged")
	if frappe.cache.set(key, 1, nx=True, ex=INVALID_LOG_INTERVAL_SECONDS):
		log_sync_durable(
			platform=PLATFORM,
			operation="Payment Webhook",
			status="Failed",
			http_status=http_status,
			error_code=code,
			error_message=message,
		)
