# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Turn on order confirmation emails for a Store Connection saved before the option existed.
	Safe to run again.

	A Single has no stored value for a field added later (it reads as 0, ignoring the field's
	default), so only a missing value is set; a choice made in Settings is kept.
	"""
	if frappe.db.exists("Singles", {"doctype": "Store Connection", "field": "send_order_confirmation"}):
		return
	frappe.db.set_single_value("Store Connection", "send_order_confirmation", 1)
