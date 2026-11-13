# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import time
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime, nowdate
from frappe.utils.password import set_encrypted_password

from field_sales_woocommerce.payments import razorpay
from field_sales_woocommerce.payments.razorpay import (
	API_BASE,
	create_link_for_order,
	get_payment_config,
	indian_phone,
	link_for_confirmation,
)


class FakeRazorpay:
	"""Stands in for requests.request: records calls and answers like Razorpay."""

	def __init__(self):
		self.calls = []
		self.links = {}  # id -> link
		self.fail_create = []  # queued (status, description) answers for POST /payment_links

	def __call__(self, method, url, params=None, json=None, auth=None, timeout=None):
		path = url.removeprefix(API_BASE)
		self.calls.append((method, path, json))
		if method == "POST" and path == "/payment_links":
			if self.fail_create:
				status, description = self.fail_create.pop(0)
				return self._response(
					status, {"error": {"code": "BAD_REQUEST_ERROR", "description": description}}
				)
			link_id = f"plink_{len(self.links) + 1}"
			link = {
				"id": link_id,
				"short_url": f"https://rzp.io/i/{link_id}",
				"status": "created",
				"reference_id": json["reference_id"],
				"expire_by": json.get("expire_by", 0),
			}
			self.links[link_id] = link
			return self._response(200, link)
		if method == "GET" and path.startswith("/payment_links/"):
			return self._response(200, self.links[path.split("/")[2]])
		if method == "POST" and path.endswith("/cancel"):
			link = self.links[path.split("/")[2]]
			link["status"] = "cancelled"
			return self._response(200, link)
		raise AssertionError(f"Unexpected call {method} {path}")

	def paths(self):
		return [(m, p) for m, p, _j in self.calls]

	def created(self):
		return [j for m, p, j in self.calls if m == "POST" and p == "/payment_links"]

	@staticmethod
	def _response(status, body):
		response = MagicMock(status_code=status)
		response.json.return_value = body
		return response


class TestRazorpayLinks(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Razorpay Settings",
			{
				"enabled": 1,
				"key_id": "rzp_test_abc",
				"link_expiry_hours": 72,
				"notify_sms": 1,
				"notify_email": 1,
				"callback_url": "",
			},
		)
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", "s3cret", "key_secret")
		# Links exist on direct installs; OAuth mode (the App Store) has them disabled.
		frappe.db.set_single_value(
			"Store Connection", {"platform": "WooCommerce", "enabled": 0}
		)

		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "WooCommerce",
				"product_title": "Payment link product",
				"external_product_id": "9400",
				"external_variant_id": "94",
				"status": "Active",
				"price": 1234.5,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.rep_user = self._user("rzp.rep@example.com", "Field Sales Rep")
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": self.rep_user}).insert(
			ignore_permissions=True
		)
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Payment Link Stores",
				"phone": "98765 43210",
				"email": "shop@example.com",
				"address_line1": "1 Market Street",
				"city": "Salem",
				"sales_rep": self.rep.name,
			}
		).insert(ignore_permissions=True)
		self.order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": self.rep.name,
				"order_date": nowdate(),
				"items": [{"product": product.name, "qty": 2, "rate": 1234.5}],
			}
		).insert(ignore_permissions=True)
		# Submitted without the store push.
		frappe.db.set_value("Field Order", self.order.name, {"docstatus": 1, "sync_status": "Synced"})

		self.fake = FakeRazorpay()
		patcher = patch.object(razorpay.requests, "request", side_effect=self.fake)
		patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def _user(self, email, role):
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "T", "send_welcome_email": 0})
		user.insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def order_values(self):
		return frappe.db.get_value(
			"Field Order",
			self.order.name,
			[
				"payment_status",
				"razorpay_payment_link_id",
				"razorpay_payment_link_url",
				"payment_link_expires_on",
			],
			as_dict=True,
		)

	def create_as_rep(self):
		frappe.set_user(self.rep_user)
		try:
			return create_link_for_order(self.order.name)
		finally:
			frappe.set_user("Administrator")

	# -- payload -----------------------------------------------------------

	def test_creates_link_with_payload(self):
		before = int(time.time())
		result = self.create_as_rep()
		(payload,) = self.fake.created()

		grand_total = frappe.db.get_value("Field Order", self.order.name, "grand_total")
		self.assertEqual(payload["amount"], round(grand_total * 100))  # paise
		self.assertGreater(payload["amount"], 0)
		self.assertEqual(payload["currency"], frappe.db.get_default("currency") or "INR")
		self.assertIs(payload["accept_partial"], False)
		self.assertEqual(payload["description"], f"Field Order {self.order.name}")
		self.assertEqual(payload["reference_id"], self.order.name)
		self.assertEqual(
			payload["customer"],
			{"name": "Payment Link Stores", "contact": "+919876543210", "email": "shop@example.com"},
		)
		self.assertEqual(payload["notify"], {"sms": True, "email": True})
		self.assertIs(payload["reminder_enable"], True)
		self.assertEqual(
			payload["notes"],
			{"field_order": self.order.name, "sales_rep": self.rep.name, "retailer": self.retailer.name},
		)
		self.assertAlmostEqual(payload["expire_by"], before + 72 * 3600, delta=5)
		self.assertNotIn("callback_url", payload)

		values = self.order_values()
		self.assertEqual(values.payment_status, "Link Sent")
		self.assertEqual(values.razorpay_payment_link_id, "plink_1")
		self.assertEqual(
			result,
			{
				"url": "https://rzp.io/i/plink_1",
				"id": "plink_1",
				"expires_on": values.payment_link_expires_on,
			},
		)
		expected_expiry = add_to_date(now_datetime(), hours=72)
		self.assertLess(
			abs((get_datetime(values.payment_link_expires_on) - expected_expiry).total_seconds()), 10
		)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Field Order",
					"reference_name": self.order.name,
					"comment_type": "Info",
				},
			)
		)
		self.assertTrue(
			frappe.db.exists(
				"Sync Log",
				{
					"platform": "Razorpay",
					"operation": "Create Payment Link",
					"reference_name": self.order.name,
					"status": "Success",
				},
			)
		)

	def test_missing_contacts_are_left_out_and_not_notified(self):
		frappe.db.set_value("Retailer", self.retailer.name, {"phone": "12345", "email": ""})
		frappe.db.set_single_value("Razorpay Settings", "callback_url", "https://example.com/thanks")
		create_link_for_order(self.order.name)
		(payload,) = self.fake.created()
		self.assertEqual(payload["customer"], {"name": "Payment Link Stores"})
		self.assertEqual(payload["notify"], {"sms": False, "email": False})
		self.assertEqual(
			(payload["callback_url"], payload["callback_method"]), ("https://example.com/thanks", "get")
		)

	def test_no_expiry_when_hours_is_zero(self):
		frappe.db.set_single_value("Razorpay Settings", "link_expiry_hours", 0)
		create_link_for_order(self.order.name)
		self.assertNotIn("expire_by", self.fake.created()[0])
		self.assertIsNone(self.order_values().payment_link_expires_on)

	def test_indian_phone(self):
		for raw in ("9876543210", "+91 98765 43210", "09876543210", "91-9876543210"):
			self.assertEqual(indian_phone(raw), "+919876543210", raw)
		for raw in ("", None, "12345", "1234567890", "+1 415 555 0100"):
			self.assertIsNone(indian_phone(raw), raw)

	# -- reuse / replace -------------------------------------------------

	def test_valid_link_is_returned_again(self):
		first = create_link_for_order(self.order.name)
		second = create_link_for_order(self.order.name)
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(len(self.fake.created()), 1)

	def test_replace_cancels_a_valid_link(self):
		create_link_for_order(self.order.name)
		result = create_link_for_order(self.order.name, replace=True)
		self.assertEqual(result["id"], "plink_2")
		self.assertIn(("POST", "/payment_links/plink_1/cancel"), self.fake.paths())
		self.assertEqual(self.fake.created()[1]["reference_id"], f"{self.order.name}-2")

	def test_expired_link_gets_new_reference(self):
		create_link_for_order(self.order.name)
		self.fake.links["plink_1"]["status"] = "expired"
		frappe.db.set_value(
			"Field Order", self.order.name, "payment_link_expires_on", add_to_date(now_datetime(), hours=-1)
		)

		result = create_link_for_order(self.order.name)
		self.assertEqual(result["id"], "plink_2")
		self.assertEqual(self.fake.created()[1]["reference_id"], f"{self.order.name}-2")
		self.assertNotIn(("POST", "/payment_links/plink_1/cancel"), self.fake.paths())

	def test_open_old_link_is_cancelled_first(self):
		create_link_for_order(self.order.name)
		# e.g. the status was set back by a manager's correction; the old link is still payable.
		frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Expired")
		create_link_for_order(self.order.name)
		paths = self.fake.paths()
		self.assertLess(paths.index(("POST", "/payment_links/plink_1/cancel")), len(paths) - 1)
		self.assertEqual(paths[-1], ("POST", "/payment_links"))
		self.assertEqual(self.fake.links["plink_1"]["status"], "cancelled")
		self.assertEqual(self.order_values().razorpay_payment_link_id, "plink_2")

	def test_third_link_counts_on(self):
		for _ in range(2):
			create_link_for_order(self.order.name)
			frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Expired")
		create_link_for_order(self.order.name)
		refs = [p["reference_id"] for p in self.fake.created()]
		self.assertEqual(refs, [self.order.name, f"{self.order.name}-2", f"{self.order.name}-3"])

	def test_taken_reference_moves_to_next(self):
		self.fake.fail_create.append((400, "Payment link with reference id already exists"))
		result = create_link_for_order(self.order.name)
		self.assertEqual(result["id"], "plink_1")
		self.assertEqual(
			[p["reference_id"] for p in self.fake.created()], [self.order.name, f"{self.order.name}-2"]
		)

	def test_paid_old_link_is_not_replaced(self):
		create_link_for_order(self.order.name)
		self.fake.links["plink_1"]["status"] = "paid"
		frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Expired")
		self.assertRaises(frappe.ValidationError, create_link_for_order, self.order.name)
		self.assertEqual(len(self.fake.created()), 1)

	def test_failed_create_after_cancel_records_cancelled(self):
		create_link_for_order(self.order.name)
		frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Expired")
		self.fake.fail_create.append((400, "The amount is invalid"))
		# The endpoint commits the cancelled state; keep this test inside its transaction.
		with patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			with self.assertRaises(frappe.ValidationError) as ctx:
				create_link_for_order(self.order.name)
		self.assertIn("The amount is invalid", str(ctx.exception))
		self.assertEqual(self.order_values().payment_status, "Link Cancelled")

	# -- link for the confirmation email ----------------------------------

	def doc(self):
		return frappe.get_doc("Field Order", self.order.name)

	def test_confirmation_link_is_created_without_razorpay_notify(self):
		result = link_for_confirmation(self.doc())
		(payload,) = self.fake.created()
		self.assertEqual(payload["notify"], {"sms": False, "email": False})
		self.assertEqual(result["url"], "https://rzp.io/i/plink_1")
		values = self.order_values()
		self.assertEqual(values.payment_status, "Link Sent")
		self.assertEqual(values.razorpay_payment_link_id, "plink_1")

	def test_confirmation_reuses_a_valid_link(self):
		first = create_link_for_order(self.order.name)
		self.assertEqual(link_for_confirmation(self.doc())["id"], first["id"])
		self.assertEqual(len(self.fake.created()), 1)

	def test_confirmation_replaces_an_expired_link(self):
		create_link_for_order(self.order.name)
		self.fake.links["plink_1"]["status"] = "expired"
		frappe.db.set_value("Field Order", self.order.name, "payment_status", "Link Expired")
		self.assertEqual(link_for_confirmation(self.doc())["id"], "plink_2")
		self.assertEqual(self.fake.created()[1]["reference_id"], f"{self.order.name}-2")

	def test_confirmation_gets_no_link_when_not_payable(self):
		cases = {
			"disabled": lambda: frappe.db.set_single_value("Razorpay Settings", "enabled", 0),
			"paid": lambda: frappe.db.set_value("Field Order", self.order.name, "payment_status", "Paid"),
			"partly paid": lambda: frappe.db.set_value(
				"Field Order", self.order.name, "payment_status", "Partially Paid"
			),
		}
		for label, setup in cases.items():
			with self.subTest(label):
				frappe.db.savepoint("no_link")
				setup()
				self.assertIsNone(link_for_confirmation(self.doc()))
				frappe.db.rollback(save_point="no_link")
		self.assertEqual(self.fake.created(), [])

	def test_confirmation_link_failure_does_not_raise(self):
		self.fake.fail_create.append((400, "The amount is invalid"))
		doc = self.doc()
		self.assertIsNone(link_for_confirmation(doc))
		self.assertEqual(self.order_values().payment_status, "Unpaid")
		# The rep must be able to see why the email had no link.
		self.assertEqual(doc.flags.payment_link_error, "The amount is invalid")
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Field Order", "reference_name": self.order.name, "comment_type": "Comment"},
			pluck="content",
		)
		self.assertTrue(any("without a payment link" in c and "The amount is invalid" in c for c in comments))

	# -- refusals ----------------------------------------------------------

	def test_refusals(self):
		cases = {
			"disabled": lambda: frappe.db.set_single_value("Razorpay Settings", "enabled", 0),
			"draft": lambda: frappe.db.set_value("Field Order", self.order.name, "docstatus", 0),
			"zero total": lambda: frappe.db.set_value("Field Order", self.order.name, "grand_total", 0),
			"paid": lambda: frappe.db.set_value("Field Order", self.order.name, "payment_status", "Paid"),
		}
		for label, setup in cases.items():
			with self.subTest(label):
				frappe.db.savepoint("refusal")
				setup()
				self.assertRaises(frappe.ValidationError, create_link_for_order, self.order.name)
				frappe.db.rollback(save_point="refusal")
		self.assertEqual(self.fake.created(), [])

	def test_other_reps_order_is_refused(self):
		other = self._user("rzp.other@example.com", "Field Sales Rep")
		frappe.get_doc({"doctype": "Sales Rep", "user": other}).insert(ignore_permissions=True)
		frappe.set_user(other)
		try:
			self.assertRaises(frappe.PermissionError, create_link_for_order, self.order.name)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.fake.created(), [])

	def test_order_payment_info_for_the_card(self):
		from field_sales_woocommerce.frontend_api import get_order_payment_info

		create_link_for_order(self.order.name)
		frappe.set_user(self.rep_user)
		try:
			info = get_order_payment_info(self.order.name)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(info["payment_status"], "Link Sent")
		self.assertEqual(info["razorpay_payment_link_id"], "plink_1")
		self.assertIs(info["razorpay_enabled"], True)
		self.assertIs(info["can_create_link"], True)
		self.assertEqual(info["whatsapp_number"], "919876543210")
		self.assertNotIn("key_secret", info)

	def test_order_payment_info_checks_permission(self):
		from field_sales_woocommerce.frontend_api import get_order_payment_info

		other = self._user("rzp.info.other@example.com", "Field Sales Rep")
		frappe.get_doc({"doctype": "Sales Rep", "user": other}).insert(ignore_permissions=True)
		frappe.set_user(other)
		try:
			self.assertRaises(frappe.PermissionError, get_order_payment_info, self.order.name)
		finally:
			frappe.set_user("Administrator")

	def test_payment_config_has_only_flags(self):
		frappe.set_user(self.rep_user)
		try:
			self.assertEqual(get_payment_config(), {"enabled": True, "available": True})
		finally:
			frappe.set_user("Administrator")
