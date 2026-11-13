# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils.password import set_encrypted_password

from field_sales_woocommerce.payments import razorpay
from field_sales_woocommerce.payments.razorpay import (
	API_BASE,
	REQUEST_TIMEOUT,
	WEBHOOK_PATH,
	get_webhook_url,
	test_connection,
)


def _response(status: int, body: dict):
	response = MagicMock(status_code=status)
	response.json.return_value = body
	return response


class TestRazorpaySettings(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value("Razorpay Settings", {"key_id": "rzp_test_abc", "mode": "Test"})
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", "s3cret-key", "key_secret")
		set_encrypted_password("Razorpay Settings", "Razorpay Settings", "hook-secret", "webhook_secret")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	@patch.object(razorpay.requests, "request")
	def test_success_uses_basic_auth_and_timeout(self, mock_request):
		mock_request.return_value = _response(200, {"payment_links": [], "count": 0})
		result = test_connection()
		self.assertTrue(result["ok"])
		mock_request.assert_called_once_with(
			"GET",
			f"{API_BASE}/payment_links",
			params={"count": 1},
			json=None,
			auth=("rzp_test_abc", "s3cret-key"),
			timeout=REQUEST_TIMEOUT,
		)

	@patch.object(razorpay.requests, "request")
	def test_failure_shows_razorpay_description(self, mock_request):
		mock_request.return_value = _response(
			401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
		)
		self.assertEqual(test_connection(), {"ok": False, "message": "Authentication failed"})

	@patch.object(razorpay.requests, "request", side_effect=requests.ConnectTimeout("boom"))
	def test_network_error(self, _mock):
		result = test_connection()
		self.assertFalse(result["ok"])
		self.assertIn("ConnectTimeout", result["message"])

	def test_missing_keys(self):
		frappe.db.set_single_value("Razorpay Settings", "key_id", "")
		self.assertFalse(test_connection()["ok"])

	def _user(self, email: str, role: str) -> str:
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Test", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def test_reps_cannot_test(self):
		frappe.set_user(self._user("rzp-rep@example.com", "Field Sales Rep"))
		self.assertRaises(frappe.PermissionError, test_connection)
		self.assertRaises(frappe.PermissionError, get_webhook_url)
		self.assertFalse(frappe.has_permission("Razorpay Settings", "read"))

	def test_secrets_not_sent_and_webhook_url_filled(self):
		doc = frappe.get_doc("Razorpay Settings")
		doc.run_method("onload")
		data = doc.as_dict()
		self.assertNotEqual(data.get("key_secret"), "s3cret-key")
		self.assertNotEqual(data.get("webhook_secret"), "hook-secret")
		self.assertEqual(doc.webhook_url, frappe.utils.get_url(WEBHOOK_PATH))

	def test_sales_manager_reads_masked_settings(self):
		# What the SPA's Settings tab loads (frappe.client.get), after a save through the form.
		doc = frappe.get_doc("Razorpay Settings")
		doc.key_secret = "s3cret-key"
		doc.save()
		frappe.set_user(self._user("rzp-manager@example.com", "Sales Manager"))
		from frappe.client import get

		data = get("Razorpay Settings", "Razorpay Settings")
		self.assertNotIn("s3cret", str(data.get("key_secret")))
		# Keys, webhook and test are System Manager only.
		self.assertFalse(frappe.has_permission("Razorpay Settings", "write"))
		self.assertRaises(frappe.PermissionError, get_webhook_url)
		self.assertRaises(frappe.PermissionError, test_connection)

	def test_webhook_url_follows_a_tunnel(self):
		# ngrok --host-header keeps Host local; the public address comes in X-Forwarded-*.
		tunnel = MagicMock(
			headers={"X-Forwarded-Host": "abc.ngrok-free.dev", "X-Forwarded-Proto": "https"}
		)
		with (
			patch.object(frappe.local, "request", tunnel, create=True),
			patch.dict(frappe.local.conf, {"field_sales_public_hosts": ["abc.ngrok-free.dev"]}),
		):
			self.assertEqual(razorpay.webhook_url(), f"https://abc.ngrok-free.dev{WEBHOOK_PATH}")

	def test_webhook_url_ignores_an_unknown_forwarded_host(self):
		forged = MagicMock(headers={"X-Forwarded-Host": "evil.example.net", "X-Forwarded-Proto": "https"})
		with patch.object(frappe.local, "request", forged, create=True):
			self.assertEqual(razorpay.webhook_url(), frappe.utils.get_url(WEBHOOK_PATH))

	def test_webhook_url_ignores_a_malformed_forwarded_host(self):
		bad = MagicMock(headers={"X-Forwarded-Host": "evil.com/path?x=<script>"}, host="site.local")
		with patch.object(frappe.local, "request", bad, create=True):
			url = razorpay.webhook_url()
			self.assertEqual(url, frappe.utils.get_url(WEBHOOK_PATH))
		self.assertNotIn("evil.com", url)

	def test_webhook_url_without_a_request_uses_host_name(self):
		with patch.object(frappe.local, "request", None, create=True):
			self.assertEqual(razorpay.webhook_url(), frappe.utils.get_url(WEBHOOK_PATH))
