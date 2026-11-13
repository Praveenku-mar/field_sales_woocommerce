# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
patches.txt lists only this app's own patches, and each one exists.

The WooCommerce and Odoo editions were copied from the Shopify one with ``field_sales.patches.*``
left in patches.txt: they ran the Shopify app's code, and ``bench migrate`` fails on a bench
without it. The same file runs in field_sales, field_sales_woocommerce and field_sales_odoo.
"""

import importlib

import frappe
from frappe.modules.patch_handler import get_patches_from_app
from frappe.tests import UnitTestCase

APP = __name__.split(".", 1)[0]


class TestPatchesList(UnitTestCase):
	def test_every_patch_is_this_apps_own_and_exists(self):
		patches = get_patches_from_app(APP)
		self.assertTrue(patches)
		for patch in patches:
			with self.subTest(patch=patch):
				module = patch.split()[0]  # "app.patches.x #comment" or "execute:..." lines
				if module.startswith("execute:"):
					continue
				self.assertTrue(module.startswith(f"{APP}.patches."), "a patch of another app")
				self.assertTrue(callable(getattr(importlib.import_module(module), "execute", None)))

	def test_no_patch_is_listed_twice(self):
		patches = get_patches_from_app(APP)
		self.assertEqual(len(patches), len(set(patches)))
