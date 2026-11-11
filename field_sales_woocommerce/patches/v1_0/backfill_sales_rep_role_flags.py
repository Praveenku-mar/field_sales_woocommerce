# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Backfill sales_manager and sales_rep checkboxes for existing Sales Rep records
	based on the roles assigned to the linked User.
	"""
	reps = frappe.get_all("Sales Rep", fields=["name", "user"])
	for rep in reps:
		if not rep.user:
			continue
		roles = set(frappe.get_roles(rep.user))
		is_manager = 1 if "Sales Manager" in roles else 0
		is_rep = 1 if ("Field Sales Rep" in roles or "Sales Rep" in roles or not is_manager) else 0

		frappe.db.set_value(
			"Sales Rep",
			rep.name,
			{
				"sales_manager": is_manager,
				"sales_rep": is_rep,
			},
			update_modified=False,
		)
