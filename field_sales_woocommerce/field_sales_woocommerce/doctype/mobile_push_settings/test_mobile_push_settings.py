# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestMobilePushSettings(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_push_needs_a_service_account_key(self):
		settings = frappe.get_single("Mobile Push Settings")
		settings.enabled = 1
		settings.service_account_json = None
		settings.firebase_project_id = None
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_a_key_that_is_not_json_is_refused(self):
		settings = frappe.get_single("Mobile Push Settings")
		settings.service_account_json = "not a key"
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)
