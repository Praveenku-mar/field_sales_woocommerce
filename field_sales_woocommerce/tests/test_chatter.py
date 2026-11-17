# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The record's history (chatter.py): notes, field changes and messages, and Log note. The same
file runs in every Field Sales edition.
"""

import importlib

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
chatter = importlib.import_module(f"{APP}.chatter")
get_chatter, post_note = chatter.get_chatter, chatter.post_note
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


class TestChatter(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Chatter test product",
				"external_product_id": "chat-1",
				"external_variant_id": "chat-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _user(self, email, role):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			user.insert(ignore_permissions=True)
		else:
			user = frappe.get_doc("User", email)
		if role:
			user.add_roles(role)
		return email

	def test_note_is_stored_escaped_and_returned_as_plain_text(self):
		item = post_note("Platform Product", self.product.name, "<b>Restock</b> soon\nsecond line")
		self.assertEqual(item["kind"], "note")
		self.assertEqual(item["text"], "<b>Restock</b> soon\nsecond line")
		stored = frappe.db.get_value(
			"Comment", {"reference_doctype": "Platform Product", "reference_name": self.product.name, "comment_type": "Comment"}, "content"
		)
		self.assertNotIn("<b>", stored)
		timeline = get_chatter("Platform Product", self.product.name)
		note = next(i for i in timeline["items"] if i["kind"] == "note")
		self.assertEqual(note["text"], "<b>Restock</b> soon\nsecond line")
		self.assertTrue(note["by_name"])
		self.assertTrue(timeline["can_post"])

	def test_field_changes_show_with_their_labels(self):
		self.product.price = 125
		# Frappe records versions in tests only when asked.
		self.product.save(ignore_permissions=True, ignore_version=False)
		change = next(i for i in get_chatter("Platform Product", self.product.name)["items"] if i["kind"] == "change")
		price = next(c for c in change["changes"] if c["label"] == "Price")
		# Versions keep the values as the desk shows them (e.g. "₹ 100.00").
		self.assertIn("100", price["from"])
		self.assertIn("125", price["to"])
		self.assertNotIn("modified", [c["label"].lower() for c in change["changes"]])

	def test_newest_first(self):
		post_note("Platform Product", self.product.name, "first")
		post_note("Platform Product", self.product.name, "second")
		notes = [i["text"] for i in get_chatter("Platform Product", self.product.name)["items"] if i["kind"] == "note"]
		self.assertEqual(notes[:2], ["second", "first"])

	def test_empty_or_too_long_note_is_refused(self):
		self.assertRaises(frappe.ValidationError, post_note, "Platform Product", self.product.name, "   ")
		self.assertRaises(frappe.ValidationError, post_note, "Platform Product", self.product.name, "x" * 5001)

	def test_other_doctypes_are_refused(self):
		self.assertRaises(frappe.PermissionError, get_chatter, "User", "Administrator")
		self.assertRaises(frappe.PermissionError, post_note, "User", "Administrator", "hello")

	def test_customer_login_cannot_read_or_post(self):
		user = self._user("chatter.shop@example.com", "Field Sales Retailer")
		frappe.set_user(user)
		self.assertRaises(frappe.PermissionError, get_chatter, "Platform Product", self.product.name)
		self.assertRaises(frappe.PermissionError, post_note, "Platform Product", self.product.name, "hi")

	def test_user_without_app_access_is_refused(self):
		user = self._user("chatter.nobody@example.com", None)
		frappe.set_user(user)
		self.assertRaises(frappe.PermissionError, get_chatter, "Platform Product", self.product.name)
