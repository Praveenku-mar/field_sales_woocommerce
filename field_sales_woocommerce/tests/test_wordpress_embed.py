# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Sign-in inside wp-admin: tokens from the Agamsale WordPress plugin (wordpress_embed.py)."""

import time
from unittest.mock import patch

import frappe
import jwt
from frappe.tests import IntegrationTestCase

from field_sales_woocommerce import wordpress_embed as wp

SECRET = "s" * 48
STORE = "https://shop.example.com"
EMAIL = "wp-embed-rep@example.com"


def token(secret=SECRET, **overrides) -> str:
	"""A token shaped like the plugin's field_sales_token()."""
	now = int(time.time())
	claims = {
		"iss": STORE + "/",
		"aud": wp.AUDIENCE,
		"sub": "7",
		"email": EMAIL,
		"name": "WP Rep",
		"iat": now,
		"exp": now + 300,
	}
	claims.update(overrides)
	claims = {k: v for k, v in claims.items() if v is not None}
	return jwt.encode(claims, secret, algorithm="HS256")


class TestWordPressEmbed(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", EMAIL):
			user = frappe.get_doc(
				{"doctype": "User", "email": EMAIL, "first_name": "WP Rep", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
			user.add_roles("Field Sales Rep")

	def setUp(self):
		frappe.set_user("Administrator")
		settings = frappe.get_single("Store Connection")
		self._saved = (settings.store_url, settings.get_password("wp_shared_secret", raise_exception=False))
		frappe.db.set_single_value("Store Connection", "store_url", STORE)
		self.set_secret(SECRET)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", "store_url", self._saved[0])
		self.set_secret(self._saved[1] or "")

	def set_secret(self, value):
		from frappe.utils.password import remove_encrypted_password, set_encrypted_password

		if value:
			set_encrypted_password("Store Connection", "Store Connection", value, "wp_shared_secret")
		else:
			remove_encrypted_password("Store Connection", "Store Connection", "wp_shared_secret")

	# decode

	def test_valid_token_decodes(self):
		self.assertEqual(wp.decode(token())["email"], EMAIL)

	def test_wrong_secret_expired_or_wrong_store_is_refused(self):
		now = int(time.time())
		for bad in (
			token(secret="x" * 48),
			token(iat=now - 900, exp=now - 600),
			token(iss="https://other.example.com/"),
			token(aud="someone-else"),
			token(email=None),
			token(iat=now, exp=now + 3600),  # longer than the plugin ever issues
		):
			with self.assertRaises(wp.InvalidToken):
				wp.decode(bad)

	def test_no_or_short_secret_refuses_everything(self):
		self.set_secret("short")
		with self.assertRaises(wp.InvalidToken):
			wp.decode(token(secret="short"))

	def test_same_site_url(self):
		self.assertTrue(wp.same_site_url("http://www.Shop.example.com/", "https://shop.example.com"))
		self.assertTrue(wp.same_site_url("https://x.com/store/", "https://x.com/store"))
		self.assertFalse(wp.same_site_url("https://x.com/store", "https://x.com"))
		self.assertFalse(wp.same_site_url("https://x.com:8443", "https://x.com"))
		self.assertFalse(wp.same_site_url("", ""))

	# authenticate (auth_hooks)

	def _authenticate(self, auth_header):
		frappe.set_user("Guest")
		with patch.object(frappe, "get_request_header", lambda name, default=None: auth_header if name == "Authorization" else default):
			wp.authenticate()
		return frappe.session.user

	def test_authenticate_signs_in_the_user_with_that_email(self):
		self.assertEqual(self._authenticate(f"Bearer {token()}"), EMAIL)

	def test_authenticate_ignores_other_bearer_tokens(self):
		other = jwt.encode({"aud": "not-us"}, "k" * 32, algorithm="HS256")
		self.assertEqual(self._authenticate(f"Bearer {other}"), "Guest")
		self.assertEqual(self._authenticate("Bearer opaque-oauth-token"), "Guest")
		self.assertEqual(self._authenticate(None), "Guest")

	def test_authenticate_bad_token_is_401(self):
		with self.assertRaises(frappe.AuthenticationError):
			self._authenticate(f"Bearer {token(secret='x' * 48)}")

	def test_unknown_email_or_no_role_is_403(self):
		with self.assertRaises(frappe.PermissionError):
			self._authenticate(f"Bearer {token(email='nobody-here@example.com')}")
		admin_email = frappe.db.get_value("User", "Administrator", "email")
		if admin_email:
			with self.assertRaises(frappe.PermissionError):
				self._authenticate(f"Bearer {token(email=admin_email)}")

	def test_secret_shorter_than_32_is_rejected_on_save(self):
		settings = frappe.get_single("Store Connection")
		settings.wp_shared_secret = "too-short"
		with self.assertRaises(frappe.ValidationError):
			settings._validate_wp_shared_secret()
