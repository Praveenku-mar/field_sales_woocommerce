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


class TestFieldOrder(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})
		self.rep = make_rep()
		self.retailer = make_retailer(self.rep)
		self.product = make_product()

	def tearDown(self):
		frappe.db.rollback()

	def order(self, items):
		return frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer,
				"sales_rep": self.rep,
				"order_date": nowdate(),
				"items": items,
			}
		)

	def test_totals_are_computed(self):
		doc = self.order([{"product": self.product, "qty": 2, "rate": 100}]).insert(ignore_permissions=True)
		self.assertEqual(doc.items[0].amount, 200)
		self.assertEqual(doc.net_total, 200)
		self.assertEqual(doc.total_qty, 2)

	def test_a_product_is_listed_once(self):
		line = {"product": self.product, "qty": 1, "rate": 100}
		with self.assertRaises(frappe.ValidationError):
			self.order([line, dict(line)]).insert(ignore_permissions=True)

	def test_quantity_must_be_positive(self):
		with self.assertRaises(frappe.ValidationError):
			self.order([{"product": self.product, "qty": 0, "rate": 100}]).insert(ignore_permissions=True)

	def test_quantity_must_be_whole(self):
		# The store takes whole units: 2.5 was billed here and sent as 2.
		with self.assertRaises(frappe.ValidationError):
			self.order([{"product": self.product, "qty": 2.5, "rate": 100}]).insert(ignore_permissions=True)
		self.order([{"product": self.product, "qty": 2, "rate": 100}]).insert(ignore_permissions=True)

	def test_only_active_products_can_be_ordered(self):
		for status in ("Draft", "Archived"):
			frappe.db.set_value("Platform Product", self.product, "status", status)
			with self.assertRaises(frappe.ValidationError):
				self.order([{"product": self.product, "qty": 1, "rate": 100}]).insert(ignore_permissions=True)

	def test_a_product_archived_after_it_was_added_stops_the_submit(self):
		doc = self.order([{"product": self.product, "qty": 1, "rate": 100}]).insert(ignore_permissions=True)
		frappe.db.set_value("Platform Product", self.product, "status", "Archived")
		with self.assertRaises(frappe.ValidationError):
			doc.submit()
