# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Low-severity hardening: trusted invite URLs, webhook event privacy, GPS input bounds,
first shop location, retailer order detail, login lockout identity and count filters."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales_woocommerce.api import (
	LOGIN_MAX_FAILURES,
	_login_failure_key,
	_retailer_login_url,
	check_in,
	check_out,
	mobile,
	mobile_login,
)
from field_sales_woocommerce.utils.geo import LOCATION_FILL_MAX_ACCURACY_M, MAX_ACCURACY_M
from field_sales_woocommerce.utils.urls import public_url

PASSWORD = "Fs-Low#2026-strong"
PRICE = 100
LAT, LNG = 11.0168, 76.9558


class TestSecurityLow(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Everything rolls back after each test; no store pushes from these saves.
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": "WooCommerce"})
		if not frappe.db.exists("Role", "Field Sales Retailer"):
			frappe.get_doc({"doctype": "Role", "role_name": "Field Sales Retailer", "desk_access": 0}).insert(
				ignore_permissions=True
			)
		self.manager_user = self.make_user("low.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("low.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep)
		self.retailer = self.make_retailer("Low Stores")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.cache.delete(_login_failure_key(self.rep_user))
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		if roles:
			user.add_roles(*roles)
		frappe.utils.password.update_password(user.name, PASSWORD)
		return user.name

	def make_rep(self, user, reports_to=None):
		return (
			frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
				ignore_permissions=True
			)
		).name

	def make_retailer(self, name, **values):
		return (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": name,
					"phone": "9876543210",
					"address_line1": "3 Market Street",
					"city": "Salem",
					"sales_rep": self.rep,
					**values,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_visit(self, retailer=None):
		return (
			frappe.get_doc(
				{
					"doctype": "Visit",
					"sales_rep": self.rep,
					"retailer": retailer or self.retailer,
					"visit_date": nowdate(),
					"status": "Planned",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def retailer_location(self, retailer=None):
		return frappe.db.get_value("Retailer", retailer or self.retailer, ["latitude", "longitude"])

	# -- 1. invite / webhook URLs -----------------------------------------

	def test_invite_url_ignores_forged_forwarded_host(self):
		request = MagicMock(
			host="evil.example.net",
			scheme="https",
			headers={"X-Forwarded-Host": "evil.example.net", "X-Forwarded-Proto": "https"},
		)
		with patch.object(frappe.local, "request", request, create=True):
			url = _retailer_login_url()
		self.assertNotIn("evil", url)
		self.assertEqual(url, frappe.utils.get_url("/login?redirect-to=%2Ffieldsales", allow_header_override=False))

	def test_invite_url_uses_a_configured_public_host(self):
		request = MagicMock(
			host=f"{frappe.local.site}:8000",
			scheme="http",
			headers={"X-Forwarded-Host": "Sales.Example.com", "X-Forwarded-Proto": "https"},
		)
		with (
			patch.object(frappe.local, "request", request, create=True),
			patch.dict(frappe.local.conf, {"field_sales_public_hosts": ["sales.example.com"]}),
		):
			self.assertEqual(_retailer_login_url(), "https://Sales.Example.com/login?redirect-to=%2Ffieldsales")

	def test_request_host_matching_the_site_is_used(self):
		request = MagicMock(host=f"{frappe.local.site}:8000", scheme="http", headers={})
		with patch.object(frappe.local, "request", request, create=True):
			self.assertEqual(public_url("/x"), f"http://{frappe.local.site}:8000/x")

	def test_lookalike_host_is_not_trusted(self):
		request = MagicMock(host=f"{frappe.local.site}.evil.example.net", scheme="https", headers={})
		with patch.object(frappe.local, "request", request, create=True):
			self.assertNotIn("evil", public_url("/x"))

	# -- 2. Razorpay Webhook Event -----------------------------------------

	def test_sales_manager_cannot_read_webhook_events(self):
		self.assertFalse(frappe.has_permission("Razorpay Webhook Event", "read", user=self.manager_user))
		self.assertTrue(frappe.has_permission("Razorpay Webhook Event", "read", user="Administrator"))
		frappe.get_doc(
			{
				"doctype": "Razorpay Webhook Event",
				"event_id": "evt_low_1",
				"status": "Processed",
				"payload": "{}",
			}
		).insert(ignore_permissions=True)
		self.assertRaises(
			frappe.PermissionError,
			self.as_user,
			self.manager_user,
			frappe.get_list,
			"Razorpay Webhook Event",
			fields=["name", "payload"],
		)

	# -- 3. GPS input bounds ------------------------------------------------

	def test_check_in_rejects_bad_accuracy(self):
		visit = self.make_visit()
		for accuracy in ("nan", float("nan"), "inf", 1e9, MAX_ACCURACY_M + 1, -1):
			with self.assertRaises(frappe.ValidationError, msg=str(accuracy)):
				self.as_user(self.rep_user, check_in, visit, LAT, LNG, accuracy)
		# Not a number at all: Frappe's type check on the endpoint refuses it first.
		with self.assertRaises((frappe.ValidationError, frappe.FrappeTypeError)):
			self.as_user(self.rep_user, check_in, visit, LAT, LNG, "abc")
		self.assertEqual(frappe.db.get_value("Visit", visit, "status"), "Planned")

	def test_check_in_rejects_bad_coordinates(self):
		visit = self.make_visit()
		for lat, lng in ((91, LNG), (LAT, 181), ("nan", LNG), (LAT, "inf"), (0, 0)):
			with self.assertRaises(frappe.ValidationError, msg=f"{lat},{lng}"):
				self.as_user(self.rep_user, check_in, visit, lat, lng, 10)

	def test_check_out_rejects_bad_accuracy_and_accepts_none(self):
		visit = self.make_visit()
		self.as_user(self.rep_user, check_in, visit, LAT, LNG, 10)
		with self.assertRaises(frappe.ValidationError):
			self.as_user(
				self.rep_user,
				check_out,
				visit,
				LAT,
				LNG,
				"nan",
				no_order_reason="Other",
				no_order_remarks="x",
			)
		result = self.as_user(
			self.rep_user,
			check_out,
			visit,
			LAT,
			LNG,
			None,
			no_order_reason="Other",
			no_order_remarks="Closed",
		)
		self.assertEqual(result["status"], "Completed")

	def test_huge_accuracy_cannot_pass_the_geofence(self):
		# The retailer is ~1 km away: the geofence follows the distance, never the accuracy radius.
		retailer = self.make_retailer("Far Low Stores", latitude=LAT + 0.01, longitude=LNG)
		visit = self.make_visit(retailer)
		result = self.as_user(self.rep_user, check_in, visit, LAT, LNG, MAX_ACCURACY_M)
		self.assertEqual(result["within_geofence"], 0)
		self.assertGreater(result["distance_m"], 1000)

	def test_visit_rejects_bad_accuracy_from_system_manager(self):
		visit = frappe.get_doc("Visit", self.make_visit())
		visit.location_accuracy_m = 1e9
		self.assertRaises(frappe.ValidationError, visit.save)

	# -- 4. first shop location ---------------------------------------------

	def test_first_location_is_filled_from_a_good_fix(self):
		result = self.as_user(
			self.rep_user, check_in, self.make_visit(), LAT, LNG, LOCATION_FILL_MAX_ACCURACY_M
		)
		self.assertTrue(result["retailer_location_set"])
		self.assertEqual(self.retailer_location(), (LAT, LNG))

	def test_first_location_is_not_filled_from_a_rough_or_unknown_fix(self):
		for accuracy in (LOCATION_FILL_MAX_ACCURACY_M + 1, None):
			visit = self.make_visit()
			result = self.as_user(self.rep_user, check_in, visit, LAT, LNG, accuracy)
			self.assertFalse(result["retailer_location_set"])
			self.assertIn("location was not saved", result["warning"])
			self.assertEqual(self.retailer_location(), (0.0, 0.0))
			frappe.db.set_value("Visit", visit, "status", "Cancelled")

	def test_existing_location_is_never_overwritten(self):
		retailer = self.make_retailer("Pinned Low Stores", latitude=LAT + 0.02, longitude=LNG)
		result = self.as_user(self.rep_user, check_in, self.make_visit(retailer), LAT, LNG, 5)
		self.assertFalse(result["retailer_location_set"])
		self.assertEqual(self.retailer_location(retailer), (LAT + 0.02, LNG))
		# Only the visit's own retailer: the other (empty) retailer stays empty.
		self.assertEqual(self.retailer_location(), (0.0, 0.0))

	# -- 5. retailer order detail -------------------------------------------

	def test_retailer_order_detail_hides_sync_internals(self):
		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "WooCommerce",
				"product_title": "Low product",
				"external_product_id": "97300",
				"external_variant_id": "9730",
				"status": "Active",
				"price": PRICE,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		order = (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer,
					"sales_rep": self.rep,
					"order_date": nowdate(),
					"items": [{"product": product.name, "qty": 1, "rate": PRICE}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value(
			"Field Order",
			order,
			{"sync_error": "WooCommerce 422: customer 123 invalid", "location_id": "gid://1"},
		)
		buyer = self.make_user("low.buyer@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", self.retailer, "user", buyer)

		as_buyer = self.as_user(buyer, mobile.get_order_detail, order)
		self.assertIsNone(as_buyer["sync_error"])
		self.assertIsNone(as_buyer["location_id"])
		self.assertEqual(as_buyer["name"], order)

		as_rep = self.as_user(self.rep_user, mobile.get_order_detail, order)
		self.assertIn("422", as_rep["sync_error"])

	# -- 7. login lockout identity and count filters -----------------------

	def test_lockout_is_shared_by_case_and_accent_variants(self):
		local, domain = self.rep_user.split("@")
		variants = [self.rep_user.upper(), f" {self.rep_user} ", f"{local.replace('o', 'ó', 1)}@{domain}"]
		for attempt in range(LOGIN_MAX_FAILURES):
			with self.assertRaises(frappe.AuthenticationError):
				self.as_user("Guest", mobile_login, variants[attempt % len(variants)], "wrong-password")
		with self.assertRaises(frappe.AuthenticationError):
			self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)

	def test_count_rejects_unknown_filter_fields(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown filter field"):
			self.as_user(self.rep_user, mobile.get_mobile_count, "Retailer", filters='{"no_such_field": 1}')
		with self.assertRaisesRegex(frappe.ValidationError, "single value"):
			self.as_user(
				self.rep_user, mobile.get_mobile_count, "Retailer", filters={"status": ["like", "%"]}
			)
		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.rep_user, mobile.get_mobile_count, "Retailer", filters="not json")
		with self.assertRaises(frappe.ValidationError):
			self.as_user(self.rep_user, mobile.get_mobile_count, "Retailer", filters="[1, 2]")

	def test_count_accepts_known_filter_fields(self):
		result = self.as_user(
			self.rep_user, mobile.get_mobile_count, "Retailer", filters='{"name": "%s"}' % self.retailer
		)
		self.assertEqual(result["count"], 1)
