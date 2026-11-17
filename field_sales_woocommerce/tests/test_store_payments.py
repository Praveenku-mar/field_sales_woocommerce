# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
A payment made on the store marks the Field Order paid (payments/store_payments.py). The same
file runs in every Field Sales edition; each platform's own rule for "paid" is tested with its
connector.
"""

import importlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
store_payments = importlib.import_module(f"{APP}.payments.store_payments")
links = importlib.import_module(f"{APP}.tests.test_razorpay_links")


class TestStorePayments(links.LinkFixture, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Field Order", self.order.name, "external_order_id", "7001")
		self.total = frappe.db.get_value("Field Order", self.order.name, "grand_total")
		self.conn = MagicMock()
		notify = patch.object(store_payments, "notify_payment_received")
		self.notify = notify.start()
		self.addCleanup(notify.stop)

	def saved(self, *fields):
		return frappe.db.get_value("Field Order", self.order.name, list(fields), as_dict=True)

	def paid(self, amount, **extra):
		return {"amount": amount, "currency": "INR", "method": "Card", "reference": "txn_1", "paid_at": "2026-11-16T04:30:00Z", **extra}

	def history(self):
		return frappe.get_all(
			"Payment Transaction",
			filters={"reference_order": self.order.name, "transaction_key": "store:7001"},
			fields=["status", "amount", "source", "method", "reference"],
		)

	def test_paid_in_full_on_the_store(self):
		self.conn.get_store_payment.return_value = self.paid(self.total)
		result = store_payments.apply_store_payment(self.conn, "7001")
		self.assertEqual(result["payment_status"], "Paid")
		o = self.saved("payment_status", "amount_paid", "store_marked_paid_on", "paid_on")
		self.assertEqual((o.payment_status, o.amount_paid), ("Paid", self.total))
		self.assertIsNotNone(o.store_marked_paid_on)
		self.assertIsNotNone(o.paid_on)
		(row,) = self.history()
		self.assertEqual((row.status, row.source, row.method, row.reference), ("Captured", "Store Checkout", "Card", "txn_1"))
		self.notify.assert_called_once()

	def test_part_then_rest_updates_one_history_row(self):
		self.conn.get_store_payment.return_value = self.paid(100)
		store_payments.apply_store_payment(self.conn, "7001")
		self.assertEqual(self.saved("payment_status").payment_status, "Partially Paid")
		self.conn.get_store_payment.return_value = self.paid(self.total)
		store_payments.apply_store_payment(self.conn, "7001")
		self.assertEqual(self.saved("payment_status").payment_status, "Paid")
		(row,) = self.history()
		self.assertEqual(row.amount, self.total)

	def test_repeat_changes_nothing(self):
		self.conn.get_store_payment.return_value = self.paid(100)
		store_payments.apply_store_payment(self.conn, "7001")
		self.assertIn("Ignored", store_payments.apply_store_payment(self.conn, "7001")["note"])
		self.notify.assert_called_once()

	def test_more_than_the_order_is_capped(self):
		self.conn.get_store_payment.return_value = self.paid(self.total + 500)
		store_payments.apply_store_payment(self.conn, "7001")
		self.assertEqual(self.saved("amount_paid").amount_paid, self.total)

	def test_already_paid_in_agamsale_is_left_alone(self):
		frappe.db.set_value("Field Order", self.order.name, {"payment_status": "Paid", "amount_paid": self.total})
		self.assertIn("Ignored", store_payments.apply_store_payment(self.conn, "7001")["note"])
		self.conn.get_store_payment.assert_not_called()

	def test_unpaid_or_unknown_store_order(self):
		self.conn.get_store_payment.return_value = None
		self.assertIn("Ignored", store_payments.apply_store_payment(self.conn, "7001")["note"])
		self.assertEqual(self.saved("payment_status").payment_status, "Unpaid")
		self.assertIn("Ignored", store_payments.apply_store_payment(self.conn, "999999")["note"])

	def test_open_payment_link_is_cancelled(self):
		frappe.db.set_value("Field Order", self.order.name, {"payment_status": "Link Sent", "razorpay_payment_link_id": "plink_9"})
		self.conn.get_store_payment.return_value = self.paid(self.total)
		with patch.object(store_payments, "fetch_payment_link", return_value={"status": "created", "amount_paid": 0}), patch.object(
			store_payments, "cancel_payment_link"
		) as cancel:
			store_payments.apply_store_payment(self.conn, "7001")
		cancel.assert_called_once()
		self.assertEqual(self.saved("payment_status").payment_status, "Paid")

	def test_paid_link_wins(self):
		frappe.db.set_value("Field Order", self.order.name, {"payment_status": "Link Sent", "razorpay_payment_link_id": "plink_9"})
		self.conn.get_store_payment.return_value = self.paid(self.total)
		with patch.object(store_payments, "fetch_payment_link", return_value={"status": "paid", "amount_paid": 100}):
			result = store_payments.apply_store_payment(self.conn, "7001")
		self.assertIn("payment link", result["note"])
		self.assertEqual(self.saved("payment_status").payment_status, "Link Sent")

	def test_store_time_in_utc_becomes_system_time(self):
		naive = store_payments._when("2026-11-16 04:30:00")
		zulu = store_payments._when("2026-11-16T04:30:00Z")
		self.assertEqual(naive, zulu)
		self.assertIsNone(zulu.tzinfo)


class TestWooCommercePaidRule(IntegrationTestCase):
	"""WooCommerce: a paid date with a real payment method; see connectors/woocommerce.py."""

	def test_rule(self):
		from field_sales_woocommerce.connectors.woocommerce import _store_payment

		paid = {"date_paid": "2026-11-16T10:00:00", "date_paid_gmt": "2026-11-16T04:30:00", "status": "processing", "payment_method": "stripe", "payment_method_title": "Card", "total": "1180.00", "currency": "INR", "transaction_id": "ch_1"}
		self.assertEqual(
			_store_payment(paid),
			{"amount": 1180.0, "currency": "INR", "method": "Card", "reference": "ch_1", "paid_at": "2026-11-16T04:30:00"},
		)
		# Agamsale's Confirmed orders: Processing (so a paid date) but no payment method.
		self.assertIsNone(_store_payment({**paid, "payment_method": ""}))
		# The plugin's gateway: Agamsale records those payments itself.
		self.assertIsNone(_store_payment({**paid, "payment_method": "agamsale_razorpay"}))
		# Cash on delivery counts once delivered (Completed).
		self.assertIsNone(_store_payment({**paid, "payment_method": "cod"}))
		self.assertIsNotNone(_store_payment({**paid, "payment_method": "cod", "status": "completed"}))
		self.assertIsNone(_store_payment({**paid, "date_paid": None}))
		self.assertIsNone(_store_payment({**paid, "status": "cancelled"}))
