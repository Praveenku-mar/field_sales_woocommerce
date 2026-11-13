# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The mobile contract every Field Sales edition serves: the manifest the app discovers the
edition with, the app login, and the shapes of the list and detail responses. The same
file runs in field_sales, field_sales_woocommerce and field_sales_odoo, so each edition is
held to the same responses.
"""

import importlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate
from frappe.utils.password import update_password

APP = __name__.split(".", 1)[0]
api = importlib.import_module(f"{APP}.api")
mobile = importlib.import_module(f"{APP}.api.mobile")

EDITIONS = {"field_sales": "shopify", "field_sales_woocommerce": "woocommerce", "field_sales_odoo": "odoo"}
PASSWORD = "Fs-Contract#2026-strong"

SHARED_CAPABILITIES = {
	"orders",
	"visits",
	"customers",
	"products",
	"live_stock",
	"dashboard",
	"commission",
	"razorpay_payments",
	"visit_import",
	"retailer_logins",
	"push_notifications",
}

PAGE_KEYS = {"items", "has_more", "next_cursor", "limit"}

# Exact card and detail fields, so every edition returns the same shape.
ORDER_CARD_KEYS = {
	"amount_paid",
	"discount_amount",
	"docstatus",
	"grand_total",
	"location_name",
	"modified",
	"name",
	"net_total",
	"order_date",
	"payment_status",
	"remarks",
	"retailer",
	"retailer_name",
	"sales_rep",
	"sync_status",
	"tax_amount",
	"total_qty",
	"visit",
}
VISIT_CARD_KEYS = {
	"check_in_time",
	"check_out_time",
	"city",
	"modified",
	"name",
	"no_order_reason",
	"notes",
	"order_taken",
	"retailer",
	"retailer_name",
	"sales_rep",
	"sales_rep_name",
	"status",
	"time_on_site_min",
	"visit_date",
	"visit_purpose",
}
CUSTOMER_CARD_KEYS = {
	"city",
	"last_visit",
	"latitude",
	"longitude",
	"modified",
	"name",
	"owner_name",
	"phone",
	"retailer_name",
	"retailer_type",
	"sales_rep",
	"status",
	"sync_status",
}
ORDER_DETAIL_KEYS = {
	"discount_amount",
	"discount_percent",
	"docstatus",
	"grand_total",
	"items",
	"location_id",
	"modified",
	"name",
	"net_total",
	"order_date",
	"payment_status",
	"remarks",
	"retailer",
	"retailer_name",
	"sales_rep",
	"store_checkout_url",
	"store_order_name",
	"subtotal",
	"sync_error",
	"sync_status",
	"tax_amount",
	"tax_percent",
	"visit",
}
ORDER_ITEM_KEYS = {
	"amount",
	"available_qty",
	"name",
	"product",
	"product_title",
	"qty",
	"rate",
	"sku",
	"variant_title",
}
VISIT_DETAIL_KEYS = {
	"check_in_time",
	"check_out_time",
	"distance_from_retailer_m",
	"latitude",
	"location_accuracy_m",
	"longitude",
	"name",
	"no_order_reason",
	"no_order_remarks",
	"notes",
	"order_taken",
	"retailer",
	"retailer_latitude",
	"retailer_longitude",
	"retailer_name",
	"sales_rep",
	"sales_rep_name",
	"status",
	"time_on_site_min",
	"visit_date",
	"within_geofence",
}


class TestMobileContract(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "enable_commission": 1})
		self.rep_user = self.make_user("contract.rep@example.com", ["Field Sales Rep"])
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": self.rep_user}).insert(
			ignore_permissions=True
		).name
		self.retailer = (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Contract Stores",
					"phone": "9876543210",
					"address_line1": "7 Market Street",
					"city": "Salem",
					"sales_rep": self.rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		platform = frappe.get_meta("Platform Product").get_field("platform").options.split("\n")[0]
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": platform,
				"product_title": "Contract product",
				"external_product_id": "97200",
				"external_variant_id": "9720",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer,
				"sales_rep": self.rep,
				"order_date": nowdate(),
				"client_temp_id": "contract-temp-1",
				"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
			}
		).insert(ignore_permissions=True)
		self.visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"retailer": self.retailer,
				"sales_rep": self.rep,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.cache.delete(api._login_failure_key(self.rep_user))
		frappe.db.rollback()

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Contract", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(*roles)
		update_password(user.name, PASSWORD)
		return user.name

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	# -- manifest ----------------------------------------------------------

	def test_manifest_names_this_edition(self):
		manifest = self.as_user("Guest", mobile.get_mobile_manifest)
		self.assertEqual(set(manifest), {"edition", "contract_version", "active", "capabilities"})
		self.assertEqual(manifest["edition"], EDITIONS[APP])
		self.assertEqual(manifest["contract_version"], 1)
		self.assertTrue(manifest["active"])
		self.assertTrue(SHARED_CAPABILITIES <= set(manifest["capabilities"]))

	def test_only_shopify_offers_the_store_connection_editor(self):
		capabilities = mobile.get_mobile_manifest()["capabilities"]
		self.assertEqual("store_connection_settings" in capabilities, APP == "field_sales")

	# -- login -------------------------------------------------------------

	def test_mobile_login_returns_api_keys(self):
		result = self.as_user("Guest", api.mobile_login, self.rep_user, PASSWORD)
		self.assertEqual(result["user"], self.rep_user)
		self.assertTrue(result["api_key"] and result["api_secret"])

	def test_mobile_login_refuses_a_wrong_password(self):
		with self.assertRaises(frappe.AuthenticationError):
			self.as_user("Guest", api.mobile_login, self.rep_user, "wrong-password")

	# -- data --------------------------------------------------------------

	def test_guest_cannot_read_lists(self):
		for fn in (mobile.get_orders_list, mobile.get_visits_list, mobile.get_customers_list):
			with self.assertRaises(frappe.PermissionError):
				self.as_user("Guest", fn)

	def test_list_shapes(self):
		cases = (
			(mobile.get_orders_list, self.order.name, ORDER_CARD_KEYS),
			(mobile.get_visits_list, self.visit.name, VISIT_CARD_KEYS),
			(mobile.get_customers_list, self.retailer, CUSTOMER_CARD_KEYS),
		)
		for fn, name, keys in cases:
			page = self.as_user(self.rep_user, fn)
			self.assertTrue(PAGE_KEYS <= set(page), fn.__name__)
			row = next(r for r in page["items"] if r["name"] == name)
			self.assertEqual(set(row), keys, fn.__name__)

	def test_detail_and_lookup(self):
		detail = self.as_user(self.rep_user, mobile.get_order_detail, self.order.name)
		self.assertEqual(detail["name"], self.order.name)
		found = self.as_user(self.rep_user, mobile.find_order_by_client_temp_id, "contract-temp-1")
		self.assertEqual(found, {"name": self.order.name, "docstatus": 0})
		self.assertEqual(set(detail), ORDER_DETAIL_KEYS)
		self.assertEqual(set(detail["items"][0]), ORDER_ITEM_KEYS)
		visit = self.as_user(self.rep_user, mobile.get_visit_detail, self.visit.name)
		self.assertEqual(set(visit), VISIT_DETAIL_KEYS)
		self.assertEqual(visit["name"], self.visit.name)

	def test_visit_client_temp_id(self):
		"""The app's id finds the visit it created; a retry is refused, and the id never changes."""
		values = {
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "contract-visit-1",
		}
		visit = frappe.get_doc(values).insert(ignore_permissions=True)
		found = self.as_user(self.rep_user, mobile.find_visit_by_client_temp_id, "contract-visit-1")
		self.assertEqual(found, {"name": visit.name, "status": "Planned"})

		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(values).insert(ignore_permissions=True)

		visit.client_temp_id = "changed"
		visit.save(ignore_permissions=True)
		self.assertEqual(visit.client_temp_id, "contract-visit-1")

		visit.db_set("status", "Cancelled")
		self.assertIsNone(
			self.as_user(self.rep_user, mobile.find_visit_by_client_temp_id, "contract-visit-1")
		)

	def test_client_temp_key_lifecycle_and_reuse(self):
		"""Key set on insert; cleared on Field Order cancel and when a Visit becomes Cancelled; a new record may then reuse the id."""
		# Field Order
		order_values = {
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"client_temp_id": "reuse-order-1",
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}
		order = frappe.get_doc(order_values).insert(ignore_permissions=True)
		self.assertEqual(order.client_temp_key, "reuse-order-1")
		self.assertEqual(
			frappe.db.get_value("Field Order", order.name, "client_temp_key"), "reuse-order-1"
		)

		order.submit()
		order.db_set("sync_status", "Draft")
		order.cancel()
		self.assertIsNone(frappe.db.get_value("Field Order", order.name, "client_temp_key"))

		reused_order = frappe.get_doc(order_values).insert(ignore_permissions=True)
		self.assertEqual(reused_order.client_temp_key, "reuse-order-1")
		self.assertEqual(
			frappe.db.get_value("Field Order", reused_order.name, "client_temp_key"), "reuse-order-1"
		)

		# Visit
		visit_values = {
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "reuse-visit-1",
		}
		visit = frappe.get_doc(visit_values).insert(ignore_permissions=True)
		self.assertEqual(visit.client_temp_key, "reuse-visit-1")
		self.assertEqual(frappe.db.get_value("Visit", visit.name, "client_temp_key"), "reuse-visit-1")

		visit.status = "Cancelled"
		visit.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("Visit", visit.name, "client_temp_key"))

		reused_visit = frappe.get_doc(visit_values).insert(ignore_permissions=True)
		self.assertEqual(reused_visit.client_temp_key, "reuse-visit-1")
		self.assertEqual(
			frappe.db.get_value("Visit", reused_visit.name, "client_temp_key"), "reuse-visit-1"
		)

	def test_legacy_duplicate_stays_savable(self):
		"""Duplicates made before the unique key existed (left keyless by the patch) can still be saved."""
		order_values = {
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}
		visit_values = {
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
		}
		for doctype, values in (("Field Order", order_values), ("Visit", visit_values)):
			with self.subTest(doctype):
				first = frappe.get_doc(values).insert(ignore_permissions=True).name
				second = frappe.get_doc(values).insert(ignore_permissions=True).name
				# As the patch leaves them: same id, only the oldest holds the key.
				frappe.db.set_value(doctype, first, {"client_temp_id": "legacy-dup", "client_temp_key": "legacy-dup"})
				frappe.db.set_value(doctype, second, {"client_temp_id": "legacy-dup", "client_temp_key": None})

				doc = frappe.get_doc(doctype, second)
				doc.save(ignore_permissions=True)
				self.assertIsNone(frappe.db.get_value(doctype, second, "client_temp_key"))
				self.assertEqual(frappe.db.get_value(doctype, first, "client_temp_key"), "legacy-dup")

	def test_uncancelled_visit_claims_its_id_again(self):
		values = {
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "uncancel-visit-1",
		}
		visit = frappe.get_doc(values).insert(ignore_permissions=True)
		visit.status = "Cancelled"
		visit.save(ignore_permissions=True)
		visit.status = "Planned"
		visit.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Visit", visit.name, "client_temp_key"), "uncancel-visit-1")

		# Reused by a new visit while cancelled: un-cancelling the old one is refused.
		visit.status = "Cancelled"
		visit.save(ignore_permissions=True)
		frappe.get_doc(values).insert(ignore_permissions=True)
		visit.status = "Planned"
		self.assertRaises(frappe.UniqueValidationError, visit.save, ignore_permissions=True)

	def test_client_temp_key_race_backstop(self):
		"""With the read check patched out, the 2nd concurrent insert raises UniqueValidationError."""
		orig_get_value = frappe.db.get_value

		def bypass_read_check(doctype, filters=None, *args, **kwargs):
			if isinstance(filters, dict) and "client_temp_id" in filters:
				return None
			return orig_get_value(doctype, filters, *args, **kwargs)

		# Field Order race
		order_values = {
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"client_temp_id": "race-order-key",
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}
		frappe.get_doc(order_values).insert(ignore_permissions=True)
		with patch.object(frappe.db, "get_value", side_effect=bypass_read_check):
			with self.assertRaises(frappe.UniqueValidationError):
				frappe.get_doc(order_values).insert(ignore_permissions=True)

		# Visit race
		visit_values = {
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "race-visit-key",
		}
		frappe.get_doc(visit_values).insert(ignore_permissions=True)
		with patch.object(frappe.db, "get_value", side_effect=bypass_read_check):
			with self.assertRaises(frappe.UniqueValidationError):
				frappe.get_doc(visit_values).insert(ignore_permissions=True)

	def test_client_cannot_set_or_change_client_temp_key(self):
		"""REST / client values for client_temp_key are always overwritten by the server."""
		order = frappe.get_doc({
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"client_temp_id": "client-order-1",
			"client_temp_key": "spoofed-key",
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}).insert(ignore_permissions=True)
		self.assertEqual(order.client_temp_key, "client-order-1")
		self.assertEqual(
			frappe.db.get_value("Field Order", order.name, "client_temp_key"), "client-order-1"
		)

		order.client_temp_key = "tampered-key"
		order.save(ignore_permissions=True)
		order.reload()
		self.assertEqual(order.client_temp_key, "client-order-1")

		order_no_id = frappe.get_doc({
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"client_temp_key": "spoofed-no-id",
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}).insert(ignore_permissions=True)
		self.assertIsNone(order_no_id.client_temp_key)
		self.assertIsNone(frappe.db.get_value("Field Order", order_no_id.name, "client_temp_key"))

		visit = frappe.get_doc({
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "client-visit-1",
			"client_temp_key": "spoofed-visit-key",
		}).insert(ignore_permissions=True)
		self.assertEqual(visit.client_temp_key, "client-visit-1")
		self.assertEqual(frappe.db.get_value("Visit", visit.name, "client_temp_key"), "client-visit-1")

		visit.client_temp_key = "tampered-visit-key"
		visit.save(ignore_permissions=True)
		visit.reload()
		self.assertEqual(visit.client_temp_key, "client-visit-1")

		visit_no_id = frappe.get_doc({
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_key": "spoofed-no-id",
		}).insert(ignore_permissions=True)
		self.assertIsNone(visit_no_id.client_temp_key)
		self.assertIsNone(frappe.db.get_value("Visit", visit_no_id.name, "client_temp_key"))

	def test_client_temp_key_patch(self):
		"""Patch fills client_temp_key for the oldest active row; duplicates and cancelled rows keep NULL."""
		run_patch = importlib.import_module(f"{APP}.patches.v1_0.client_temp_key").execute

		o1 = frappe.get_doc({
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"client_temp_id": "patch-dupe-order",
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}).insert(ignore_permissions=True)

		o2 = frappe.get_doc({
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}).insert(ignore_permissions=True)

		o3 = frappe.get_doc({
			"doctype": "Field Order",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"order_date": nowdate(),
			"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
		}).insert(ignore_permissions=True)

		frappe.db.set_value(
			"Field Order",
			o1.name,
			{"creation": "2026-01-01 10:00:00", "client_temp_key": None},
			update_modified=False,
		)
		frappe.db.set_value(
			"Field Order",
			o2.name,
			{"client_temp_id": "patch-dupe-order", "creation": "2026-01-01 11:00:00", "client_temp_key": None},
			update_modified=False,
		)
		frappe.db.set_value(
			"Field Order",
			o3.name,
			{"client_temp_id": "patch-dupe-order", "docstatus": 2, "creation": "2026-01-01 09:00:00", "client_temp_key": None},
			update_modified=False,
		)

		v1 = frappe.get_doc({
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
			"client_temp_id": "patch-dupe-visit",
		}).insert(ignore_permissions=True)

		v2 = frappe.get_doc({
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Planned",
		}).insert(ignore_permissions=True)

		v3 = frappe.get_doc({
			"doctype": "Visit",
			"retailer": self.retailer,
			"sales_rep": self.rep,
			"visit_date": nowdate(),
			"status": "Cancelled",
		}).insert(ignore_permissions=True)

		frappe.db.set_value(
			"Visit",
			v1.name,
			{"creation": "2026-01-01 10:00:00", "client_temp_key": None},
			update_modified=False,
		)
		frappe.db.set_value(
			"Visit",
			v2.name,
			{"client_temp_id": "patch-dupe-visit", "creation": "2026-01-01 11:00:00", "client_temp_key": None},
			update_modified=False,
		)
		frappe.db.set_value(
			"Visit",
			v3.name,
			{"client_temp_id": "patch-dupe-visit", "creation": "2026-01-01 09:00:00", "client_temp_key": None},
			update_modified=False,
		)

		run_patch()

		self.assertEqual(
			frappe.db.get_value("Field Order", o1.name, "client_temp_key"), "patch-dupe-order"
		)
		self.assertIsNone(frappe.db.get_value("Field Order", o2.name, "client_temp_key"))
		self.assertIsNone(frappe.db.get_value("Field Order", o3.name, "client_temp_key"))

		self.assertEqual(
			frappe.db.get_value("Visit", v1.name, "client_temp_key"), "patch-dupe-visit"
		)
		self.assertIsNone(frappe.db.get_value("Visit", v2.name, "client_temp_key"))
		self.assertIsNone(frappe.db.get_value("Visit", v3.name, "client_temp_key"))

		# Safe to re-run
		run_patch()
		self.assertEqual(
			frappe.db.get_value("Field Order", o1.name, "client_temp_key"), "patch-dupe-order"
		)
		self.assertIsNone(frappe.db.get_value("Field Order", o2.name, "client_temp_key"))
		self.assertEqual(
			frappe.db.get_value("Visit", v1.name, "client_temp_key"), "patch-dupe-visit"
		)
		self.assertIsNone(frappe.db.get_value("Visit", v2.name, "client_temp_key"))

	# -- push devices ------------------------------------------------------

	def test_push_device_endpoints(self):
		token = "contract-fcm-token-0123456789abcdef"
		registered = self.as_user(self.rep_user, mobile.register_device, token, "android", "1.0.0")
		self.assertEqual(set(registered), {"enabled", "push_configured"})
		self.assertTrue(registered["enabled"])

		toggled = self.as_user(self.rep_user, mobile.set_device_push_enabled, token, "false")
		self.assertEqual(toggled, {"enabled": False})

		removed = self.as_user(self.rep_user, mobile.unregister_device, token)
		self.assertEqual(removed, {"removed": True})
		self.assertFalse(frappe.db.exists("Mobile Device", {"fcm_token": token}))

	def test_guest_cannot_register_a_device(self):
		with self.assertRaises(frappe.PermissionError):
			self.as_user("Guest", mobile.register_device, "contract-fcm-token-guest-000000", "android")
