# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Server-only fields: read_only is only a UI hint, so REST could otherwise set them."""

import frappe


def keep_server_fields(doc, fieldnames) -> None:
	"""
	Undo client changes to ``fieldnames``: a new document gets each field's default
	(so amended and duplicated documents start clean), a saved one keeps its stored value.

	Server code that sets these fields writes them with ``db_set`` / ``frappe.db.set_value``.
	"""
	if doc.is_new():
		for fieldname in fieldnames:
			field = doc.meta.get_field(fieldname)
			doc.set(fieldname, (field.default if field else None) or None)
		return

	before = doc.get_doc_before_save()
	if not before:
		return
	for fieldname in fieldnames:
		doc.set(fieldname, before.get(fieldname))


def is_system_manager() -> bool:
	return frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()


def after_request(response=None, request=None):
	import frappe
	settings = frappe.get_single("Store Connection")
	if settings.enabled and settings.store_url and response is not None:
		store = (settings.store_url or "").rstrip("/")
		response.headers["Content-Security-Policy"] = f"frame-ancestors {store};"
		response.headers.pop("X-Frame-Options", None)
