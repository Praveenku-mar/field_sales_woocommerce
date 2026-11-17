# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The record's history in the app — Odoo's chatter, the Shopify timeline, WooCommerce's notes,
each edition drawing it its own way: its timeline from Frappe (comments and system notes from
Comment, field changes from Version, emails from Communication) and "Log note" to add an
internal comment.

Staff only: a customer login (Field Sales Retailer) never sees internal notes. Read access to
the record is required for both reading and posting, as in the desk; the timeline returns plain
text only (HTML is stripped), so nothing from a comment is rendered as markup.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import escape_html, get_fullname, strip_html

from field_sales_woocommerce.permissions import is_retailer_user
from field_sales_woocommerce.www.fieldsales import has_app_access

# The records that have a form with a chatter in the app.
CHATTER_DOCTYPES = ("Field Order", "Retailer", "Visit", "Platform Product", "Sales Rep", "Commission Statement")
# Comment types worth showing: notes people wrote, and the app's own messages (Info).
COMMENT_TYPES = ("Comment", "Info", "Workflow", "Assigned", "Attachment", "Shared")
MAX_ITEMS = 100
MAX_NOTE_LENGTH = 5000
MAX_VALUE_LENGTH = 120
# Bookkeeping fields a person did not change.
HIDDEN_FIELDS = {"modified", "modified_by", "idx", "docstatus", "_seen", "_comments", "_liked_by", "_assign", "_user_tags"}


@frappe.whitelist(methods=["GET"])
def get_chatter(doctype: str, name: str) -> dict:
	"""
	The record's timeline, newest first.

	:return: ``{"items": [{kind, at, by, by_name, text?, subject?, changes?}], "can_post": bool}``
		kind is ``note`` (a person's comment), ``info`` (a system message), ``change`` (field
		changes, ``changes`` = ``[{label, from, to}]``) or ``email``.
	"""
	doc = _readable(doctype, name)
	items = [*_comments(doctype, name), *_changes(doc), *_emails(doctype, name)]
	items.sort(key=lambda item: item["at"], reverse=True)
	items = items[:MAX_ITEMS]
	names = {}
	for item in items:
		user = item["by"]
		if user and user not in names:
			names[user] = get_fullname(user)
		item["by_name"] = item.get("by_name") or names.get(user) or user
		item["at"] = str(item["at"])
	return {"items": items, "can_post": True}


@frappe.whitelist(methods=["POST"])
def post_note(doctype: str, name: str, text: str) -> dict:
	"""Log an internal note on the record (a Comment, as the desk's comment box writes)."""
	doc = _readable(doctype, name)
	text = (text or "").strip()
	if not text:
		frappe.throw(_("Write a note first"))
	if len(text) > MAX_NOTE_LENGTH:
		frappe.throw(_("A note can be at most {0} characters").format(MAX_NOTE_LENGTH))
	content = escape_html(text).replace("\n", "<br>")
	comment = doc.add_comment("Comment", content, comment_email=frappe.session.user, comment_by=get_fullname())
	return {
		"kind": "note",
		"at": str(comment.creation),
		"by": frappe.session.user,
		"by_name": get_fullname(),
		"text": text,
	}


def _readable(doctype: str, name: str):
	if frappe.session.user == "Guest" or not has_app_access() or is_retailer_user():
		frappe.throw(_("You do not have access to this record's history"), frappe.PermissionError)
	if doctype not in CHATTER_DOCTYPES:
		frappe.throw(_("This record has no history in the app"), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	return doc


def _comments(doctype: str, name: str) -> list[dict]:
	rows = frappe.get_all(
		"Comment",
		filters={"reference_doctype": doctype, "reference_name": name, "comment_type": ("in", COMMENT_TYPES)},
		fields=["comment_type", "content", "comment_email", "comment_by", "owner", "creation"],
		order_by="creation desc",
		limit=MAX_ITEMS,
	)
	return [
		{
			"kind": "note" if row.comment_type == "Comment" else "info",
			"at": row.creation,
			"by": row.comment_email or row.owner,
			"by_name": row.comment_by,
			"text": _plain(row.content),
		}
		for row in rows
	]


def _changes(doc) -> list[dict]:
	meta = frappe.get_meta(doc.doctype)
	rows = frappe.get_all(
		"Version",
		filters={"ref_doctype": doc.doctype, "docname": doc.name},
		fields=["owner", "creation", "data"],
		order_by="creation desc",
		limit=50,
	)
	items = []
	for row in rows:
		try:
			data = json.loads(row.data or "{}")
		except ValueError:
			continue
		changes = []
		for change in data.get("changed") or []:
			if not isinstance(change, list | tuple) or len(change) != 3:
				continue
			field, old, new = change
			if field in HIDDEN_FIELDS or not meta.get_field(field) and field != "name":
				continue
			changes.append({"label": _(meta.get_label(field)), "from": _value(old), "to": _value(new)})
		if changes:
			items.append({"kind": "change", "at": row.creation, "by": row.owner, "changes": changes})
	return items


def _emails(doctype: str, name: str) -> list[dict]:
	rows = frappe.get_all(
		"Communication",
		filters={"reference_doctype": doctype, "reference_name": name, "communication_type": "Communication"},
		fields=["subject", "sender", "owner", "creation", "sent_or_received"],
		order_by="creation desc",
		limit=20,
	)
	return [
		{
			"kind": "email",
			"at": row.creation,
			"by": row.owner,
			"subject": _plain(row.subject),
			"text": _("Received from {0}").format(row.sender) if row.sent_or_received == "Received" else _("Email sent"),
		}
		for row in rows
	]


def _plain(html: str | None) -> str:
	"""Comment HTML as plain text: tags out, line breaks kept, entities decoded."""
	import html as html_lib

	text = (html or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n").replace("</p>", "\n")
	return html_lib.unescape(strip_html(text)).strip()


def _value(value) -> str:
	if value in (None, ""):
		return ""
	text = _plain(str(value))
	return text if len(text) <= MAX_VALUE_LENGTH else text[: MAX_VALUE_LENGTH - 1] + "…"
