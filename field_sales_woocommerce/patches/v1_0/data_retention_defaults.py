# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Failed Sync Logs were kept forever; existing sites now keep them 180 days, like new ones.

	A Single has no stored value for a field added later (it reads as 0, which means "keep").
	"""
	singles = frappe.qb.DocType("Singles")
	stored = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == "Store Connection") & (singles.field == "failed_log_retention_days"))
		.run()
	)
	if not stored:
		frappe.db.set_single_value("Store Connection", "failed_log_retention_days", 180)
