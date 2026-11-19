# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Setup a brand-new site needs (hooks.py ``after_install``)."""


def after_install():
	"""
	Frappe records every patch in patches.txt as done on a fresh install without running it, so
	setup that existing sites got from a patch must run here too:

	- ``retailer_role``: the Field Sales Retailer role is created by the doctype permissions with
	  desk access on. Without this, a retailer's login became a System User with the Desk User role,
	  could open the Desk, and every retailer-login action refused it ("has roles beyond Field
	  Sales Retailer").
	"""
	from field_sales_woocommerce.patches.v1_0 import retailer_role

	retailer_role.execute()
