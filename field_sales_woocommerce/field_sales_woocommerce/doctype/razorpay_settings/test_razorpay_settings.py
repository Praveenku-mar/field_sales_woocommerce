# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestRazorpaySettings(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_callback_url_must_be_a_web_address(self):
		settings = frappe.get_single("Razorpay Settings")
		for bad in ("ftp://shop.example.com/paid", "https://", "https://shop.example.com/pa id"):
			settings.callback_url = bad
			with self.subTest(url=bad), self.assertRaises(frappe.ValidationError):
				settings.save(ignore_permissions=True)

	def test_values_are_trimmed_and_webhook_url_is_set(self):
		settings = frappe.get_single("Razorpay Settings")
		settings.callback_url = "  https://shop.example.com/paid  "
		settings.key_id = "  rzp_test_key  "
		settings.save(ignore_permissions=True)
		self.assertEqual(settings.callback_url, "https://shop.example.com/paid")
		self.assertEqual(settings.key_id, "rzp_test_key")
		self.assertTrue(settings.webhook_url)
