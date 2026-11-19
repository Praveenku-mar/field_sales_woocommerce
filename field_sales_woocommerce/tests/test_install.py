# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
A fresh site runs ``after_install`` instead of the patches (Frappe marks them done on install). The
retailer login role must come out of it without desk access, or retailer logins become System
Users and every retailer-login action refuses them. The same file runs in every edition.
"""

import importlib

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
install = importlib.import_module(f"{APP}.install")
RETAILER_ROLE = "Field Sales Retailer"


class TestInstall(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_after_install_keeps_the_retailer_role_out_of_the_desk(self):
		# As a fresh site has it: the doctype permissions create the role with desk access.
		frappe.db.set_value("Role", RETAILER_ROLE, "desk_access", 1, update_modified=False)

		install.after_install()

		self.assertEqual(frappe.db.get_value("Role", RETAILER_ROLE, "desk_access"), 0)

	def test_hooked_up(self):
		self.assertIn(f"{APP}.install.after_install", frappe.get_hooks("after_install", app_name=APP))
