# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Plan limits, platform-neutral. Core code calls ``check_limit(kind)`` before adding a sales rep
or submitting an order; each ``field_sales_limit_checks`` hook (hooks.py) may refuse with a
``frappe.ValidationError``. Today only the Shopify app plan has one
(``field_sales_woocommerce.shopify.billing.check_limit``).
"""

import frappe

SALES_REPS = "sales_reps"
ORDERS_PER_MONTH = "orders_per_month"


def check_limit(kind: str) -> None:
	"""Raise if adding one more ``kind`` (``sales_reps`` / ``orders_per_month``) is over the plan."""
	for method in frappe.get_hooks("field_sales_limit_checks"):
		frappe.get_attr(method)(kind)
