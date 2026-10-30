# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Existing sites follow the store's currency and time zone, as new ones do by default.

	A Single has no stored value for a field added later (it reads as 0, ignoring the default).
	"""
	frappe.db.set_single_value("Store Connection", "match_shop_settings", 1)
