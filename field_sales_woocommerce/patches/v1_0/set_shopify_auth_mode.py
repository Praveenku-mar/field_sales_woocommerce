# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Existing sites connect with the client credentials grant, as before.

	A Single has no stored value for a field added later, so the default is written here.
	"""
	singles = frappe.qb.DocType("Singles")
	stored = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == "Store Connection") & (singles.field == "shopify_auth_mode"))
		.run()
	)
	if not (stored and stored[0][0]):
		frappe.db.set_single_value("Store Connection", "shopify_auth_mode", "Client Credentials")
