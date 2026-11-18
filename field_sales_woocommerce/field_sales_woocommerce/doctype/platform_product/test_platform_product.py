# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

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


class TestPlatformProduct(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_deleting_a_product_removes_its_stock_rows(self):
		product = make_product()
		frappe.get_doc(
			{"doctype": "Product Stock", "product": product, "location_id": "1001", "available_qty": 5}
		).insert(ignore_permissions=True)
		frappe.delete_doc("Platform Product", product, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Product Stock", {"product": product}))
