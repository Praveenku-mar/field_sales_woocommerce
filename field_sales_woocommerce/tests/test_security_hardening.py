# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Login lockout, API secret rotation, separation of duties and IDOR guards."""

from unittest.mock import patch

import frappe
import frappe.client
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, get_first_day, get_last_day, nowdate
from frappe.utils.password import get_decrypted_password, update_password

from field_sales_woocommerce import frontend_api
from field_sales_woocommerce.api import (
	LIVE_STOCK_MAX_PRODUCTS,
	LOGIN_MAX_FAILURES,
	_login_failure_key,
	create_retailer_login,
	create_sales_rep,
	get_live_stock,
	get_retailer_summary,
	mobile,
	mobile_login,
	revoke_retailer_login,
	send_retailer_password_reset,
	set_sales_rep_password,
)
from field_sales_woocommerce.commission.api import approve_statement, create_adjustment
from field_sales_woocommerce.commission.statements import build_statement, mark_paid, regenerate_statement
from field_sales_woocommerce.payments import razorpay
from field_sales_woocommerce.utils.search import escape_like

PASSWORD = "Fs-Harden#2026-strong"
NEW_PASSWORD = "Fs-Harden#2026-newer"
PRICE = 100


class TestSecurityHardening(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Everything rolls back after each test; no store pushes from these saves.
		frappe.db.set_single_value(
			"Store Connection", {"enabled": 0, "platform": "WooCommerce", "enable_commission": 1}
		)
		frappe.db.delete("Store Location", {"parenttype": "Store Connection"})
		# Commission adjustments need a site currency; don't depend on what the test site has.
		# Always written: the defaults cache can still claim one after an earlier rollback.
		frappe.db.set_default("currency", "INR")
		if not frappe.db.exists("Role", "Field Sales Retailer"):
			frappe.get_doc({"doctype": "Role", "role_name": "Field Sales Retailer", "desk_access": 0}).insert(
				ignore_permissions=True
			)

		self.manager_user = self.make_user("harden.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("harden.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep)
		# Another team.
		self.other_manager_user = self.make_user("harden.other@example.com", ["Field Sales Rep", "Sales Manager"])
		self.other_rep = self.make_rep(self.other_manager_user)
		self.nobody_user = self.make_user("harden.nobody@example.com", [])

		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "WooCommerce",
				"product_title": "Hardening product",
				"external_product_id": "97100",
				"external_variant_id": "9710",
				"status": "Active",
				"price": PRICE,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.retailer = self.make_retailer("Harden Stores", self.rep)
		self.other_retailer = self.make_retailer("Elsewhere Stores", self.other_rep)

	def tearDown(self):
		frappe.set_user("Administrator")
		for user in (self.rep_user, self.nobody_user, self.manager_user):
			frappe.cache.delete(_login_failure_key(user))
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
		update_password(user.name, PASSWORD)
		return user.name

	def make_rep(self, user, reports_to=None):
		return (
			frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
				ignore_permissions=True
			)
		).name

	def make_retailer(self, name, sales_rep):
		return (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": name,
					"phone": "9876543210",
					"address_line1": "7 Market Street",
					"city": "Salem",
					"sales_rep": sales_rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def order_doc(self, retailer=None, sales_rep=None, **values):
		return frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": retailer or self.retailer,
				"sales_rep": sales_rep or self.rep,
				"order_date": nowdate(),
				"items": [{"product": self.product.name, "qty": 1, "rate": PRICE}],
				**values,
			}
		)

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def secret(self, user):
		return get_decrypted_password("User", user, "api_secret", raise_exception=False)

	def statement(self, sales_rep):
		create_adjustment(sales_rep, 100, "Hardening bonus")
		today = nowdate()
		return build_statement(sales_rep, get_first_day(today), get_last_day(today))

	# -- mobile_login ------------------------------------------------------

	def test_first_login_generates_keys_and_reuses_them(self):
		self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))
		first = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		self.assertEqual(first["user"], self.rep_user)
		self.assertTrue(first["api_key"] and first["api_secret"])
		self.assertEqual(self.secret(self.rep_user), first["api_secret"])

		again = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		self.assertEqual((again["api_key"], again["api_secret"]), (first["api_key"], first["api_secret"]))

	def test_lockout_after_repeated_failures(self):
		for _attempt in range(LOGIN_MAX_FAILURES):
			with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
				self.as_user("Guest", mobile_login, self.rep_user, "wrong-password")
		# Locked: even the right password gets the same generic answer.
		with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
			self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))

	def test_unknown_user_gets_the_same_message(self):
		with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
			self.as_user("Guest", mobile_login, "harden.nobody.at.all@example.com", PASSWORD)

	def test_rejects_user_without_field_sales_role(self):
		with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
			self.as_user("Guest", mobile_login, self.nobody_user, PASSWORD)
		self.assertFalse(frappe.db.get_value("User", self.nobody_user, "api_key"))

	def test_disabled_user_gets_the_same_message(self):
		frappe.db.set_value("User", self.rep_user, "enabled", 0)
		with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
			self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)

	# -- mobile_login: Frappe's sign-in rules -------------------------------

	def system_settings(self, **values):
		"""Overlay System Settings values for ``frappe.get_system_settings``."""
		real = frappe.get_system_settings
		return patch("frappe.get_system_settings", side_effect=lambda key: values[key] if key in values else real(key))

	def two_factor_for_reps(self):
		"""2FA on (OTP App, set up already) for Field Sales Rep; Frappe commits the OTP secret, so commit is patched."""
		from frappe.twofactor import set_default

		frappe.db.set_value("Role", "Field Sales Rep", "two_factor_auth", 1)
		set_default(self.rep_user + "_otplogin", 1)
		return self.system_settings(enable_two_factor_auth=1, two_factor_method="OTP App")

	def otp_for(self, tmp_id):
		import pyotp

		return pyotp.TOTP(frappe.safe_decode(frappe.cache.get(f"{tmp_id}_otp_secret"))).now()

	def test_two_factor_login_needs_the_code_before_keys(self):
		with self.two_factor_for_reps(), patch.object(frappe.db, "commit"):
			first = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
			self.assertEqual(first["message"], "Verification")
			self.assertTrue(first["tmp_id"])
			self.assertNotIn("api_secret", first)
			self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))

			done = self.as_user("Guest", mobile_login, otp=self.otp_for(first["tmp_id"]), tmp_id=first["tmp_id"])
			self.assertEqual(done["user"], self.rep_user)
			self.assertTrue(done["api_key"] and done["api_secret"])

			# The code is used up: the same tmp_id cannot sign in again.
			with self.assertRaises(frappe.AuthenticationError):
				self.as_user("Guest", mobile_login, otp="123456", tmp_id=first["tmp_id"])

	def test_two_factor_login_refuses_a_wrong_code(self):
		with self.two_factor_for_reps(), patch.object(frappe.db, "commit"):
			first = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
			wrong = "000000" if self.otp_for(first["tmp_id"]) != "000000" else "111111"
			with self.assertRaisesRegex(frappe.AuthenticationError, "Invalid login credentials"):
				self.as_user("Guest", mobile_login, otp=wrong, tmp_id=first["tmp_id"])
		self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))

	def test_restricted_ip_is_refused(self):
		frappe.db.set_value("User", self.rep_user, "restrict_ip", "10.99.99.")
		frappe.clear_document_cache("User", self.rep_user)
		with patch.object(frappe.local, "request_ip", "192.0.2.10", create=True):
			with self.assertRaisesRegex(frappe.AuthenticationError, "IP Address"):
				self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))

	def test_login_hours_are_enforced(self):
		# login_after 24 is never reached, so every hour is outside the window.
		frappe.db.set_value("User", self.rep_user, "login_after", 24)
		with self.assertRaisesRegex(frappe.AuthenticationError, "not allowed at this time"):
			self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		self.assertFalse(frappe.db.get_value("User", self.rep_user, "api_key"))

	def test_password_login_can_be_switched_off(self):
		with self.system_settings(disable_user_pass_login=1):
			with self.assertRaisesRegex(frappe.AuthenticationError, "not allowed"):
				self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)

	# -- API secret rotation ---------------------------------------------------

	def test_password_set_rotates_api_secret(self):
		old = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)["api_secret"]
		set_sales_rep_password(self.rep, NEW_PASSWORD, logout_all_sessions=0)
		self.assertNotEqual(self.secret(self.rep_user), old)

	def test_own_password_change_returns_new_keys(self):
		old = self.as_user("Guest", mobile_login, self.rep_user, PASSWORD)
		result = self.as_user(
			self.rep_user, set_sales_rep_password, self.rep, NEW_PASSWORD, logout_all_sessions=0, old_password=PASSWORD
		)
		self.assertEqual(result["api_key"], old["api_key"])
		self.assertNotEqual(result["api_secret"], old["api_secret"])
		self.assertEqual(self.secret(self.rep_user), result["api_secret"])

	# -- commission separation of duties -----------------------------------------

	def test_manager_cannot_adjust_own_commission(self):
		self.assertRaises(
			frappe.PermissionError, self.as_user, self.manager_user, create_adjustment, self.manager_rep, 50, "Me"
		)
		# Their team is fine.
		entry = self.as_user(self.manager_user, create_adjustment, self.rep, 50, "Team bonus")
		self.assertEqual(entry["sales_rep"], self.rep)

	def test_manager_cannot_add_own_entry_through_rest(self):
		entry = frappe.get_doc(
			{
				"doctype": "Commission Entry",
				"entry_type": "Adjustment",
				"beneficiary_type": "Rep",
				"sales_rep": self.manager_rep,
				"status": "Earned",
				"posting_date": nowdate(),
				"currency": frappe.db.get_default("currency") or "INR",
				"amount": 500,
				"adjustment_reason": "Self",
			}
		)
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, entry.insert)

	def test_manager_cannot_approve_regenerate_or_pay_own_statement(self):
		statement = self.statement(self.manager_rep)
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, approve_statement, statement.name)
		self.assertRaises(
			frappe.PermissionError, self.as_user, self.manager_user, regenerate_statement, statement.name
		)
		statement.submit()
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, mark_paid, statement.name)
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "status"), "Approved")

	def test_manager_cannot_submit_own_statement_from_the_form(self):
		statement = self.statement(self.manager_rep)
		doc = frappe.get_doc("Commission Statement", statement.name)
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, doc.submit)
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "docstatus"), 0)

	def test_statement_amounts_and_status_are_not_taken_from_the_client(self):
		statement = self.statement(self.rep)
		values = statement.as_dict()
		values.update({"net_payable": 999999, "gross_commission": 999999, "status": "Paid", "entries": []})
		self.as_user(self.manager_user, frappe.client.save, values)
		saved = frappe.get_doc("Commission Statement", statement.name)
		self.assertEqual(saved.net_payable, statement.net_payable)
		self.assertEqual(saved.status, "Draft")
		self.assertEqual(len(saved.entries), len(statement.entries))

	def test_statement_cannot_be_made_by_hand(self):
		doc = frappe.get_doc(
			{
				"doctype": "Commission Statement",
				"sales_rep": self.rep,
				"period_start": get_first_day(nowdate()),
				"period_end": get_last_day(nowdate()),
				"net_payable": 500,
			}
		)
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, doc.insert)

	def test_approved_statement_status_cannot_be_updated_by_hand(self):
		statement = self.statement(self.rep)
		self.as_user(self.manager_user, approve_statement, statement.name)
		doc = frappe.get_doc("Commission Statement", statement.name)
		doc.status, doc.paid_on, doc.payment_reference = "Paid", nowdate(), "FAKE"
		self.as_user(self.manager_user, doc.save)  # Update after submit
		saved = frappe.db.get_value("Commission Statement", statement.name, ["status", "paid_on"], as_dict=True)
		self.assertEqual((saved.status, saved.paid_on), ("Approved", None))

	# -- own terms on the Sales Rep record ---------------------------------------

	def test_manager_cannot_change_own_commission_terms(self):
		for field, value in (("commission_active", 1), ("max_discount_pct", 50)):
			with self.subTest(field):
				doc = frappe.get_doc("Sales Rep", self.manager_rep)
				doc.set(field, value)
				self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, doc.save)

	def test_manager_may_edit_own_contact_details_and_team_terms(self):
		own = frappe.get_doc("Sales Rep", self.manager_rep)
		own.mobile = "9876500000"
		self.as_user(self.manager_user, own.save)
		team = frappe.get_doc("Sales Rep", self.rep)
		team.max_discount_pct = 10
		self.as_user(self.manager_user, team.save)
		self.assertEqual(frappe.db.get_value("Sales Rep", self.rep, "max_discount_pct"), 10)

	def test_mark_paid_on_another_teams_statement_is_refused(self):
		statement = self.statement(self.other_rep)
		statement.submit()
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, mark_paid, statement.name)
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "status"), "Approved")

	def test_manager_pays_their_teams_statement(self):
		statement = self.statement(self.rep)
		self.as_user(self.manager_user, approve_statement, statement.name)
		result = self.as_user(self.manager_user, mark_paid, statement.name, payment_reference="UTR-9")
		self.assertEqual(result["status"], "Paid")

	# -- Razorpay settings -------------------------------------------------------

	def test_sales_manager_cannot_write_razorpay_settings(self):
		frappe.set_user(self.manager_user)
		self.assertTrue(frappe.has_permission("Razorpay Settings", "read"))
		self.assertFalse(frappe.has_permission("Razorpay Settings", "write"))
		self.assertRaises(frappe.PermissionError, razorpay.test_connection)
		doc = frappe.get_doc("Razorpay Settings")
		doc.key_id = "rzp_test_manager"
		self.assertRaises(frappe.PermissionError, doc.save)

	def test_callback_url_must_be_https(self):
		with patch.dict(frappe.conf, {"developer_mode": 0}):
			for url in ("http://example.com/thanks", "javascript:alert(1)", "https://"):
				doc = frappe.get_doc("Razorpay Settings")
				doc.callback_url = url
				with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
					doc.save()
			doc = frappe.get_doc("Razorpay Settings")
			doc.callback_url = "https://example.com/thanks"
			doc.save()

	# -- retailer logins and reps ------------------------------------------------

	def test_reset_link_for_another_teams_retailer_is_refused(self):
		login = self.make_user("harden.buyer@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", self.other_retailer, "user", login)
		self.assertRaises(
			frappe.PermissionError, self.as_user, self.manager_user, send_retailer_password_reset, self.other_retailer
		)

	def test_revoking_a_retailer_login_rotates_its_api_secret(self):
		login = self.make_user("harden.shop@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", self.retailer, "user", login)
		old = self.as_user("Guest", mobile_login, login, PASSWORD)["api_secret"]
		self.as_user(self.manager_user, revoke_retailer_login, self.retailer)
		self.assertNotEqual(self.secret(login), old)

	def test_disabled_outside_account_is_not_adopted(self):
		outsider = self.make_user("harden.disabled@example.com", [])
		frappe.db.set_value("User", outsider, "enabled", 0)
		self.assertRaises(
			frappe.PermissionError,
			self.as_user,
			self.manager_user,
			create_retailer_login,
			self.retailer,
			outsider,
			send_invite=0,
		)
		self.assertFalse(frappe.db.get_value("User", outsider, "enabled"))

	def test_manager_cannot_turn_a_system_manager_into_a_rep(self):
		admin = self.make_user("harden.sysman@example.com", ["System Manager"])
		self.assertRaises(
			frappe.PermissionError,
			self.as_user,
			self.manager_user,
			create_sales_rep,
			admin,
			reports_to=self.manager_rep,
		)
		self.assertFalse(frappe.db.exists("Sales Rep", {"user": admin}))

	def test_only_system_manager_changes_sales_rep_user(self):
		doc = frappe.get_doc("Sales Rep", self.rep)
		doc.user = self.nobody_user
		self.assertRaises(frappe.PermissionError, self.as_user, self.manager_user, doc.save)

	def test_rep_cannot_reassign_retailer(self):
		# Even to a rep the row permissions would allow (one reporting to them).
		below = self.make_rep(self.make_user("harden.below@example.com", ["Field Sales Rep"]), reports_to=self.rep)
		doc = frappe.get_doc("Retailer", self.retailer)
		doc.sales_rep = below
		self.assertRaisesRegex(
			frappe.PermissionError, "another Sales Rep", self.as_user, self.rep_user, doc.save
		)

	# -- orders --------------------------------------------------------------

	def test_order_on_another_teams_visit_is_refused(self):
		frappe.db.set_value("Retailer", self.other_retailer, "sales_rep", self.other_rep)
		visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.other_rep,
				"retailer": self.other_retailer,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)
		order = self.order_doc(retailer=self.other_retailer, visit=visit.name)
		self.assertRaises(frappe.PermissionError, self.as_user, self.rep_user, order.insert)

		own_visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.rep,
				"retailer": self.retailer,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)
		self.as_user(self.rep_user, self.order_doc(visit=own_visit.name).insert)

	def test_tax_must_be_a_slab(self):
		self.assertRaises(frappe.ValidationError, self.order_doc(tax_percent=7).insert)
		self.order_doc(tax_percent=18).insert()

	def test_rep_cannot_set_the_rate(self):
		order = self.order_doc()
		order.items[0].rate = 1
		self.as_user(self.rep_user, order.insert)
		self.assertEqual(order.items[0].rate, PRICE)

	def test_live_stock_is_capped(self):
		products = [f"P{i}" for i in range(LIVE_STOCK_MAX_PRODUCTS + 1)]
		self.assertRaises(frappe.ValidationError, self.as_user, self.rep_user, get_live_stock, products)

	def test_find_order_by_client_temp_id(self):
		order = self.order_doc(client_temp_id="offline-order-abc-123")
		self.as_user(self.rep_user, order.insert)
		found = self.as_user(self.rep_user, mobile.find_order_by_client_temp_id, "offline-order-abc-123")
		self.assertEqual(found, {"name": order.name, "docstatus": 0})
		# Out of scope and unknown ids are both "not found".
		self.assertIsNone(
			self.as_user(self.other_manager_user, mobile.find_order_by_client_temp_id, "offline-order-abc-123")
		)
		self.assertIsNone(self.as_user(self.rep_user, mobile.find_order_by_client_temp_id, "offline-order-none"))
		# A second insert with the same id is a duplicate, not a second order.
		again = self.order_doc(client_temp_id="offline-order-abc-123")
		self.assertRaises(frappe.DuplicateEntryError, self.as_user, self.rep_user, again.insert)
		# Set once: a later save cannot change it.
		order.reload()
		order.client_temp_id = "something-else"
		order.save()
		self.assertEqual(frappe.db.get_value("Field Order", order.name, "client_temp_id"), "offline-order-abc-123")

	# -- search and lists ------------------------------------------------------

	def test_like_wildcards_are_escaped(self):
		self.assertEqual(escape_like("50%_a\\"), "50\\%\\_a\\\\")
		self.assertEqual(mobile.mobile_search("%%")["results"], [])
		self.assertEqual(mobile.get_customers_list(search="%%")["items"], [])
		self.assertEqual(frontend_api.search("%%"), {"retailers": [], "orders": []})
		# A plain prefix still matches.
		titles = [r.title for r in mobile.mobile_search("Harden", doctype="Retailer")["results"]]
		self.assertIn("Harden Stores", titles)

	def test_retailer_payments_list_runs(self):
		order = self.order_doc()
		order.insert()
		order.db_set({"docstatus": 1, "payment_status": "Paid", "razorpay_payment_id": "pay_harden"})
		login = self.make_user("harden.payer@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", self.retailer, "user", login)
		result = self.as_user(login, mobile.get_retailer_payments_list)
		self.assertEqual([r.order_id for r in result["items"]], [order.name])
		self.assertEqual(result["items"][0].payment_reference, "pay_harden")

	def test_retailer_summary_is_scoped_to_the_team(self):
		order = self.order_doc(retailer=self.other_retailer, sales_rep=self.other_rep)
		order.insert()
		order.db_set("docstatus", 1)
		mine = self.as_user(self.manager_user, get_retailer_summary, self.other_retailer)
		self.assertEqual((mine["orders_30d"], mine["value_30d"]), (0, 0))
		theirs = self.as_user(self.other_manager_user, get_retailer_summary, self.other_retailer)
		self.assertEqual((theirs["orders_30d"], theirs["value_30d"]), (1, order.grand_total))

	def test_customer_outstanding_is_scoped_to_the_team(self):
		order = self.order_doc(retailer=self.other_retailer, sales_rep=self.other_rep)
		order.insert()
		order.db_set("docstatus", 1)
		mine = self.as_user(self.manager_user, mobile.get_customer_detail_records, self.other_retailer)
		self.assertEqual(mine["customer"]["outstanding_balance"], 0)
		theirs = self.as_user(self.other_manager_user, mobile.get_customer_detail_records, self.other_retailer)
		self.assertEqual(theirs["customer"]["outstanding_balance"], order.grand_total)

	def test_team_list_pages_through_reps_without_a_name(self):
		reps = (self.manager_rep, self.rep, self.other_rep)
		frappe.db.set_value("Sales Rep", {"name": ("in", reps)}, "full_name", None, update_modified=False)
		seen, cursor = [], None
		for _page in range(200):
			page = mobile.get_team_list(cursor=cursor, limit=1)
			seen += [row.name for row in page["items"]]
			cursor = page["next_cursor"]
			if not page["has_more"]:
				break
		self.assertEqual(len(seen), len(set(seen)))
		self.assertTrue(set(reps) <= set(seen))

	def test_customer_outstanding_counts_what_is_still_owed(self):
		def submitted(**payment):
			order = self.order_doc()
			order.insert()
			order.db_set({"docstatus": 1, **payment})
			return order.grand_total

		partly = submitted(payment_status="Partially Paid", amount_paid=40)
		no_status = submitted(payment_status=None)  # older rows have none: still owed
		submitted(payment_status="Paid", amount_paid=PRICE)
		result = mobile.get_customer_detail_records(self.retailer)
		self.assertEqual(result["customer"]["outstanding_balance"], flt(partly - 40 + no_status, 2))
