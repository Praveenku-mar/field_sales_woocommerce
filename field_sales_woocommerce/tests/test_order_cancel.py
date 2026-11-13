# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Cancelling a Field Order that reached the store, and its commission afterwards."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, nowdate

from field_sales_woocommerce.commission import engine
from field_sales_woocommerce.connectors import ConnectorError
from field_sales_woocommerce.field_sales_woocommerce.doctype.field_order import field_order as field_order_module

PRICE = 100


class TestOrderCancel(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": "WooCommerce"})
		frappe.db.delete("Store Location", {"parenttype": "Store Connection"})

		user = frappe.get_doc(
			{"doctype": "User", "email": "cancel.rep@example.com", "first_name": "Cancel", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True).name
		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Cancel Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Salem",
				"sales_rep": self.rep,
			}
		).insert(ignore_permissions=True)
		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "WooCommerce",
				"product_title": "Cancel product",
				"external_product_id": "98100",
				"external_variant_id": "9810",
				"status": "Active",
				"price": PRICE,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": retailer.name,
				"sales_rep": self.rep,
				"order_date": nowdate(),
				"items": [{"product": product.name, "qty": 1, "rate": PRICE}],
			}
		).insert(ignore_permissions=True)
		self.order = order.name
		# Submitted and synced as the push leaves it, without running a push.
		frappe.db.set_value("Field Order", self.order, {"docstatus": 1, "sync_status": "Synced"})

		self.conn = MagicMock()
		self.conn.get_order.return_value = {"external_order_id": "O1", "cancelled": False}
		self.conn.delete_draft.return_value = None
		for target, value in (
			("get_connector", MagicMock(return_value=self.conn)),
			("enqueue_create_entries", MagicMock()),
		):
			patcher = patch.object(field_order_module, target, value)
			setattr(self, target, patcher.start())
			self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def set_store(self, **values):
		frappe.db.set_value("Field Order", self.order, values)

	def cancel(self):
		frappe.get_doc("Field Order", self.order).cancel()

	def docstatus(self):
		return frappe.db.get_value("Field Order", self.order, "docstatus")

	# -- not on the store ------------------------------------------------------

	def test_order_never_sent_cancels_without_asking_the_store(self):
		self.set_store(sync_status="Failed")
		self.cancel()
		self.assertEqual(self.docstatus(), 2)
		self.get_connector.assert_not_called()
		self.enqueue_create_entries.assert_not_called()

	def test_order_being_pushed_cannot_be_cancelled(self):
		self.set_store(sync_status="Pending")
		with self.assertRaisesRegex(frappe.ValidationError, "being sent to the store"):
			self.cancel()
		self.assertEqual(self.docstatus(), 1)

	# -- confirmed store order ---------------------------------------------------

	def test_open_store_order_must_be_cancelled_there_first(self):
		self.set_store(external_order_id="O1", store_order_name="#1001")
		with self.assertRaisesRegex(frappe.ValidationError, "#1001"):
			self.cancel()
		self.assertEqual(self.docstatus(), 1)
		self.enqueue_create_entries.assert_not_called()

	def test_cancelled_store_order_cancels_and_reconciles_commission(self):
		self.set_store(external_order_id="O1")
		self.conn.get_order.return_value = {"external_order_id": "O1", "cancelled": True}
		self.cancel()
		self.assertEqual(self.docstatus(), 2)
		self.enqueue_create_entries.assert_called_once_with(self.order)

	def test_store_order_gone_from_the_store_cancels(self):
		self.set_store(external_order_id="O1")
		self.conn.get_order.return_value = {}
		self.cancel()
		self.assertEqual(self.docstatus(), 2)

	def test_unreachable_store_refuses(self):
		self.set_store(external_order_id="O1")
		self.conn.get_order.side_effect = ConnectorError("Store down")
		with self.assertRaisesRegex(frappe.ValidationError, "Store down"):
			self.cancel()
		self.assertEqual(self.docstatus(), 1)

	def test_store_connection_off_refuses(self):
		self.set_store(external_order_id="O1")
		self.get_connector.side_effect = frappe.ValidationError("Store Connection is disabled")
		with self.assertRaisesRegex(frappe.ValidationError, "Store Connection is off"):
			self.cancel()
		self.assertEqual(self.docstatus(), 1)

	# -- Draft mode (a real `pending` order on WooCommerce, never a draft) ---------

	def test_pending_order_must_be_cancelled_on_woocommerce_first(self):
		self.set_store(external_order_id="O1", store_order_name="#1001")
		self.conn.get_order.return_value = {"external_order_id": "O1", "status": "pending", "cancelled": False}
		with self.assertRaisesRegex(frappe.ValidationError, "Cancel it there first"):
			self.cancel()
		self.assertEqual(self.docstatus(), 1)
		self.conn.delete_draft.assert_not_called()
		self.assert_store_only_read()

	def test_order_cancelled_on_woocommerce_is_never_touched_there(self):
		self.set_store(external_order_id="O1")
		self.conn.get_order.return_value = {"external_order_id": "O1", "cancelled": True}
		self.cancel()
		self.assertEqual(self.docstatus(), 2)
		self.assert_store_only_read()

	def assert_store_only_read(self):
		"""Option (b): the app reads the store order, never cancels or deletes anything there."""
		self.assertLessEqual({c[0] for c in self.conn.method_calls}, {"get_order", "delete_draft"})

	# -- commission after the cancel ---------------------------------------------

	def test_reconcile_follows_a_cancelled_order_without_new_entries(self):
		self.set_store(external_order_id="O1", docstatus=2)
		frappe.db.set_single_value("Store Connection", "enable_commission", 1)
		with (
			patch.object(engine, "get_connector", return_value=self.conn),
			patch.object(engine._Context, "create_missing_entries") as create,
			patch.object(engine._Context, "apply_state", return_value=(1, 0)) as apply_state,
		):
			result = engine.reconcile_order("O1")
		self.assertEqual(result["field_order"], self.order)
		create.assert_not_called()
		apply_state.assert_called_once()

	# -- push interleaving defensive check (M2) ----------------------------------

	def test_order_cancelled_during_push_fails_defensively(self):
		from field_sales_woocommerce.sync import orders as sync_orders

		self.set_store(docstatus=1, sync_status="Draft")
		self.conn.settings.platform = "WooCommerce"
		self.conn.settings.order_mode = "Confirmed"

		def cancel_during_create(doc):
			frappe.db.set_value("Field Order", doc.name, "docstatus", 2)
			return {"order_id": "STORE-O99", "order_name": "#99099"}

		self.conn.create_order.side_effect = cancel_during_create

		with (
			patch.object(sync_orders, "get_connector", return_value=self.conn),
			patch.object(sync_orders, "_ensure_store_customer"),
			patch.object(sync_orders, "enqueue_create_entries") as create_entries,
			patch.object(sync_orders, "enqueue_order_confirmation") as confirmation,
			patch.object(sync_orders, "notify_order_synced") as synced,
			patch.object(sync_orders, "enqueue_mark_store_paid") as mark_paid,
			patch.object(frappe.db, "commit"),
		):
			sync_orders.push_order(self.order)

		doc = frappe.get_doc("Field Order", self.order)
		self.assertEqual(doc.sync_status, "Failed")
		self.assertEqual(doc.external_order_id, "STORE-O99")
		self.assertEqual(doc.store_order_name, "#99099")
		self.assertIn("Cancelled while being sent", doc.sync_error)
		self.assertIn("#99099", doc.sync_error)

		log = frappe.db.get_value(
			"Sync Log",
			{"reference_name": self.order, "error_code": "CANCELLED_DURING_PUSH"},
			["status", "error_code", "external_id"],
			as_dict=True,
		)
		self.assertIsNotNone(log)
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.external_id, "STORE-O99")

		create_entries.assert_not_called()
		confirmation.assert_not_called()
		synced.assert_not_called()
		mark_paid.assert_not_called()

	# -- draft linker window -----------------------------------------------------

	def test_draft_linker_skips_drafts_pushed_long_ago(self):
		from field_sales_woocommerce.sync import orders as sync_orders

		frappe.db.set_single_value("Store Connection", "enabled", 1)
		frappe.db.set_value(
			"Field Order",
			{"docstatus": 1, "sync_status": "Synced", "external_draft_id": ("is", "set"), "name": ("!=", self.order)},
			"last_sync_attempt",
			add_days(now_datetime(), -(sync_orders.DRAFT_LINK_DAYS + 1)),
			update_modified=False,
		)
		self.conn.get_completed_drafts.return_value = {}
		with patch.object(sync_orders, "get_connector", return_value=self.conn):
			self.set_store(external_draft_id="D-OLD", last_sync_attempt=add_days(now_datetime(), -(sync_orders.DRAFT_LINK_DAYS + 1)))
			sync_orders.link_completed_drafts()
			self.conn.get_completed_drafts.assert_not_called()

			self.set_store(external_draft_id="D-NEW", last_sync_attempt=now_datetime())
			sync_orders.link_completed_drafts()
			self.conn.get_completed_drafts.assert_called_once_with(["D-NEW"])
