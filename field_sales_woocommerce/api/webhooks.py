# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Public endpoint the store calls with stock and product webhooks."""

import frappe

from field_sales_woocommerce.connectors import get_connector
from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync_durable

DEDUP_TTL_SECONDS = 48 * 60 * 60  # stores may redeliver for up to 48 hours
INVALID_LOG_INTERVAL_SECONDS = 60


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
	"""
	Verify a webhook, drop duplicates and queue it for ``sync.webhooks.process_event``.

	Answers quickly and writes nothing to the database (apart from one
	throttled log for a bad signature): 401 if the signature is wrong,
	otherwise 200, also when the connection is disabled or the event is a
	duplicate, so the store does not keep retrying.
	"""
	# Read before anything touches the form data; the signature covers these exact bytes.
	raw_body = frappe.request.get_data()

	try:
		conn = get_connector(require_enabled=False)
	except frappe.ValidationError:
		# Unsupported platform or misconfigured (e.g. no store URL): nothing to verify against.
		return _respond(200, "ignored")
	if not conn.settings.enabled:
		return _respond(200, "ignored")

	headers = frappe.request.headers
	if not conn.verify_webhook(headers, raw_body):
		_log_invalid_signature(conn.platform)
		return _respond(401, "invalid signature")

	event = conn.parse_webhook(headers, raw_body)
	event_id = event.get("event_id")
	key = frappe.cache.make_key(f"field_sales:webhook:{event_id}") if event_id else None
	# NX: only the first delivery of an id gets to set the key.
	if key and not frappe.cache.set(key, 1, nx=True, ex=DEDUP_TTL_SECONDS):
		return _respond(200, "duplicate")

	try:
		# The job argument is `webhook`, not `event`: frappe.enqueue has its own `event`
		# parameter (job-clearing label), so `event=` would be swallowed and never reach the job.
		frappe.enqueue(
			"field_sales_woocommerce.sync.webhooks.process_event",
			queue="short",
			platform=conn.platform,
			webhook=event,
		)
	except Exception:
		# Not queued: forget the id so the store's retry of this delivery is accepted.
		if key:
			frappe.cache.delete(key)
		raise
	return _respond(200, "ok")


def _respond(status: int, message: str) -> dict:
	"""Set the HTTP status and return the JSON body."""
	frappe.local.response.http_status_code = status
	return {"ok": status == 200, "message": message}


def _log_invalid_signature(platform: str) -> None:
	"""Log a rejected request, at most once a minute so a flood cannot fill the log."""
	key = frappe.cache.make_key("field_sales:webhook_invalid_logged")
	if frappe.cache.set(key, 1, nx=True, ex=INVALID_LOG_INTERVAL_SECONDS):
		log_sync_durable(
			platform=platform,
			operation="Webhook",
			status="Failed",
			http_status=401,
			error_code="INVALID_SIGNATURE",
			error_message="Webhook signature or shop domain did not match",
		)
