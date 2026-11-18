# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestStoreConnection(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_a_location_cannot_be_listed_twice(self):
		settings = frappe.get_single("Store Connection")
		settings.set("locations", [])
		for _i in range(2):
			settings.append("locations", {"location_id": "1001", "location_name": "Main", "sell_from": 1})
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)
