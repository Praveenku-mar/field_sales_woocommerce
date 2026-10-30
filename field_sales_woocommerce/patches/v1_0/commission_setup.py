# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe

SAMPLE_PLAN = "Sample Flat 5%"


def execute():
	"""
	Prepare existing data for commissions. Safe to run again.

	Sales Reps get Commission Active = 0 where it was never set, and an inactive sample plan is
	created only if missing (it is not a fixture, so edits to it survive a migrate).
	"""
	frappe.db.sql("update `tabSales Rep` set commission_active = 0 where commission_active is null")

	if not frappe.db.exists("Commission Plan", SAMPLE_PLAN):
		frappe.get_doc(
			{
				"doctype": "Commission Plan",
				"plan_name": SAMPLE_PLAN,
				"is_active": 0,
				"rule_type": "Flat %",
				"commission_on": "Paid",
				"flat_rate": 5,
				"description": "Example plan: 5% of the sale. Copy it or edit it, then assign it to a Sales Rep.",
			}
		).insert(ignore_permissions=True)
