# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Razorpay on the WooCommerce checkout (payments/checkout.py): signed plugin calls, order
creation, payment verification, webhooks and refunds. Razorpay and WooCommerce are faked."""

import hashlib
import hmac
import json
import secrets
import time
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import set_encrypted_password

from field_sales_woocommerce.connectors.base import ConnectorError
from field_sales_woocommerce.payments import checkout, razorpay
from field_sales_woocommerce.payments.razorpay import RazorpayError
from field_sales_woocommerce.tests import test_razorpay_links as links

PLUGIN_SECRET = "p" * 40
KEY_SECRET = "s3cret"
WEBHOOK_SECRET = "whsec_test"


class FakeCheckoutRazorpay:
	"""Stands in for payments.razorpay.request inside checkout.py."""

	def __init__(self):
		self.calls = []
		self.orders = {}
		self.payments = {}
		self.refunds = 0
		self.fail = None

	def __call__(self, method, path, params=None, json=None, settings=None):
		self.calls.append((method, path, json))
		if self.fail:
			raise self.fail
		if method == "POST" and path == "/orders":
			order_id = f"order_{len(self.orders) + 1}"
			self.orders[order_id] = {"id": order_id, **json}
			return self.orders[order_id]
		if method == "GET" and path == "/orders":
			return {"items": []}
		if method == "GET" and path.startswith("/payments/"):
			return dict(self.payments[path.split("/")[2]])
		if method == "POST" and path.endswith("/capture"):
			payment = self.payments[path.split("/")[2]]
			payment["status"] = "captured"
			return dict(payment)
		if method == "POST" and path.endswith("/refund"):
			self.refunds += 1
			return {"id": f"rfnd_{self.refunds}", "status": "processed", "amount": json["amount"]}
		raise AssertionError(f"Unexpected call {method} {path}")

	def posts(self, path):
		return [j for m, p, j in self.calls if m == "POST" and p == path]


class TestRazorpayCheckout(IntegrationTestCase):
	_user = links.TestRazorpayLinks._user
	order_values = links.TestRazorpayLinks.order_values

	def setUp(self):
		links.TestRazorpayLinks.setUp(self)
		frappe.db.set_single_value("Store Connection", {"platform": "WooCommerce", "store_url": "https://shop.example"})
		set_encrypted_password("Store Connection", "Store Connection", PLUGIN_SECRET, "wp_shared_secret")
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", KEY_SECRET, "key_secret")
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", WEBHOOK_SECRET, "webhook_secret")
		# This order was pushed to WooCommerce as order 7001.
		frappe.db.set_value("Field Order", self.order.name, "external_order_id", "7001")
		self.total = frappe.db.get_value("Field Order", self.order.name, "grand_total")

		self.rzp = FakeCheckoutRazorpay()
		for target in ("field_sales_woocommerce.payments.checkout.request", "field_sales_woocommerce.payments.refunds.request"):
			p = patch(target, side_effect=self.rzp)
			p.start()
			self.addCleanup(p.stop)
		self.woo_order = {"id": 7001, "number": "7001", "status": "pending", "total": str(self.total), "currency": "INR", "date_paid": None, "billing": {"first_name": "Asha", "email": "a@example.com"}}
		self.conn = MagicMock()
		self.conn.get_payable_order.side_effect = lambda order_id: dict(self.woo_order)
		p = patch("field_sales_woocommerce.connectors.get_connector", return_value=self.conn)
		p.start()
		self.addCleanup(p.stop)
		notify = patch.object(razorpay, "notify_users")
		notify.start()
		self.addCleanup(notify.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- helpers -------------------------------------------------------------

	def call(self, name, body, *, secret=PLUGIN_SECRET, timestamp=None, nonce=None):
		path = f"/api/method/field_sales_woocommerce.payments.checkout.{name}"
		raw = json.dumps(body).encode()
		timestamp = str(timestamp or int(time.time()))
		nonce = nonce or secrets.token_hex(16)
		signature = hmac.new(secret.encode(), b"\n".join([timestamp.encode(), nonce.encode(), path.encode(), raw]), hashlib.sha256).hexdigest()
		request = MagicMock()
		request.get_data.return_value = raw
		request.path = path
		request.headers = {"X-Agamsale-Timestamp": timestamp, "X-Agamsale-Nonce": nonce, "X-Agamsale-Signature": signature}
		frappe.local.response = frappe._dict()
		with patch.object(frappe, "request", request, create=True):
			result = getattr(checkout, name)()
		return frappe.local.response.get("http_status_code") or 200, result

	def create(self):
		return self.call("create_checkout_order", {"store_order_id": "7001", "amount": 1})

	def pay(self, order_id, payment_id="pay_9", status="captured", amount=None, currency="INR"):
		self.rzp.payments[payment_id] = {
			"id": payment_id, "order_id": order_id, "status": status, "currency": currency, "method": "upi",
			"amount": round(self.total * 100) if amount is None else amount, "created_at": int(time.time()),
		}
		signature = hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
		return {"store_order_id": "7001", "razorpay_order_id": order_id, "razorpay_payment_id": payment_id, "razorpay_signature": signature}

	def history(self, **filters):
		return frappe.get_all("Payment Transaction", filters={"store_order_id": "7001", **filters}, fields=["transaction_type", "status", "razorpay_payment_id", "razorpay_refund_id", "amount", "error"], order_by="creation asc")

	def webhook(self, body, event_id):
		raw = json.dumps(body).encode()
		request = MagicMock()
		request.get_data.return_value = raw
		request.headers = {"X-Razorpay-Signature": hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest(), "X-Razorpay-Event-Id": event_id}
		frappe.local.response = frappe._dict()
		with patch.object(frappe, "request", request, create=True):
			result = razorpay.webhook()
		return frappe.local.response.get("http_status_code") or 200, result

	# -- request signing -----------------------------------------------------

	def test_unsigned_wrong_secret_old_or_replayed_requests_are_refused(self):
		self.assertEqual(self.call("ping", {}, secret="x" * 40)[1]["code"], "BAD_SIGNATURE")
		self.assertEqual(self.call("ping", {}, timestamp=int(time.time()) - 3600)[1]["code"], "EXPIRED")
		nonce = secrets.token_hex(16)
		self.assertEqual(self.call("ping", {}, nonce=nonce)[0], 200)
		status, result = self.call("ping", {}, nonce=nonce)
		self.assertEqual((status, result["code"]), (401, "REPLAYED"))

	def test_ping_reports_razorpay_setup_without_secrets(self):
		status, result = self.call("ping", {})
		self.assertEqual(status, 200)
		self.assertTrue(result["razorpay"]["keys_set"])
		self.assertIn("payment.captured", result["razorpay"]["webhook_events"])
		self.assertNotIn(KEY_SECRET, json.dumps(result))

	# -- checkout order ------------------------------------------------------

	def test_order_amount_comes_from_woocommerce_and_is_reused(self):
		status, result = self.create()
		self.assertEqual(status, 200)
		self.assertEqual(result["amount"], round(self.total * 100))  # not the request's 1
		self.assertEqual(result["key_id"], "rzp_test_abc")
		self.assertEqual(self.create()[1]["razorpay_order_id"], result["razorpay_order_id"])
		self.assertEqual(len(self.rzp.posts("/orders")), 1)
		rows = self.history(transaction_type="Checkout Order")
		self.assertEqual([(r.status, r.amount) for r in rows], [("Created", self.total)])

	def test_changed_order_total_gets_a_new_razorpay_order(self):
		first = self.create()[1]["razorpay_order_id"]
		self.woo_order["total"] = str(self.total + 10)
		second = self.create()[1]["razorpay_order_id"]
		self.assertNotEqual(first, second)
		self.assertEqual([r.status for r in self.history(transaction_type="Checkout Order")], ["Cancelled", "Created"])

	def test_paid_or_missing_order_and_store_down_are_refused(self):
		self.woo_order.update(status="processing", date_paid="2026-11-16T10:00:00")
		self.assertEqual(self.create()[1]["code"], "NOT_PAYABLE")
		self.conn.get_payable_order.side_effect = ConnectorError("not found", code="HTTP_ERROR", http_status=404)
		self.assertEqual(self.create()[1]["code"], "ORDER_NOT_FOUND")
		self.conn.get_payable_order.side_effect = ConnectorError("timeout", code="NETWORK_ERROR")
		status, result = self.create()
		self.assertEqual((status, result["code"]), (502, "STORE_UNAVAILABLE"))

	def test_razorpay_network_failure_is_a_502_and_records_nothing(self):
		self.rzp.fail = RazorpayError("Could not reach Razorpay", code="NETWORK_ERROR")
		status, result = self.create()
		self.assertEqual((status, result["ok"]), (502, False))
		self.assertEqual(self.history(), [])

	def test_razorpay_turned_off(self):
		frappe.db.set_single_value("Razorpay Settings", "enabled", 0)
		self.assertEqual(self.create()[1]["code"], "RAZORPAY_DISABLED")

	# -- verify ----------------------------------------------------------------

	def test_verified_payment_marks_the_field_order_paid(self):
		order_id = self.create()[1]["razorpay_order_id"]
		status, result = self.call("verify_checkout_payment", self.pay(order_id))
		self.assertEqual((status, result["status"], result["payment_id"]), (200, "paid", "pay_9"))
		fo = frappe.db.get_value("Field Order", self.order.name, ["payment_status", "amount_paid", "razorpay_payment_id", "store_marked_paid_on"], as_dict=True)
		self.assertEqual((fo.payment_status, fo.amount_paid, fo.razorpay_payment_id), ("Paid", self.total, "pay_9"))
		self.assertIsNotNone(fo.store_marked_paid_on)
		# Verified again (the customer reloads the return page): same answer, one history row.
		self.assertEqual(self.call("verify_checkout_payment", self.pay(order_id))[1]["status"], "paid")
		self.assertEqual(len(self.history(transaction_type="Payment")), 1)

	def test_bad_signature_or_other_store_order_is_refused(self):
		order_id = self.create()[1]["razorpay_order_id"]
		body = self.pay(order_id)
		self.assertEqual(self.call("verify_checkout_payment", {**body, "razorpay_signature": "0" * 64})[1]["code"], "INVALID_SIGNATURE")
		self.assertEqual(self.call("verify_checkout_payment", {**body, "store_order_id": "7002"})[1]["code"], "ORDER_MISMATCH")
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "payment_status"), "Unpaid")

	def test_wrong_amount_or_currency_is_not_paid(self):
		order_id = self.create()[1]["razorpay_order_id"]
		status, result = self.call("verify_checkout_payment", self.pay(order_id, amount=100))
		self.assertEqual((status, result["code"]), (402, "NOT_PAID"))
		status, result = self.call("verify_checkout_payment", self.pay(order_id, payment_id="pay_10", currency="USD"))
		self.assertEqual(result["code"], "NOT_PAID")
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "payment_status"), "Unpaid")
		self.assertEqual({r.status for r in self.history(transaction_type="Payment")}, {"Failed"})

	def test_failed_then_retried_payment(self):
		order_id = self.create()[1]["razorpay_order_id"]
		status, result = self.call("verify_checkout_payment", self.pay(order_id, payment_id="pay_f", status="failed"))
		self.assertEqual(result["code"], "NOT_PAID")
		# The retry uses the same Razorpay order.
		self.assertEqual(self.create()[1]["razorpay_order_id"], order_id)
		self.assertEqual(self.call("verify_checkout_payment", self.pay(order_id, payment_id="pay_ok"))[1]["status"], "paid")
		self.assertEqual([(r.razorpay_payment_id, r.status) for r in self.history(transaction_type="Payment")], [("pay_f", "Failed"), ("pay_ok", "Captured")])

	def test_authorized_payment_is_captured(self):
		order_id = self.create()[1]["razorpay_order_id"]
		self.assertEqual(self.call("verify_checkout_payment", self.pay(order_id, status="authorized"))[1]["status"], "paid")
		self.assertEqual(len([c for c in self.rzp.calls if c[1].endswith("/capture")]), 1)

	# -- webhook -------------------------------------------------------------

	def test_webhook_records_a_payment_the_customer_never_returned_for(self):
		order_id = self.create()[1]["razorpay_order_id"]
		self.pay(order_id)
		payment = self.rzp.payments["pay_9"]
		body = {"event": "payment.captured", "payload": {"payment": {"entity": payment}}}
		with patch.object(checkout, "enqueue_mark_checkout_paid") as enqueue:
			self.assertEqual(self.webhook(body, "evt_c1"), (200, {"status": "ok"}))
			self.assertEqual(self.webhook(body, "evt_c1")[1], {"status": "duplicate"})
		enqueue.assert_called_once_with("7001", "pay_9")
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "payment_status"), "Paid")

	def test_webhook_payment_failed_and_unknown_order(self):
		order_id = self.create()[1]["razorpay_order_id"]
		failed = {"id": "pay_x", "order_id": order_id, "status": "failed", "amount": round(self.total * 100), "currency": "INR", "error_description": "Card declined"}
		self.webhook({"event": "payment.failed", "payload": {"payment": {"entity": failed}}}, "evt_f1")
		self.assertEqual(self.history(razorpay_payment_id="pay_x")[0].error, "Card declined")
		other = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_y", "order_id": "order_unknown"}}}}
		self.assertEqual(self.webhook(other, "evt_f2")[1], {"status": "ignored"})

	# -- refunds -------------------------------------------------------------

	def paid_order(self):
		order_id = self.create()[1]["razorpay_order_id"]
		self.call("verify_checkout_payment", self.pay(order_id))

	def test_partial_then_full_refund(self):
		self.paid_order()
		half = round(self.total / 2, 2)
		status, result = self.call("refund_checkout_payment", {"store_order_id": "7001", "amount": half, "refund_request_id": "req_" + "a" * 16})
		self.assertEqual((status, result["status"]), (200, "processed"))
		fo = frappe.db.get_value("Field Order", self.order.name, ["payment_status", "amount_refunded"], as_dict=True)
		self.assertEqual((fo.payment_status, fo.amount_refunded), ("Partially Refunded", half))
		self.call("refund_checkout_payment", {"store_order_id": "7001", "amount": round(self.total - half, 2), "refund_request_id": "req_" + "b" * 16})
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "payment_status"), "Refunded")

	def test_repeated_refund_request_refunds_once_and_overrefund_is_refused(self):
		self.paid_order()
		body = {"store_order_id": "7001", "amount": 10, "refund_request_id": "req_" + "c" * 16}
		self.call("refund_checkout_payment", body)
		self.call("refund_checkout_payment", body)
		self.assertEqual(len(self.rzp.posts("/payments/pay_9/refund")), 1)
		status, result = self.call("refund_checkout_payment", {**body, "amount": self.total, "refund_request_id": "req_" + "d" * 16})
		self.assertEqual((status, result["code"]), (409, "AMOUNT_TOO_HIGH"))

	def test_refund_failed_webhook_restores_the_paid_status(self):
		self.paid_order()
		self.call("refund_checkout_payment", {"store_order_id": "7001", "amount": 10, "refund_request_id": "req_" + "e" * 16})
		body = {"event": "refund.failed", "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_9", "amount": 1000}}}}
		self.webhook(body, "evt_r1")
		fo = frappe.db.get_value("Field Order", self.order.name, ["payment_status", "amount_refunded"], as_dict=True)
		self.assertEqual((fo.payment_status, fo.amount_refunded), ("Paid", 0))

	def test_refund_made_in_the_razorpay_dashboard_is_recorded(self):
		self.paid_order()
		body = {"event": "refund.processed", "payload": {"refund": {"entity": {"id": "rfnd_dash", "payment_id": "pay_9", "amount": 500, "currency": "INR"}}}}
		self.webhook(body, "evt_r2")
		self.assertEqual(self.history(razorpay_refund_id="rfnd_dash")[0].status, "Processed")
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "amount_refunded"), 5)

	def test_no_razorpay_payment_to_refund(self):
		self.assertEqual(self.call("refund_checkout_payment", {"store_order_id": "7001", "amount": 5, "refund_request_id": "req_" + "f" * 16})[1]["code"], "NO_RAZORPAY_PAYMENT")
