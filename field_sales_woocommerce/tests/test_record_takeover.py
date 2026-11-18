# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
No taking another team's record by saving it with your own Sales Rep
(permissions.check_owner_change). ``frappe.client.save`` is the path the desk and scripts use:
it checks permission on the record as sent only. The same file runs in every Field Sales edition.
"""

import json

import frappe
import frappe.client
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

APP = __name__.split(".", 1)[0]
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


class TestRecordTakeover(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Everything rolls back after each test; no store pushes from these saves.
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})

		# Team A: manager A with rep A. Team B: manager B with rep B.
		self.manager_a = self.make_rep("takeover.mgr.a@example.com", ["Field Sales Rep", "Sales Manager"])
		self.rep_a = self.make_rep("takeover.rep.a@example.com", ["Field Sales Rep"], reports_to=self.manager_a)
		self.rep_a2 = self.make_rep("takeover.rep.a2@example.com", ["Field Sales Rep"], reports_to=self.manager_a)
		self.manager_b = self.make_rep("takeover.mgr.b@example.com", ["Field Sales Rep", "Sales Manager"])
		self.rep_b = self.make_rep("takeover.rep.b@example.com", ["Field Sales Rep"], reports_to=self.manager_b)

		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Takeover product",
				"external_product_id": "takeover-1",
				"external_variant_id": "takeover-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.retailer = self.make_retailer("Takeover Stores", self.rep_a)

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

	def make_retailer(self, name, sales_rep):
		return (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": name,
					"phone": "9876543210",
					"address_line1": "7 Market Street",
					"city": "Salem",
					"sales_rep": sales_rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_visit(self, sales_rep):
		return (
			frappe.get_doc(
				{"doctype": "Visit", "sales_rep": sales_rep, "retailer": self.retailer, "visit_date": nowdate()}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_order(self, sales_rep):
		return (
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

	def client_save(self, user, doctype, name, **changes):
		"""Save the record as ``user`` through frappe.client.save, with ``changes`` applied."""
		data = json.loads(frappe.as_json(frappe.get_doc(doctype, name).as_dict()))
		data.update(changes)
		frappe.set_user(user)
		try:
			return frappe.client.save(data)
		finally:
			frappe.set_user("Administrator")

	def rep_of(self, doctype, name):
		return frappe.db.get_value(doctype, name, "sales_rep")

	# -- refused: another team's record ----------------------------------------

	def test_rep_cannot_take_another_teams_visit(self):
		visit = self.make_visit(self.rep_a)
		with self.assertRaises(frappe.PermissionError):
			self.client_save(self.rep_b, "Visit", visit, sales_rep=self.rep_b)
		self.assertEqual(self.rep_of("Visit", visit), self.rep_a)

	def test_rep_cannot_take_another_teams_draft_order(self):
		order = self.make_order(self.rep_a)
		with self.assertRaises(frappe.PermissionError):
			self.client_save(self.rep_b, "Field Order", order, sales_rep=self.rep_b)
		self.assertEqual(self.rep_of("Field Order", order), self.rep_a)

	def test_manager_cannot_take_another_teams_retailer(self):
		with self.assertRaises(frappe.PermissionError):
			self.client_save(self.manager_b, "Retailer", self.retailer, sales_rep=self.manager_b)
		self.assertEqual(self.rep_of("Retailer", self.retailer), self.rep_a)

	def test_manager_cannot_take_another_teams_visit(self):
		visit = self.make_visit(self.rep_a)
		with self.assertRaises(frappe.PermissionError):
			self.client_save(self.manager_b, "Visit", visit, sales_rep=self.rep_b)
		self.assertEqual(self.rep_of("Visit", visit), self.rep_a)

	# -- still allowed -----------------------------------------------------

	def test_manager_reassigns_within_own_team(self):
		visit = self.make_visit(self.rep_a)
		self.client_save(self.manager_a, "Visit", visit, sales_rep=self.rep_a2)
		self.assertEqual(self.rep_of("Visit", visit), self.rep_a2)

		order = self.make_order(self.rep_a)
		self.client_save(self.manager_a, "Field Order", order, sales_rep=self.rep_a2)
		self.assertEqual(self.rep_of("Field Order", order), self.rep_a2)

		self.client_save(self.manager_a, "Retailer", self.retailer, sales_rep=self.rep_a2)
		self.assertEqual(self.rep_of("Retailer", self.retailer), self.rep_a2)

	def test_rep_edits_own_record(self):
		visit = self.make_visit(self.rep_a)
		self.client_save(self.rep_a, "Visit", visit, notes="Called ahead")
		self.assertEqual(frappe.db.get_value("Visit", visit, "notes"), "Called ahead")

	def test_unassigned_retailer_can_be_claimed(self):
		retailer = self.make_retailer("Nobody's Stores", None)
		frappe.db.set_value("Retailer", retailer, "sales_rep", None)
		self.client_save(self.rep_b, "Retailer", retailer, sales_rep=self.rep_b)
		self.assertEqual(self.rep_of("Retailer", retailer), self.rep_b)

	def test_admin_moves_any_record(self):
		visit = self.make_visit(self.rep_a)
		self.client_save("Administrator", "Visit", visit, sales_rep=self.rep_b)
		self.assertEqual(self.rep_of("Visit", visit), self.rep_b)

	def test_server_code_with_ignore_permissions_is_not_limited(self):
		frappe.set_user(self.rep_b)
		try:
			doc = frappe.get_doc("Visit", self.make_visit(self.rep_a))
			doc.sales_rep = self.rep_b
			doc.save(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.rep_of("Visit", doc.name), self.rep_b)
