# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Payment history is limited to the user's team, like the orders it belongs to
(permissions.payment_transaction_query / payment_transaction_has_permission).
The same file runs in every Field Sales edition.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

APP = __name__.split(".", 1)[0]
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


class TestPaymentTransactionScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Everything rolls back after each test; no store pushes from these saves.
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})

		self.manager_a = self.make_rep("paytx.mgr.a@example.com", ["Field Sales Rep", "Sales Manager"])
		self.rep_a = self.make_rep("paytx.rep.a@example.com", ["Field Sales Rep"], reports_to=self.manager_a)
		self.manager_b = self.make_rep("paytx.mgr.b@example.com", ["Field Sales Rep", "Sales Manager"])
		self.rep_b = self.make_rep("paytx.rep.b@example.com", ["Field Sales Rep"], reports_to=self.manager_b)

		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Payment scope product",
				"external_product_id": "paytx-1",
				"external_variant_id": "paytx-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Payment Scope Stores",
				"phone": "9876543210",
				"address_line1": "7 Market Street",
				"city": "Salem",
				"sales_rep": self.rep_a,
			}
		).insert(ignore_permissions=True)
		self.retailer = retailer.name

		self.order_a = self.make_order(self.rep_a, "paytx-store-a")
		self.order_b = self.make_order(self.rep_b, "paytx-store-b")
		# Linked to the order, and a store checkout row known only by the store order id.
		self.tx_a = self.make_tx("paytx:a", reference_order=self.order_a)
		self.tx_a_store = self.make_tx("paytx:a-store", store_order_id="paytx-store-a")
		self.tx_b = self.make_tx("paytx:b", reference_order=self.order_b)
		self.tx_b_store = self.make_tx("paytx:b-store", store_order_id="paytx-store-b")
		self.tx_orphan = self.make_tx("paytx:orphan")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_rep(self, email, roles, reports_to=None):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(*roles)
		return (
			frappe.get_doc({"doctype": "Sales Rep", "user": email, "reports_to": reports_to})
			.insert(ignore_permissions=True)
			.name
		)

	def make_order(self, sales_rep, external_order_id):
		name = (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer,
					"sales_rep": sales_rep,
					"order_date": nowdate(),
					"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value("Field Order", name, "external_order_id", external_order_id)
		return name

	def make_tx(self, key, **values):
		return (
			frappe.get_doc(
				{
					"doctype": "Payment Transaction",
					"transaction_key": key,
					"transaction_type": "Payment",
					"status": "Captured",
					"source": "Store Checkout",
					"amount": 100,
					**values,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def visible_to(self, user):
		frappe.set_user(user)
		try:
			return set(frappe.get_list("Payment Transaction", pluck="name", limit_page_length=0))
		finally:
			frappe.set_user("Administrator")

	def can_read(self, user, name):
		frappe.set_user(user)
		try:
			return frappe.has_permission("Payment Transaction", "read", name)
		finally:
			frappe.set_user("Administrator")

	# -- tests -------------------------------------------------------------

	def test_manager_lists_only_own_teams_payments(self):
		mine = {self.tx_a, self.tx_a_store}
		others = {self.tx_b, self.tx_b_store, self.tx_orphan}
		visible = self.visible_to(self.manager_a)
		self.assertTrue(mine <= visible)
		self.assertFalse(others & visible)

	def test_manager_cannot_open_another_teams_payment(self):
		self.assertTrue(self.can_read(self.manager_a, self.tx_a))
		self.assertTrue(self.can_read(self.manager_a, self.tx_a_store))
		self.assertFalse(self.can_read(self.manager_a, self.tx_b))
		self.assertFalse(self.can_read(self.manager_a, self.tx_b_store))
		self.assertFalse(self.can_read(self.manager_a, self.tx_orphan))

	def test_other_manager_sees_their_own_side(self):
		visible = self.visible_to(self.manager_b)
		self.assertTrue({self.tx_b, self.tx_b_store} <= visible)
		self.assertFalse({self.tx_a, self.tx_a_store} & visible)

	def test_admin_sees_everything(self):
		every = {self.tx_a, self.tx_a_store, self.tx_b, self.tx_b_store, self.tx_orphan}
		self.assertTrue(every <= self.visible_to("Administrator"))
		self.assertTrue(self.can_read("Administrator", self.tx_b))
