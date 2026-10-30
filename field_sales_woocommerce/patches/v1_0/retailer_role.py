# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe

RETAILER_ROLE = "Field Sales Retailer"


def execute():
	"""
	Ensure the retailer login role exists (doctype permissions already create it
	during migrate; this also covers sites that synced out of order) and keep it
	out of the desk: retailer logins are website users. Safe to run again.
	"""
	if not frappe.db.exists("Role", RETAILER_ROLE):
		role = frappe.new_doc("Role")
		role.role_name = RETAILER_ROLE
		role.desk_access = 0
		role.flags.ignore_mandatory = role.flags.ignore_permissions = True
		role.insert()
	elif frappe.db.get_value("Role", RETAILER_ROLE, "desk_access"):
		frappe.db.set_value("Role", RETAILER_ROLE, "desk_access", 0, update_modified=False)
