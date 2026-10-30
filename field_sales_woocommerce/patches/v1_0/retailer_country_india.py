# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Retailers created before the Country field were all Indian."""
	if not frappe.db.exists("Country", "India"):
		return
	retailer = frappe.qb.DocType("Retailer")
	(
		frappe.qb.update(retailer)
		.set(retailer.country, "India")
		.where(retailer.country.isnull() | (retailer.country == ""))
		.run()
	)
