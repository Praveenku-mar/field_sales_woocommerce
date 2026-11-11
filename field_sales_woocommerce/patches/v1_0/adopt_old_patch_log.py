# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
patches.txt once listed this app's patches as ``field_sales.patches.v1_0.*`` (copied from the
Shopify edition), so existing sites logged them under those names. They are now listed as
``field_sales_woocommerce.patches.v1_0.*``: copy each logged old name to its new name so the
renamed patches count as done there instead of running a second time. Fresh sites have no
old entries and run the patches normally.
"""

import frappe

OLD_PREFIX = "field_sales.patches.v1_0."
NEW_PREFIX = "field_sales_woocommerce.patches.v1_0."


def execute():
	done = set(frappe.get_all("Patch Log", filters={"skipped": 0}, pluck="patch"))
	for old in sorted(done):
		if not old.startswith(OLD_PREFIX):
			continue
		new = NEW_PREFIX + old[len(OLD_PREFIX) :]
		if new in done:
			continue
		frappe.get_doc({"doctype": "Patch Log", "patch": new}).insert(ignore_permissions=True)
