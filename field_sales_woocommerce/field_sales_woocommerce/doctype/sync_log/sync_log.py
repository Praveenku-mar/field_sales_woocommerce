# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, nowdate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PAYLOAD_CHARS: int = 50_000

# Regex that matches any of these key names (case-insensitive) in a JSON string
# and replaces the associated string value with "****".
# Handles standard JSON: "key": "value" — does not fail on non-JSON text.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
	{
		"access_token",
		"client_secret",
		"consumer_secret",
		"api_key",
		"password",
		"refresh_token",
	}
)

_MASK_RE: re.Pattern = re.compile(
	r'("(?:'
	+ "|".join(re.escape(k) for k in sorted(_SENSITIVE_KEYS))
	+ r')"\s*:\s*)"[^"]*"',
	re.IGNORECASE,
)

# Personal data of customers (Shopify's camelCase and snake_case, Razorpay's ``contact``):
# masked too, so a log never holds who a customer is. Ids, amounts and error text stay.
_PERSONAL_KEYS: frozenset[str] = frozenset(
	{
		"email",
		"phone",
		"contact",
		"firstName",
		"lastName",
		"first_name",
		"last_name",
		"address1",
		"address2",
		"zip",
	}
)
_PERSONAL_MASK = '"[redacted]"'

_PERSONAL_RE: re.Pattern = re.compile(
	r'("(?:'
	+ "|".join(re.escape(k) for k in sorted(_PERSONAL_KEYS))
	+ r')"\s*:\s*)"(?:[^"\\]|\\.)*"',
	re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class SyncLog(Document):
	# ------------------------------------------------------------------
	# Frappe hooks
	# ------------------------------------------------------------------

	def before_insert(self):
		self._drop_single_reference()
		# Mask first: truncation can cut a secret's closing quote, and the mask then misses it.
		self._mask_sensitive_keys()
		self._truncate_payloads()

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _drop_single_reference(self):
		"""
		Clear a reference to a Single doctype (e.g. Store Connection).

		The desk's Dynamic Link control refuses Singles, so such a log could
		not be opened without an error. Connection-level logs carry no reference.
		"""
		if self.reference_doctype and frappe.get_meta(self.reference_doctype).issingle:
			self.reference_doctype = None
			self.reference_name = None

	def _truncate_payloads(self):
		"""Cap request/response payloads at _MAX_PAYLOAD_CHARS characters."""
		for fieldname in ("request_payload", "response_payload"):
			value: str | None = self.get(fieldname)
			if value and len(value) > _MAX_PAYLOAD_CHARS:
				self.set(fieldname, value[:_MAX_PAYLOAD_CHARS] + "\n\u2026 [truncated]")

	def _mask_sensitive_keys(self):
		"""Replace secrets with '****' and personal data with '[redacted]' in both payloads."""
		for fieldname in ("request_payload", "response_payload"):
			value: str | None = self.get(fieldname)
			if value:
				self.set(fieldname, _mask_payload(value))


# ---------------------------------------------------------------------------
# Module-level helpers (not whitelisted — internal use only)
# ---------------------------------------------------------------------------


def _mask_payload(payload: str) -> str:
	"""
	Mask values of sensitive keys in a JSON-like string using a regex substitution.

	Operates on raw text so it works even when the payload is truncated or
	slightly malformed JSON. Only replaces string values (``"value"``); does
	not touch ``null``, ``true``, ``false``, or numeric values.

	Example::

		>>> _mask_payload('{"access_token": "tok_live_abc123", "amount": 500}')
		'{"access_token": "****", "amount": 500}'
	"""
	masked = _MASK_RE.sub(lambda m: m.group(1) + '"****"', payload)
	return _PERSONAL_RE.sub(lambda m: m.group(1) + _PERSONAL_MASK, masked)


def log_sync(
	platform: str,
	operation: str,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	status: str = "Success",
	**kwargs,
) -> "SyncLog":
	"""
	Insert a Sync Log record from application code and return the saved doc.

	All keyword arguments must correspond to valid field names on the Sync Log
	DocType (e.g. ``http_status``, ``duration_ms``, ``error_message``,
	``error_code``, ``endpoint``, ``request_payload``, ``response_payload``,
	``external_id``, ``attempt``).

	The document is inserted with ``ignore_permissions=True`` so this helper
	can be called safely from background jobs and API handlers regardless of
	the current user's role.

	Usage::

		from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync

		log_sync(
		    platform="Shopify",
		    operation="Create Order",
		    reference_doctype="Field Order",
		    reference_name=order.name,
		    status="Failed",
		    http_status=422,
		    duration_ms=312,
		    error_message="Inventory not available for GW-SFO-1L",
		    request_payload=frappe.as_json(payload),
		    response_payload=frappe.as_json(response),
		)
	"""
	doc: SyncLog = frappe.get_doc(
		{
			"doctype": "Sync Log",
			"platform": platform,
			"operation": operation,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": status,
			**kwargs,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def log_sync_durable(**fields) -> str:
	"""
	Like ``log_sync`` but the log survives a rollback of the current transaction.

	Failures usually end in an exception that rolls back the request or job,
	which would take a plain ``log_sync`` row with it. The log is inserted in
	the current transaction and, if that transaction is rolled back before it
	commits, inserted again (and committed) right after the rollback.

	Takes the same arguments as ``log_sync`` (as keywords); returns the log name.
	"""
	name = log_sync(**fields).name

	after_rollback = getattr(frappe.db, "after_rollback", None)
	if after_rollback is not None:

		def reinsert():
			if not frappe.db.exists("Sync Log", name):
				log_sync(**fields)
				frappe.db.commit()

		after_rollback.add(reinsert)

	return name


def clear_old_logs() -> None:
	"""
	Scheduled daily job — purge Success Sync Logs older than 30 days, and Failed / Retrying
	ones older than Store Connection ``failed_log_retention_days`` (default 180; 0 keeps them).

	Called via::

	    field_sales.field_sales.doctype.sync_log.sync_log.clear_old_logs

	Registered in hooks.py under ``scheduler_events["daily"]``.
	"""
	cutoff: str = add_days(nowdate(), -30)

	frappe.db.delete(
		"Sync Log",
		{
			"status": "Success",
			"creation": ["<", cutoff],
		},
	)
	failed_days = cint(frappe.db.get_single_value("Store Connection", "failed_log_retention_days"))
	if failed_days > 0:
		frappe.db.delete(
			"Sync Log",
			{"status": ["in", ["Failed", "Retrying"]], "creation": ["<", add_days(nowdate(), -failed_days)]},
		)
	frappe.db.commit()
