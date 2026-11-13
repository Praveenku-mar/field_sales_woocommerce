# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime
from frappe.utils.password import set_encrypted_password

from field_sales_woocommerce.payments import razorpay
from field_sales_woocommerce.payments.razorpay import (
	RazorpayError,
	create_link_for_order,
	reconcile_open_links,
	refresh_payment_status,
)
from field_sales_woocommerce.tests import test_razorpay_links as links

WEBHOOK_SECRET = "whsec_test"


class TestRazorpayWebhook(IntegrationTestCase):
	# Same fixtures as the link tests: a submitted order for a rep, Razorpay faked.
	_user = links.TestRazorpayLinks._user
	order_values = links.TestRazorpayLinks.order_values

	def setUp(self):
		links.TestRazorpayLinks.setUp(self)
		# Reconcile must see only this order; earlier runs' open links linger on the site.
		frappe.db.set_value(
			"Field Order", {"payment_status": "Link Sent", "name": ("!=", self.order.name)}, "payment_status", "Draft"
		)
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", WEBHOOK_SECRET, "webhook_secret")
		create_link_for_order(self.order.name)  # plink_1
		self.paise = round(frappe.db.get_value("Field Order", self.order.name, "grand_total") * 100)
		notify = patch.object(razorpay, "notify_users")
		self.notify = notify.start()
		self.addCleanup(notify.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -------------------------------------------------------------

	def body(self, event="payment_link.paid", status="paid", amount_paid=None, link_id="plink_1"):
		amount_paid = self.paise if amount_paid is None else amount_paid
		return {
			"event": event,
			"payload": {
				"payment_link": {
					"entity": {"id": link_id, "status": status, "amount": self.paise, "amount_paid": amount_paid}
				},
				"payment": {"entity": {"id": "pay_123", "created_at": 1792000000, "status": "captured"}},
			},
		}

	def post(self, body, event_id="evt_1", signature=None):
		raw = json.dumps(body).encode()
		if signature is None:
			signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
		request = MagicMock()
		request.get_data.return_value = raw
		request.headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id}
		frappe.local.response = frappe._dict()
		with patch.object(frappe, "request", request, create=True):
			result = razorpay.webhook()
		return frappe.local.response.get("http_status_code") or 200, result

	def event(self, event_id="evt_1"):
		return frappe.db.get_value(
			"Razorpay Webhook Event", {"event_id": event_id}, ["status", "reference_order", "error"], as_dict=True
		)

	def paid_fields(self):
		return frappe.db.get_value(
			"Field Order",
			self.order.name,
			["payment_status", "amount_paid", "razorpay_payment_id", "paid_on"],
			as_dict=True,
		)

	# -- paid ----------------------------------------------------------------

	def test_paid_event_marks_order_paid(self):
		code, result = self.post(self.body())
		self.assertEqual((code, result), (200, {"status": "ok"}))
		values = self.paid_fields()
		self.assertEqual(values.payment_status, "Paid")
		self.assertEqual(round(values.amount_paid * 100), self.paise)
		self.assertEqual(values.razorpay_payment_id, "pay_123")
		self.assertIsNotNone(values.paid_on)
		self.assertEqual(self.event().status, "Processed")
		self.assertEqual(self.event().reference_order, self.order.name)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_name": self.order.name, "content": ("like", "%Razorpay payment received%")},
			)
		)

	def test_rep_and_sales_managers_are_notified(self):
		manager = self._user("rzp.manager@example.com", "Sales Manager")
		frappe.local.field_sales_role_users = None  # cached per request / job
		self.post(self.body())
		((users, kind, doctype, docname, _subject), _kwargs) = self.notify.call_args
		self.assertIn(self.rep_user, users)
		self.assertIn(manager, users)
		self.assertEqual((kind, doctype, docname), ("payment_received", "Field Order", self.order.name))

	def test_partial_payment_is_partially_paid(self):
		self.post(self.body(event="payment_link.partially_paid", status="partially_paid", amount_paid=10000))
		values = self.paid_fields()
		self.assertEqual(values.payment_status, "Partially Paid")
		self.assertEqual(values.amount_paid, 100)

	def test_paid_order_is_never_downgraded(self):
		self.post(self.body())
		self.post(self.body(event="payment_link.expired", status="expired"), event_id="evt_2")
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	# -- other events --------------------------------------------------------

	def test_expired_and_cancelled(self):
		for event_id, (event, status, expected) in enumerate(
			[
				("payment_link.expired", "expired", "Link Expired"),
				("payment_link.cancelled", "cancelled", "Link Cancelled"),
			]
		):
			with self.subTest(event):
				self.post(self.body(event=event, status=status), event_id=f"evt_{event_id}")
				self.assertEqual(self.order_values().payment_status, expected)
		self.notify.assert_not_called()

	def test_cancel_of_a_replaced_link_does_not_touch_the_new_one(self):
		# New Link: the old link's cancel webhook finds the order by the old id,
		# then waits for the row lock while the new link is saved.
		get_doc = frappe.get_doc

		def replaced_while_waiting(*args, **kwargs):
			frappe.db.set_value("Field Order", self.order.name, "razorpay_payment_link_id", "plink_2")
			return get_doc(*args, **kwargs)

		with patch.object(frappe, "get_doc", side_effect=replaced_while_waiting):
			code, _result = self.post(self.body(event="payment_link.cancelled", status="cancelled"))
		self.assertEqual(code, 200)
		self.assertEqual(self.event().status, "Ignored")
		self.assertEqual(self.order_values().payment_status, "Link Sent")

	def test_payment_on_a_replaced_link_is_still_recorded(self):
		frappe.db.set_value("Field Order", self.order.name, "razorpay_payment_link_id", "plink_2")
		doc = frappe.get_doc("Field Order", self.order.name)
		entity = self.body()["payload"]["payment_link"]["entity"]  # plink_1, paid
		self.assertTrue(razorpay._apply_link_state(doc, entity))
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	def test_unknown_link_is_ignored(self):
		code, _result = self.post(self.body(link_id="plink_other"))
		self.assertEqual(code, 200)
		self.assertEqual(self.event().status, "Ignored")
		self.assertEqual(self.order_values().payment_status, "Link Sent")

	def test_other_event_is_ignored(self):
		code, _result = self.post(self.body(event="payment.captured"))
		self.assertEqual(code, 200)
		self.assertEqual(self.event().status, "Ignored")
		self.assertEqual(self.order_values().payment_status, "Link Sent")

	# -- refusals and duplicates ---------------------------------------------

	def test_bad_or_missing_signature_changes_nothing(self):
		for label, signature in {"wrong": "0" * 64, "missing": ""}.items():
			with self.subTest(label):
				code, _result = self.post(self.body(), signature=signature)
				self.assertEqual(code, 401)
		self.assertEqual(self.order_values().payment_status, "Link Sent")
		self.assertIsNone(self.event())

	def test_disabled_is_refused(self):
		frappe.db.set_single_value("Razorpay Settings", "enabled", 0)
		code, _result = self.post(self.body())
		self.assertEqual(code, 400)
		self.assertEqual(self.order_values().payment_status, "Link Sent")
		self.assertIsNone(self.event())

	def test_duplicate_event_id_does_nothing(self):
		self.post(self.body())
		with patch.object(razorpay, "_apply_link_state") as apply:
			code, result = self.post(self.body())
		self.assertEqual((code, result), (200, {"status": "duplicate"}))
		apply.assert_not_called()
		self.assertEqual(frappe.db.count("Razorpay Webhook Event", {"event_id": "evt_1"}), 1)

	def test_replayed_body_under_a_new_event_id_does_nothing(self):
		# The event id header is not signed; the body is.
		self.post(self.body(), event_id="evt_1")
		with patch.object(razorpay, "_apply_link_state") as apply:
			code, result = self.post(self.body(), event_id="evt_replay")
		self.assertEqual((code, result), (200, {"status": "duplicate"}))
		apply.assert_not_called()

	def test_other_currency_is_ignored(self):
		body = self.body()
		body["payload"]["payment_link"]["entity"]["currency"] = "USD"
		code, result = self.post(body)
		self.assertEqual((code, result), (200, {"status": "ignored: currency mismatch"}))
		self.assertEqual(self.event().status, "Ignored")
		self.assertEqual(self.order_values().payment_status, "Link Sent")

	def test_error_is_recorded_and_retried(self):
		with patch.object(razorpay, "_apply_link_state", side_effect=Exception("boom")), patch.object(
			frappe.db, "rollback"
		), patch.object(frappe.db, "commit"):
			code, _result = self.post(self.body())
		self.assertEqual(code, 500)
		self.assertEqual(self.event().status, "Error")
		self.assertIn("boom", self.event().error)
		self.assertEqual(frappe.session.user, "Administrator")

		# Razorpay's retry of the same event goes through.
		code, _result = self.post(self.body())
		self.assertEqual(code, 200)
		self.assertEqual(self.event().status, "Processed")
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	def test_guest_user_is_restored(self):
		frappe.set_user("Guest")
		self.post(self.body())
		self.assertEqual(frappe.session.user, "Guest")
		frappe.set_user("Administrator")
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	# -- refresh fallback ----------------------------------------------------

	def test_refresh_applies_razorpay_state(self):
		self.fake.links["plink_1"].update(
			{
				"status": "paid",
				"amount_paid": self.paise,
				"payments": [
					{"payment_id": "pay_old", "status": "failed", "created_at": 1791000000},
					{"payment_id": "pay_new", "status": "captured", "created_at": 1792000000},
				],
			}
		)
		frappe.set_user(self.rep_user)
		try:
			result = refresh_payment_status(self.order.name)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(result["payment_status"], "Paid")
		self.assertEqual(result["razorpay_payment_id"], "pay_new")
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	def test_refresh_restores_a_live_link(self):
		frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Cancelled")
		self.fake.links["plink_1"]["status"] = "created"
		self.assertEqual(refresh_payment_status(self.order.name)["payment_status"], "Link Sent")

	def test_refresh_without_link_is_refused(self):
		frappe.db.set_value("Field Order", self.order.name, "razorpay_payment_link_id", None)
		self.assertRaises(frappe.ValidationError, refresh_payment_status, self.order.name)

	# -- cancelling the order ------------------------------------------------

	def test_paid_order_cannot_be_cancelled(self):
		for status in ("Paid", "Partially Paid"):
			with self.subTest(status):
				frappe.db.set_value("Field Order", self.order.name, "payment_status", status)
				doc = frappe.get_doc("Field Order", self.order.name)
				with self.assertRaises(frappe.ValidationError) as ctx:
					doc.cancel()
				self.assertIn("refund the payment", str(ctx.exception))
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "docstatus"), 1)

	def test_cancel_cancels_the_open_link(self):
		frappe.get_doc("Field Order", self.order.name).cancel()
		self.assertIn(("POST", "/payment_links/plink_1/cancel"), self.fake.paths())
		self.assertEqual(self.order_values().payment_status, "Link Cancelled")
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "docstatus"), 2)

	def test_failed_link_cancel_does_not_block_order_cancel(self):
		with patch.object(razorpay, "cancel_payment_link", side_effect=RazorpayError("Gateway down")):
			frappe.get_doc("Field Order", self.order.name).cancel()
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "docstatus"), 2)
		self.assertEqual(self.order_values().payment_status, "Link Sent")
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": self.order.name, "content": ("like", "%Gateway down%")})
		)

	def test_payment_on_cancelled_order_is_still_recorded(self):
		with patch.object(razorpay, "cancel_payment_link", side_effect=RazorpayError("Gateway down")):
			frappe.get_doc("Field Order", self.order.name).cancel()
		self.post(self.body())
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	# -- hourly reconcile ----------------------------------------------------

	def test_link_created_on_is_stored(self):
		self.assertIsNotNone(frappe.db.get_value("Field Order", self.order.name, "payment_link_created_on"))

	def test_reconcile_applies_razorpay_state(self):
		self.fake.links["plink_1"].update({"status": "paid", "amount_paid": self.paise, "payments": []})
		with patch.object(frappe.db, "commit"):
			reconcile_open_links()
		self.assertEqual(self.paid_fields().payment_status, "Paid")

	def test_reconcile_skips_links_older_than_7_days(self):
		frappe.db.set_value("Field Order", self.order.name, "payment_link_created_on", add_days(now_datetime(), -8))
		self.fake.links["plink_1"]["status"] = "expired"
		with patch.object(frappe.db, "commit"):
			reconcile_open_links()
		self.assertEqual(self.order_values().payment_status, "Link Sent")
		self.assertNotIn(("GET", "/payment_links/plink_1"), self.fake.paths())

	def test_reconcile_error_is_logged_per_order(self):
		with patch.object(razorpay, "fetch_payment_link", side_effect=RazorpayError("Gateway down")), patch.object(
			frappe.db, "commit"
		), patch.object(frappe.db, "rollback"), patch.object(frappe, "log_error") as log_error:
			reconcile_open_links()
		log_error.assert_called_once()
		self.assertEqual(self.order_values().payment_status, "Link Sent")

	def test_reconcile_does_nothing_when_disabled(self):
		frappe.db.set_single_value("Razorpay Settings", "enabled", 0)
		self.fake.links["plink_1"]["status"] = "expired"
		reconcile_open_links()
		self.assertEqual(self.order_values().payment_status, "Link Sent")

