# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Retailer rules: contact checks, the login user, and what a rep may change. Same file in every edition."""

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


class TestRetailer(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})
		self.rep_user = self.user("retailer.rep@example.com", "Field Sales Rep")
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": self.rep_user}).insert(ignore_permissions=True).name
		other_user = self.user("retailer.other@example.com", "Field Sales Rep")
		self.other_rep = frappe.get_doc({"doctype": "Sales Rep", "user": other_user}).insert(ignore_permissions=True).name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def user(self, email, role):
		doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": "R", "send_welcome_email": 0})
		doc.insert(ignore_permissions=True)
		doc.add_roles(role)
		return doc.name

	def retailer(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Rule Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Salem",
				"sales_rep": self.rep,
				**values,
			}
		)

	def test_valid_retailer_saves(self):
		self.assertTrue(self.retailer().insert(ignore_permissions=True).name)

	def test_email_must_be_a_single_address(self):
		with self.assertRaises(frappe.ValidationError):
			self.retailer(email="a@example.com, b@example.com").insert(ignore_permissions=True)

	def test_coordinates_must_be_on_the_map(self):
		with self.assertRaises(frappe.ValidationError):
			self.retailer(latitude=120, longitude=80).insert(ignore_permissions=True)

	def test_staff_user_cannot_be_a_retailer_login(self):
		with self.assertRaises(frappe.ValidationError):
			self.retailer(user=self.rep_user).insert(ignore_permissions=True)

	def test_rep_cannot_move_a_retailer_to_another_rep(self):
		name = self.retailer().insert(ignore_permissions=True).name
		frappe.set_user(self.rep_user)
		doc = frappe.get_doc("Retailer", name)
		doc.sales_rep = self.other_rep
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_rep_cannot_move_a_set_location(self):
		name = self.retailer(latitude=11.66, longitude=78.15).insert(ignore_permissions=True).name
		frappe.set_user(self.rep_user)
		doc = frappe.get_doc("Retailer", name)
		doc.latitude = 12.5
		with self.assertRaises(frappe.PermissionError):
			doc.save()
