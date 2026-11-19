# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

APP = __name__.split(".", 1)[0]
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


def make_rep(email="doctype.rep@example.com"):
	user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "Rep", "send_welcome_email": 0})
	user.insert(ignore_permissions=True)
	user.add_roles("Field Sales Rep")
	return frappe.get_doc({"doctype": "Sales Rep", "user": email}).insert(ignore_permissions=True).name


def make_retailer(sales_rep):
	return (
		frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Doctype Test Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Salem",
				"sales_rep": sales_rep,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_product(external_id="doctype-1"):
	return (
		frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Doctype test product",
				"external_product_id": external_id,
				"external_variant_id": f"{external_id}-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestCommissionEntry(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_default("currency", "INR")
		self.rep = make_rep()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def adjustment(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Commission Entry",
				"entry_type": "Adjustment",
				"beneficiary_type": "Rep",
				"sales_rep": self.rep,
				"status": "Earned",
				"posting_date": nowdate(),
				"earned_on": nowdate(),
				"currency": "INR",
				"amount": 50,
				**values,
			}
		)

	def test_adjustment_needs_a_reason(self):
		with self.assertRaises(frappe.ValidationError):
			self.adjustment(adjustment_reason="").insert(ignore_permissions=True)
		self.assertTrue(self.adjustment(adjustment_reason="Festival bonus").insert(ignore_permissions=True).name)

	def test_order_entries_come_from_the_engine_only(self):
		# A hand-made Order entry paid like store-earned commission, with no order and no reason.
		values = {
			"doctype": "Commission Entry",
			"entry_type": "Order",
			"beneficiary_type": "Rep",
			"sales_rep": self.rep,
			"status": "Earned",
			"posting_date": nowdate(),
			"earned_on": nowdate(),
			"currency": "INR",
			"amount": 50000,
			"base_amount": 500000,
		}
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(values).insert(ignore_permissions=True)  # Administrator included

		entry = frappe.get_doc(values)
		entry.flags.from_engine = True
		self.assertTrue(entry.insert(ignore_permissions=True).name)

	def test_a_rep_cannot_add_an_adjustment(self):
		frappe.set_user(frappe.db.get_value("Sales Rep", self.rep, "user"))
		with self.assertRaises(frappe.PermissionError):
			self.adjustment(adjustment_reason="Self bonus").insert(ignore_permissions=True)
