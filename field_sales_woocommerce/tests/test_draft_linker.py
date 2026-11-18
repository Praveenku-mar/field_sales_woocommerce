# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The draft linker (sync.orders.link_completed_drafts) keeps going when one draft fails: that draft is
rolled back and logged, and the drafts after it are still linked. The same file runs in every edition.
"""

import importlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

APP = __name__.split(".", 1)[0]
sync_orders = importlib.import_module(f"{APP}.sync.orders")
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[
	APP
]


class TestDraftLinker(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.started = now_datetime()
		frappe.db.set_single_value("Store Connection", {"enabled": 1, "platform": PLATFORM})
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "linker.rep@example.com",
				"first_name": "Linker",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		self.rep = (
			frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True).name
		)
		self.retailer = (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Linker Stores",
					"phone": "9876543210",
					"address_line1": "1 Main Road",
					"city": "Salem",
					"sales_rep": self.rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Linker product",
				"external_product_id": "linker-1",
				"external_variant_id": "linker-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		# Only this test's drafts are open: older test data must not join the run.
		frappe.db.set_value(
			"Field Order",
			{"external_draft_id": ("is", "set")},
			"last_sync_attempt",
			add_to_date(now_datetime(), days=-400),
		)
		# Newest attempt first: the failing draft is listed before the good one.
		self.failing = self.synced_draft("D-FAIL", minutes_ago=1)
		self.good = self.synced_draft("D-GOOD", minutes_ago=5)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def synced_draft(self, draft_id, minutes_ago):
		name = (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer,
					"sales_rep": self.rep,
					"order_date": nowdate(),
					"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value(
			"Field Order",
			name,
			{
				"docstatus": 1,
				"sync_status": "Synced",
				"external_draft_id": draft_id,
				"external_order_id": None,
				"last_sync_attempt": add_to_date(now_datetime(), minutes=-minutes_ago),
			},
			update_modified=False,
		)
		return name

	def test_one_failing_draft_does_not_stop_the_rest(self):
		conn = MagicMock()
		conn.settings.platform = PLATFORM
		conn.get_completed_drafts.return_value = {"D-FAIL": "9001", "D-GOOD": "9002"}

		def create_entries(field_order):
			if field_order == self.failing:
				raise RuntimeError("boom")

		with (
			patch.object(sync_orders, "get_connector", return_value=conn),
			patch.object(sync_orders, "enqueue_create_entries", side_effect=create_entries),
			# A store payment carried over from the draft (Shopify / Odoo only) is out of scope here.
			patch.object(sync_orders, "_carry_draft_payment", create=True),
			patch.object(sync_orders, "enqueue_order_confirmation"),
			patch.object(sync_orders, "notify_order_synced"),
			patch.object(sync_orders, "notify_sync_result"),
			# Keep the test's rows: the linker commits per draft and rolls back a failed one.
			patch.object(sync_orders.frappe.db, "commit"),
			patch.object(sync_orders.frappe.db, "rollback") as rollback,
		):
			sync_orders.link_completed_drafts()

		self.assertEqual(frappe.db.get_value("Field Order", self.good, "external_order_id"), "9002")
		# The connector gets a record it can log against (Sync Log needs doctype and name).
		passed = conn.set_order_metafields.call_args_list[-1].args[1]
		self.assertEqual((passed.doctype, passed.name), ("Field Order", self.good))
		rollback.assert_called_once()
		logged = frappe.get_all(
			"Error Log",
			filters={
				"method": f"Linking completed draft failed: {self.failing}",
				"creation": (">=", self.started),
			},
			pluck="name",
		)
		self.assertTrue(logged)
