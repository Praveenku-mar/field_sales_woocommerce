# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Refunds of Razorpay payments (payments/refunds.py): the order page's Refund button, refund and
failed-payment webhooks, and what the payment info API offers. The same file runs in every
Field Sales edition. Razorpay is faked.
"""

import importlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
refunds = importlib.import_module(f"{APP}.payments.refunds")
transactions = importlib.import_module(f"{APP}.payments.transactions")
frontend_api = importlib.import_module(f"{APP}.frontend_api")
links = importlib.import_module(f"{APP}.tests.test_razorpay_links")


class FakeRefunds:
	def __init__(self):
		self.calls = []

	def __call__(self, method, path, params=None, json=None, settings=None):
		self.calls.append((method, path, json))
		return {"id": f"rfnd_{len(self.calls)}", "status": "processed", "amount": json["amount"]}


class TestPaymentRefunds(links.LinkFixture, IntegrationTestCase):
	"""Runs on a submitted order paid in full on a payment link (pay_1)."""

	def setUp(self):
		super().setUp()
		self.total = frappe.db.get_value("Field Order", self.order.name, "grand_total")
		frappe.db.set_value(
			"Field Order", self.order.name, {"payment_status": "Paid", "amount_paid": self.total, "razorpay_payment_id": "pay_1"}
		)
		transactions.record(
			"payment:pay_1",
			transaction_type="Payment",
			source="Payment Link",
			status="Captured",
			amount=self.total,
			reference_order=self.order.name,
			razorpay_payment_id="pay_1",
		)
		self.fake_refunds = FakeRefunds()
		p = patch.object(refunds, "request", side_effect=self.fake_refunds)
		p.start()
		self.addCleanup(p.stop)
		notify = patch(f"{APP}.payments.razorpay.notify_users")
		notify.start()
		self.addCleanup(notify.stop)

	def saved(self, *fields):
		return frappe.db.get_value("Field Order", self.order.name, list(fields), as_dict=True)

	def refund(self, amount, request_id="req_" + "a" * 16, reason="Damaged"):
		return refunds.refund_order_payment(self.order.name, amount, request_id, reason)

	# -- the Refund button ---------------------------------------------------

	def test_partial_then_full_refund(self):
		half = round(self.total / 2, 2)
		result = self.refund(half)
		self.assertEqual((result["status"], result["payment_status"]), ("processed", "Partially Refunded"))
		self.assertEqual(self.fake_refunds.calls[0][1], "/payments/pay_1/refund")
		self.assertEqual(self.fake_refunds.calls[0][2]["amount"], round(half * 100))
		self.refund(round(self.total - half, 2), request_id="req_" + "b" * 16)
		o = self.saved("payment_status", "amount_refunded")
		self.assertEqual((o.payment_status, o.amount_refunded), ("Refunded", self.total))

	def test_same_request_refunds_once(self):
		self.refund(10)
		self.refund(10)
		self.assertEqual(len(self.fake_refunds.calls), 1)

	def test_more_than_left_or_nothing_paid_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.refund, self.total + 1)
		self.assertRaises(frappe.ValidationError, self.refund, 0, request_id="req_" + "c" * 16)
		frappe.db.delete("Payment Transaction", {"reference_order": self.order.name})
		self.assertRaises(frappe.ValidationError, self.refund, 5, request_id="req_" + "d" * 16)
		self.assertEqual(self.fake_refunds.calls, [])

	def test_reps_cannot_refund(self):
		frappe.set_user(self.rep_user)
		try:
			self.assertRaises(frappe.PermissionError, self.refund, 10)
		finally:
			frappe.set_user("Administrator")

	def test_payment_info_offers_what_is_left(self):
		info = frontend_api.get_order_payment_info(self.order.name)
		self.assertTrue(info["can_refund"])
		self.assertEqual(info["refundable_amount"], self.total)
		self.refund(self.total)
		info = frontend_api.get_order_payment_info(self.order.name)
		self.assertFalse(info["can_refund"])
		self.assertEqual(info["refundable_amount"], 0)
		self.assertFalse(info["can_mark_paid"])
		self.assertEqual([t.transaction_type for t in info["transactions"]][:2], ["Refund", "Payment"])

	# -- webhooks ------------------------------------------------------------

	def test_failed_refund_restores_paid(self):
		self.refund(10)
		refunds.handle_webhook_event(
			"refund.failed", {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_1", "amount": 1000}}}, {}
		)
		o = self.saved("payment_status", "amount_refunded")
		self.assertEqual((o.payment_status, o.amount_refunded), ("Paid", 0))

	def test_dashboard_refund_is_recorded(self):
		status, _note = refunds.handle_webhook_event(
			"refund.processed",
			{"refund": {"entity": {"id": "rfnd_dash", "payment_id": "pay_1", "amount": 500, "currency": "INR"}}},
			{},
		)
		self.assertEqual(status, "Processed")
		self.assertEqual(self.saved("amount_refunded").amount_refunded, 5)
		self.assertEqual(
			frappe.db.get_value("Payment Transaction", {"razorpay_refund_id": "rfnd_dash"}, "status"), "Processed"
		)

	def test_unknown_refund_is_ignored(self):
		status, _note = refunds.handle_webhook_event(
			"refund.processed", {"refund": {"entity": {"id": "rfnd_x", "payment_id": "pay_other", "amount": 500}}}, {}
		)
		self.assertEqual(status, "Ignored")

	def test_failed_link_attempt_goes_into_history(self):
		payment = {
			"id": "pay_failed",
			"status": "failed",
			"amount": 1000,
			"currency": "INR",
			"error_description": "Card declined",
			"notes": {"field_order": self.order.name},
		}
		status, _note = refunds.handle_webhook_event("payment.failed", {"payment": {"entity": payment}}, {})
		self.assertEqual(status, "Processed")
		row = frappe.db.get_value(
			"Payment Transaction", {"razorpay_payment_id": "pay_failed"}, ["status", "error", "source"], as_dict=True
		)
		self.assertEqual((row.status, row.error, row.source), ("Failed", "Card declined", "Payment Link"))
		# The order stays paid: a failed attempt changes nothing on it.
		self.assertEqual(self.saved("payment_status").payment_status, "Paid")
