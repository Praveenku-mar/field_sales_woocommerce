# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt


def execute():
	"""
	Give Field Orders that carry a discount amount the matching Discount %. Safe to run again.

	Only orders without a Discount % are touched, and the discount amount itself is left as it
	was, so existing orders (and what was sent to the store) keep their totals.
	"""
	for order in frappe.get_all(
		"Field Order",
		filters={"discount_amount": (">", 0), "net_total": (">", 0)},
		or_filters=[["discount_percent", "is", "not set"], ["discount_percent", "=", 0]],
		fields=["name", "discount_amount", "net_total"],
	):
		frappe.db.set_value(
			"Field Order",
			order.name,
			"discount_percent",
			round(flt(order.discount_amount) / flt(order.net_total) * 100, 2),
			update_modified=False,
		)
