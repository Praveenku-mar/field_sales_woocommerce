# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_first_day, get_last_day, nowdate

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


class TestCommissionStatement(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_default("currency", "INR")
		self.rep = make_rep()
		self.start = get_first_day(nowdate())

	def tearDown(self):
		frappe.db.rollback()

	def statement(self, from_build=True, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Commission Statement",
				"sales_rep": self.rep,
				"period_start": self.start,
				"period_end": get_last_day(self.start),
				**values,
			}
		)
		# Statements are made by the builder (commission.statements.build_statement), which sets this.
		doc.flags.from_build = from_build
		return doc

	def test_statements_come_from_the_builder(self):
		with self.assertRaises(frappe.PermissionError):
			self.statement(from_build=False).insert(ignore_permissions=True)

	def test_period_end_is_not_before_start(self):
		with self.assertRaises(frappe.ValidationError):
			self.statement(period_end=add_days(self.start, -1)).insert(ignore_permissions=True)

	def test_one_statement_per_rep_and_period(self):
		self.statement().insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			self.statement().insert(ignore_permissions=True)
