# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Retry Sync for several orders in one call (api.retry_sync_orders, the list view's action): every
order gets retry_sync's checks, and nothing is queued unless all pass. The same file runs in every
edition.
"""

import importlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

APP = __name__.split(".", 1)[0]
api = importlib.import_module(f"{APP}.api")
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]


class TestRetrySyncOrders(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})
		user = frappe.get_doc(
			{"doctype": "User", "email": "retry.rep@example.com", "first_name": "Retry", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True).name
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Retry Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Salem",
				"sales_rep": self.rep,
			}
		).insert(ignore_permissions=True).name
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Retry product",
				"external_product_id": "retry-1",
				"external_variant_id": "retry-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def order(self, sync_status):
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
		frappe.db.set_value("Field Order", name, {"docstatus": 1, "sync_status": sync_status}, update_modified=False)
		return name

	def test_failed_orders_are_queued_in_one_call(self):
		first, second = self.order("Failed"), self.order("Failed")
		with patch.object(api, "get_connector"), patch.object(api, "enqueue_push_order") as enqueue:
			result = api.retry_sync_orders([first, second, first])
		self.assertEqual(result["queued"], 2)
		self.assertEqual(sorted(c.args[0] for c in enqueue.call_args_list), sorted([first, second]))

	def test_nothing_is_queued_when_one_order_has_not_failed(self):
		failed, synced = self.order("Failed"), self.order("Synced")
		with patch.object(api, "get_connector"), patch.object(api, "enqueue_push_order") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				api.retry_sync_orders(frappe.as_json([failed, synced]))
		enqueue.assert_not_called()

	def test_another_reps_order_is_refused(self):
		other_user = frappe.get_doc(
			{"doctype": "User", "email": "retry.other@example.com", "first_name": "Other", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		other_user.add_roles("Field Sales Rep")
		frappe.get_doc({"doctype": "Sales Rep", "user": other_user.name}).insert(ignore_permissions=True)
		order = self.order("Failed")
		frappe.set_user(other_user.name)
		with patch.object(api, "get_connector"), patch.object(api, "enqueue_push_order") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				api.retry_sync_orders([order])
		enqueue.assert_not_called()
