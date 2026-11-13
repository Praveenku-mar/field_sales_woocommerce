# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Retailer logins: the invite APIs, the row-level scope of a retailer user, the
session/page gates for the app's login page and My Orders view."""

from unittest.mock import MagicMock, patch

import frappe
from frappe import Redirect
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, set_request

from field_sales_woocommerce.api import (
	_retailer_login_url,
	create_retailer_login,
	revoke_retailer_login,
	send_retailer_password_reset,
)
from field_sales_woocommerce.frontend_api import get_session
from field_sales_woocommerce.permissions import get_own_retailer, is_retailer_user
from field_sales_woocommerce.utils.anonymise import anonymise
from field_sales_woocommerce.www.fieldsales import get_context, has_app_access

PRICE = 100


class TestRetailerLogin(IntegrationTestCase):
	def test_invite_login_url_uses_trusted_request_origin(self):
		request = MagicMock(host="sales.example.com", scheme="https", headers={})
		with (
			patch.object(frappe.local, "request", request, create=True),
			patch.dict(frappe.local.conf, {"field_sales_public_hosts": ["sales.example.com"]}),
		):
			self.assertEqual(
				_retailer_login_url(), "https://sales.example.com/login?redirect-to=%2Ffieldsales"
			)

	def test_invite_login_url_prefers_trusted_forwarded_origin(self):
		request = MagicMock(
			host="fieldsale.local:8000",
			scheme="http",
			headers={
				"X-Forwarded-Host": "sales.example.com",
				"X-Forwarded-Proto": "https",
			},
		)
		with (
			patch.object(frappe.local, "request", request, create=True),
			patch.dict(frappe.local.conf, {"field_sales_public_hosts": ["sales.example.com"]}),
		):
			self.assertEqual(
				_retailer_login_url(), "https://sales.example.com/login?redirect-to=%2Ffieldsales"
			)

	def test_invite_login_url_falls_back_for_malformed_request_host(self):
		request = MagicMock(
			host="evil.com/path?x=<script>", scheme="https", headers={}
		)
		with patch.object(frappe.local, "request", request, create=True):
			self.assertEqual(
				_retailer_login_url(), frappe.utils.get_url("/login?redirect-to=%2Ffieldsales", allow_header_override=False)
			)

	def setUp(self):
		frappe.set_user("Administrator")
		# No store pushes or emails from these tests; everything rolls back after each test.
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": "WooCommerce"})
		self.ensure_retailer_role()

		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "WooCommerce",
				"product_title": "Login product",
				"external_product_id": "9600",
				"external_variant_id": "96",
				"status": "Active",
				"price": PRICE,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

		self.manager_user = self.make_user("rlogin.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		manager_rep = self.make_rep(self.manager_user)
		self.manager_rep = manager_rep.name
		self.outsider_user = self.make_user("rlogin.outsider@example.com", ["Field Sales Rep"])
		self.outsider_rep = self.make_rep(self.outsider_user).name

		self.retailer_a = self.make_retailer("Login Stores", self.manager_rep)
		# Belongs to a rep under the manager, so the manager may invite its login.
		self.retailer_b = self.make_retailer("Other Stores", self.manager_rep)
		self.retailer_c = self.make_retailer("Far Stores", self.outsider_rep)
		self.order_a = self.make_order(self.retailer_a, self.manager_rep)
		self.order_c = self.make_order(self.retailer_c, self.outsider_rep)

		self.retailer_user = self.make_user("rlogin.customer@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", self.retailer_a, "user", self.retailer_user)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def ensure_retailer_role(self):
		"""Migrate creates it from the doctype permissions; tests may run before it did."""
		if not frappe.db.exists("Role", "Field Sales Retailer"):
			frappe.get_doc({"doctype": "Role", "role_name": "Field Sales Retailer", "desk_access": 0}).insert(
				ignore_permissions=True
			)

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		return frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
			ignore_permissions=True
		)

	def make_retailer(self, name, sales_rep):
		return (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": name,
					"phone": "9876543210",
					"address_line1": "5 Market Street",
					"city": "Salem",
					"sales_rep": sales_rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_order(self, retailer, sales_rep):
		return (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": retailer,
					"sales_rep": sales_rep,
					"order_date": nowdate(),
					"items": [{"product": self.product.name, "qty": 1, "rate": PRICE}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def as_user(self, acting_user, fn, *args, **kwargs):
		frappe.set_user(acting_user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def ensure_outgoing_email(self):
		if frappe.get_all("Email Account", filters={"enable_outgoing": 1, "default_outgoing": 1}, limit=1):
			return
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "Retailer Login Test",
				"email_id": "rlogin.outgoing@example.com",
				"smtp_server": "smtp.example.com",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"no_smtp_authentication": 1,
			}
		).insert(ignore_permissions=True)

	# -- who is a retailer user ----------------------------------------------

	def test_is_retailer_user(self):
		self.assertFalse(is_retailer_user(self.manager_user))
		self.assertFalse(is_retailer_user("Administrator"))
		self.assertTrue(is_retailer_user(self.retailer_user))
		self.assertFalse(is_retailer_user("somebody-new@example.com"))

	def test_get_own_retailer(self):
		self.assertEqual(get_own_retailer(self.retailer_user), self.retailer_a)
		self.assertIsNone(get_own_retailer(self.manager_user))

	# -- row-level scope ------------------------------------------------------

	def test_retailer_lists_only_own_row(self):
		names = self.as_user(self.retailer_user, frappe.get_list, "Retailer", pluck="name")
		self.assertEqual(names, [self.retailer_a])

	def test_field_order_list_scoped_to_own_retailer(self):
		names = self.as_user(self.retailer_user, frappe.get_list, "Field Order", pluck="name")
		self.assertEqual(names, [self.order_a])

	def test_has_permission(self):
		own_order = frappe.get_doc("Field Order", self.order_a)
		other_order = frappe.get_doc("Field Order", self.order_c)
		own_retailer = frappe.get_doc("Retailer", self.retailer_a)
		other_retailer = frappe.get_doc("Retailer", self.retailer_c)

		def check(ptype, doc, doctype):
			return self.as_user(self.retailer_user, frappe.has_permission, doctype, ptype, doc=doc)

		self.assertTrue(check("read", own_order, "Field Order"))
		self.assertTrue(check("print", own_order, "Field Order"))
		self.assertFalse(check("read", other_order, "Field Order"))
		self.assertFalse(check("write", own_order, "Field Order"))
		self.assertFalse(check("create", own_retailer, "Retailer"))

		self.assertTrue(check("read", own_retailer, "Retailer"))
		self.assertFalse(check("read", other_retailer, "Retailer"))
		self.assertFalse(check("write", own_retailer, "Retailer"))

	def test_cannot_open_another_retailers_order(self):
		def load():
			frappe.set_user(self.retailer_user)
			try:
				frappe.get_doc("Field Order", self.order_c).check_permission("read")
			finally:
				frappe.set_user("Administrator")

		self.assertRaises(frappe.PermissionError, load)

	def test_staff_sees_unchanged_rows(self):
		# The read-anything rules for staff must not have narrowed (the site holds
		# retailers from demo data and earlier runs, so containment, not equality).
		names = self.as_user(self.manager_user, frappe.get_list, "Retailer", pluck="name")
		for name in (self.retailer_a, self.retailer_b, self.retailer_c):
			self.assertIn(name, names)

	# -- invite ---------------------------------------------------------------

	def test_create_login_new_user(self):
		result = self.as_user(
			self.manager_user, create_retailer_login, self.retailer_b, "buyer@otherstores.example", send_invite=0
		)
		self.assertTrue(result["user_created"])
		user = frappe.get_doc("User", "buyer@otherstores.example")
		self.assertEqual(user.user_type, "Website User")
		self.assertEqual({row.role for row in user.roles}, {"Field Sales Retailer"})
		self.assertEqual(frappe.db.get_value("Retailer", self.retailer_b, "user"), "buyer@otherstores.example")
		# The login email doubles as the contact email when the retailer had none.
		self.assertEqual(frappe.db.get_value("Retailer", self.retailer_b, "email"), "buyer@otherstores.example")

	def test_create_login_requires_outgoing_email_for_invite(self):
		# Remove any default outgoing account (the test rollback brings it back).
		frappe.db.delete("Email Account", {"enable_outgoing": 1, "default_outgoing": 1})
		self.assertRaises(
			frappe.ValidationError,
			self.as_user,
			self.manager_user,
			create_retailer_login,
			self.retailer_b,
			"buyer@otherstores.example",
		)

	def test_invite_email_welcomes_to_the_store(self):
		"""The invite says "Welcome to <store>", not Frappe's own "Welcome to Frappe" mail."""
		self.ensure_outgoing_email()
		frappe.db.set_single_value(
			"Store Connection", {"company_name": None, "shop_name": "Sunrise Stores", "store_url": ""}
		)
		self.as_user(self.manager_user, create_retailer_login, self.retailer_b, "buyer@otherstores.example")
		# Email Queue keeps everything in `message` (the raw email); find ours by recipient.
		name = frappe.get_all(
			"Email Queue",
			filters={"message": ("like", "%buyer@otherstores.example%")},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)[0]
		message = frappe.db.get_value("Email Queue", name, "message")
		self.assertIn("Subject: Welcome to Sunrise Stores", message)
		# The one-time set-password link was made (the email carries it; Frappe's flow).
		self.assertTrue(frappe.db.get_value("User", "buyer@otherstores.example", "reset_password_key"))

	def test_create_login_rejections(self):
		# A plain rep is not a manager.
		self.assertRaises(
			frappe.PermissionError,
			self.as_user,
			self.outsider_user,
			create_retailer_login,
			self.retailer_b,
			"buyer@otherstores.example",
			send_invite=0,
		)
		# Already invited.
		self.assertRaises(
			frappe.DuplicateEntryError,
			self.as_user,
			self.manager_user,
			create_retailer_login,
			self.retailer_a,
			"again@example.com",
			send_invite=0,
		)
		# Staff users cannot become retailer logins (this one has no Sales Rep, so the
		# role check is what stops it).
		staff = self.make_user("rlogin.staff@example.com", ["Field Sales Rep"])
		self.assertRaises(
			frappe.ValidationError,
			self.as_user,
			self.manager_user,
			create_retailer_login,
			self.retailer_b,
			staff,
			send_invite=0,
		)
		# A user already logging in for another retailer.
		self.assertRaises(
			frappe.DuplicateEntryError,
			self.as_user,
			self.manager_user,
			create_retailer_login,
			self.retailer_b,
			self.retailer_user,
			send_invite=0,
		)
		self.assertFalse(frappe.db.exists("User", "again@example.com"))

	def test_retailer_doc_rejects_staff_and_duplicate_user(self):
		doc = frappe.get_doc("Retailer", self.retailer_b)
		doc.user = self.manager_user
		self.assertRaises(frappe.ValidationError, doc.save)
		# Reload: the failed save must leave no half-applied change behind.
		doc = frappe.get_doc("Retailer", self.retailer_b)
		doc.user = self.retailer_user
		self.assertRaises(frappe.DuplicateEntryError, doc.save)
		self.assertIsNone(frappe.db.get_value("Retailer", self.retailer_b, "user"))

	# -- reset ----------------------------------------------------------------

	def test_send_reset_link(self):
		self.ensure_outgoing_email()
		result = self.as_user(self.manager_user, send_retailer_password_reset, self.retailer_a)
		self.assertEqual(result, {"email": self.retailer_user})

	def test_send_reset_link_rejections(self):
		self.ensure_outgoing_email()
		# No login on retailer_b.
		self.assertRaises(
			frappe.ValidationError,
			self.as_user,
			self.manager_user,
			send_retailer_password_reset,
			self.retailer_b,
		)
		# Not a manager.
		self.assertRaises(
			frappe.PermissionError,
			self.as_user,
			self.outsider_user,
			send_retailer_password_reset,
			self.retailer_a,
		)

	# -- revoke ----------------------------------------------------------------

	def test_revoke_and_reinvite(self):
		self.as_user(self.manager_user, revoke_retailer_login, self.retailer_a)
		self.assertIsNone(frappe.db.get_value("Retailer", self.retailer_a, "user"))
		self.assertFalse(frappe.db.get_value("User", self.retailer_user, "enabled"))

		# Inviting the same email again adopts the user and re-enables it.
		self.as_user(
			self.manager_user, create_retailer_login, self.retailer_a, self.retailer_user, send_invite=0
		)
		self.assertTrue(frappe.db.get_value("User", self.retailer_user, "enabled"))
		self.assertEqual(frappe.db.get_value("Retailer", self.retailer_a, "user"), self.retailer_user)

	# -- session and page gates -------------------------------------------------

	def test_get_session_for_retailer(self):
		info = self.as_user(self.retailer_user, get_session)
		self.assertTrue(info["is_retailer"])
		self.assertEqual(info["retailer"]["name"], self.retailer_a)
		self.assertFalse(info["is_manager"])
		self.assertIn("Field Sales Retailer", info["roles"])

	def test_get_session_for_staff(self):
		info = self.as_user(self.manager_user, get_session)
		self.assertFalse(info["is_retailer"])
		self.assertIsNone(info["retailer"])

	def test_get_session_refuses_inactive_or_unlinked_retailer(self):
		frappe.db.set_value("Retailer", self.retailer_a, "status", "Inactive")
		self.assertRaises(frappe.PermissionError, self.as_user, self.retailer_user, get_session)
		frappe.db.set_value("Retailer", self.retailer_a, "status", "Active")
		frappe.db.set_value("Retailer", self.retailer_a, "user", None)
		self.assertRaises(frappe.PermissionError, self.as_user, self.retailer_user, get_session)

	def test_has_app_access(self):
		self.assertTrue(self.as_user(self.retailer_user, has_app_access))

	def test_old_login_link_goes_to_frappe_login(self):
		# The app has no login screen of its own: old links land on Frappe's login.
		for path, home in (("/fieldsales/login", "/fieldsales"), ("/fs/login", "/fs")):
			set_request(method="GET", path=path)
			frappe.set_user("Guest")
			frappe.local.flags.redirect_location = None
			self.assertRaises(Redirect, get_context, frappe._dict())
			self.assertEqual(frappe.local.flags.redirect_location, f"/login?redirect-to={home}")

	def test_guest_redirected_to_app_login(self):
		set_request(method="GET", path="/fieldsales/dashboard")
		frappe.set_user("Guest")
		frappe.local.flags.redirect_location = None
		self.assertRaises(Redirect, get_context, frappe._dict())
		self.assertEqual(
			frappe.local.flags.redirect_location,
			"/login?redirect-to=/fieldsales/dashboard",
		)

	def test_old_login_link_redirects_a_signed_in_user_too(self):
		# Frappe's login sends a signed-in user on to redirect-to, or lets them switch user.
		plain = self.make_user("rlogin.plain@example.com", [])
		set_request(method="GET", path="/fieldsales/login")
		frappe.set_user(plain)
		frappe.local.flags.redirect_location = None
		self.assertRaises(Redirect, get_context, frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/fieldsales")

	def test_other_pages_refuse_user_without_app_access(self):
		plain = self.make_user("rlogin.plain@example.com", [])
		set_request(method="GET", path="/fieldsales/dashboard")
		frappe.set_user(plain)
		self.assertRaises(frappe.PermissionError, get_context, frappe._dict())

	# -- data protection ---------------------------------------------------------

	def test_anonymise_disables_login(self):
		anonymise([self.retailer_a], [])
		self.assertIsNone(frappe.db.get_value("Retailer", self.retailer_a, "user"))
		self.assertFalse(frappe.db.get_value("User", self.retailer_user, "enabled"))
