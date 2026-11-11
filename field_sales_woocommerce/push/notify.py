# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Bell notifications that are also pushed to the recipient's phones.

``notify_users`` writes the Notification Log exactly as before (Frappe's own dedupe,
"notifications off" and don't-notify-yourself rules apply) and tags it with a push kind.
The ``after_insert`` hook on Notification Log then queues the push, so a push goes out
only for a bell entry that was actually created, and links back to it.
"""

import frappe
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

from field_sales_woocommerce.push import messages


def notify_users(
	users,
	kind: str,
	doctype: str,
	docname: str | None,
	subject: str,
	email_content: str | None = None,
	from_user: str | None = None,
	params: dict | None = None,
	dedupe_on: list[str] | None = None,
) -> None:
	"""
	Bell notification of ``kind`` for ``users``, pushed to their phones as well.

	:param params: values for the push text (see ``push.messages``); never names or amounts.
	"""
	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": doctype,
			"document_name": docname,
			"from_user": from_user or "Administrator",
			"subject": subject,
			"email_content": email_content or subject,
			# Not columns: read by on_notification_log_insert from the in-memory doc.
			"push_kind": kind,
			"push_params": params or {},
		},
		dedupe_on=dedupe_on,
	)


def on_notification_log_insert(doc, method=None) -> None:
	"""doc_events hook: queue the push for a bell entry made by ``notify_users``."""
	kind = doc.get("push_kind")
	if not kind or not _owns_field_sales_data():
		return
	from field_sales_woocommerce.push.fcm import push_enabled

	if not push_enabled():
		return
	if not frappe.db.exists("Mobile Device", {"user": doc.for_user, "enabled": 1}):
		return
	frappe.enqueue(
		"field_sales_woocommerce.push.notify.send_push",
		queue="short",
		now=frappe.in_test,
		enqueue_after_commit=True,
		user=doc.for_user,
		kind=kind,
		doctype=doc.document_type,
		docname=doc.document_name,
		log=doc.name,
		params=doc.get("push_params") or {},
	)


def _owns_field_sales_data() -> bool:
	"""
	Whether this edition owns the Field Sales data on this site (as ``get_mobile_manifest``
	reports it). A site with several editions installed runs every edition's hooks; only
	the owner pushes, so nothing is sent twice.
	"""
	module = frappe.db.get_value("DocType", "Field Order", "module")
	return bool(module) and frappe.local.module_app.get(frappe.scrub(module)) == __name__.split(".", 1)[0]


def send_push(user, kind, doctype=None, docname=None, log=None, params=None) -> dict:
	"""Background job: send one push of ``kind`` to every phone of ``user``."""
	from field_sales_woocommerce.push.fcm import send_to_user

	title, body = messages.render(kind, docname, params)
	data = {"kind": kind, "doctype": doctype, "name": docname, "notification_log": log}
	tag = f"{doctype}:{docname}" if doctype and docname else None
	return send_to_user(user, title, body, data, tag=tag)
