# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Importing the store's customers as Shops (sync/customers.py import_customers). The same file
runs in every Field Sales edition; each connector's customer mapping is tested below it.
"""

import importlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
customers = importlib.import_module(f"{APP}.sync.customers")
api = importlib.import_module(f"{APP}.api")


def customer(cid, **values):
	base = {
		"external_customer_id": cid,
		"email": None,
		"first_name": "",
		"last_name": "",
		"company": None,
		"phone": None,
		"address_line1": None,
		"address_line2": None,
		"city": None,
		"state": None,
		"pincode": None,
		"country_code": "IN",
	}
	return {**base, **values}


class TestCustomerImport(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.conn = MagicMock()
		self.conn.settings.platform = frappe.db.get_single_value("Store Connection", "platform") or "Odoo"
		p = patch.object(customers, "get_connector", return_value=self.conn)
		p.start()
		self.addCleanup(p.stop)
		push = patch.object(customers, "enqueue_push")
		self.push = push.start()
		self.addCleanup(push.stop)
		# The import commits after each page; here everything rolls back with the test.
		commit = patch.object(customers.frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def pages(self, *pages):
		answers = [{"items": list(items), "next_cursor": str(i + 1) if i < len(pages) - 1 else None} for i, items in enumerate(pages)]
		self.conn.get_customers.side_effect = answers

	def retailer(self, external_id):
		return frappe.db.get_value(
			"Retailer",
			{"external_customer_id": external_id},
			["name", "retailer_name", "owner_name", "email", "phone", "city", "sales_rep", "sync_status", "status"],
			as_dict=True,
		)

	def test_new_customers_become_shops_across_pages(self):
		self.pages(
			[customer("ci-1", company="Murugan Stores", first_name="Ravi", last_name="K", email="RAVI@Example.com", city="Salem")],
			[customer("ci-2", first_name="Asha", last_name="M")],
		)
		summary = customers.import_customers()
		self.assertEqual((summary["created"], summary["failed"]), (2, 0))
		shop = self.retailer("ci-1")
		self.assertEqual((shop.retailer_name, shop.owner_name, shop.email, shop.city), ("Murugan Stores", "Ravi K", "ravi@example.com", "Salem"))
		# Synced with the store, active, no rep yet (a manager assigns one), never pushed back.
		self.assertEqual((shop.sync_status, shop.status, shop.sales_rep), ("Synced", "Active", None))
		self.assertEqual(self.retailer("ci-2").retailer_name, "Asha M")
		self.push.assert_not_called()
		log = frappe.get_last_doc("Sync Log", filters={"operation": "Customer Sync", "reference_doctype": "Retailer"})
		self.assertEqual(frappe.parse_json(log.response_payload)["created"], 2)

	def test_existing_shop_is_linked_by_email_and_only_blanks_filled(self):
		existing = frappe.get_doc(
			{"doctype": "Retailer", "retailer_name": "Typed In App", "email": "shop@example.com", "city": "Erode", "address_line1": "1 Main Rd", "phone": "9876543210"}
		)
		existing.flags.ignore_mandatory = True
		existing.flags.ignore_sync = True
		existing.insert(ignore_permissions=True)
		self.pages([customer("ci-3", company="Store Name", email="shop@example.com", city="Salem", pincode="636001")])
		self.assertEqual(customers.import_customers()["linked"], 1)
		doc = frappe.get_doc("Retailer", existing.name)
		# What was typed in the app wins; empty fields are filled; it is now linked.
		self.assertEqual((doc.retailer_name, doc.city, doc.pincode, doc.external_customer_id), ("Typed In App", "Erode", "636001", "ci-3"))

	def test_second_import_changes_nothing(self):
		self.pages([customer("ci-4", company="Repeat Shop")])
		customers.import_customers()
		self.pages([customer("ci-4", company="Repeat Shop")])
		self.assertEqual(customers.import_customers()["unchanged"], 1)

	def test_bad_phone_is_dropped_and_placeholder_is_skipped(self):
		self.pages(
			[
				customer("ci-5", company="Phone Shop", phone="not-a-phone"),
				customer("ci-6", company="Ghost", email="fo-0001@customers.fieldsales.local"),
			]
		)
		summary = customers.import_customers()
		self.assertEqual((summary["created"], summary["skipped"]), (1, 1))
		self.assertFalse(self.retailer("ci-5").phone)
		self.assertIsNone(self.retailer("ci-6"))

	def test_store_error_is_logged_and_raised(self):
		self.conn.get_customers.side_effect = RuntimeError("store down")
		self.assertRaises(RuntimeError, customers.import_customers)
		log = frappe.get_last_doc("Sync Log", filters={"operation": "Customer Sync", "reference_doctype": "Retailer"})
		self.assertEqual(log.status, "Failed")

	def test_only_managers_start_an_import(self):
		with patch.object(api, "get_connector"), patch.object(api, "enqueue_import", return_value=True):
			self.assertTrue(api.enqueue_customer_import()["queued"])
		rep = "import.rep@example.com"
		if not frappe.db.exists("User", rep):
			frappe.get_doc({"doctype": "User", "email": rep, "first_name": "Import Rep", "send_welcome_email": 0}).insert(ignore_permissions=True)
		frappe.get_doc("User", rep).add_roles("Field Sales Rep")
		frappe.set_user(rep)
		self.assertRaises(frappe.PermissionError, api.enqueue_customer_import)


class TestWooCommerceCustomers(IntegrationTestCase):
	def test_mapping_prefers_the_billing_street(self):
		from field_sales_woocommerce.connectors.woocommerce import _normalize_customer

		c = _normalize_customer(
			{
				"id": 5,
				"email": "w@example.com",
				"first_name": "Wren",
				"last_name": "",
				"billing": {"company": "Wren Mart", "address_1": "3 Market Rd", "city": "Salem", "postcode": "636001", "country": "in", "phone": "9876543210"},
				"shipping": {"address_1": "Elsewhere"},
			}
		)
		self.assertEqual(
			(c["external_customer_id"], c["company"], c["address_line1"], c["country_code"], c["phone"]),
			("5", "Wren Mart", "3 Market Rd", "IN", "9876543210"),
		)
