# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
A manager cannot change the roles of a user they do not manage by making that user a Sales Rep
(sales_rep.check_adoptable_user), neither through create_sales_rep nor by inserting the Sales Rep
directly. The same file runs in every Field Sales edition.
"""

import importlib
import json

import frappe
import frappe.client
from frappe.tests import IntegrationTestCase

APP = __name__.split(".", 1)[0]
create_sales_rep = importlib.import_module(f"{APP}.api").create_sales_rep

OTHER_ROLE = "Adoption Test Role"


class TestSalesRepAdoption(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.flags.field_sales_checked_rep_user = None
		if not frappe.db.exists("Role", OTHER_ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": OTHER_ROLE, "desk_access": 1}).insert(
				ignore_permissions=True
			)
		self.manager = self.make_user("adopt.mgr@example.com", ["Field Sales Rep", "Sales Manager"])
		frappe.get_doc({"doctype": "Sales Rep", "user": self.manager}).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.field_sales_checked_rep_user = None
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		if roles:
			user.add_roles(*roles)
		return user.name

	def roles(self, user):
		frappe.clear_cache(user=user)
		return set(frappe.get_roles(user)) - {"All", "Guest", "Desk User"}

	def as_manager(self, fn, *args, **kwargs):
		frappe.set_user(self.manager)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")
			frappe.flags.field_sales_checked_rep_user = None

	def insert_rep(self, user, **values):
		"""Insert a Sales Rep the way the Desk / REST does (frappe.client.insert)."""
		return frappe.client.insert(
			json.loads(json.dumps({"doctype": "Sales Rep", "user": user, "reports_to": self.manager, **values}))
		)

	# -- refused -----------------------------------------------------------

	def test_manager_cannot_strip_another_managers_role(self):
		other = self.make_user("adopt.other.mgr@example.com", ["Sales Manager", OTHER_ROLE])
		with self.assertRaises(frappe.PermissionError):
			self.as_manager(create_sales_rep, user=other, reports_to=self.manager, sales_manager=0, sales_rep=1)
		self.assertEqual(self.roles(other), {"Sales Manager", OTHER_ROLE})
		self.assertFalse(frappe.db.exists("Sales Rep", other))

	def test_manager_cannot_grant_sales_manager_to_an_unrelated_user(self):
		other = self.make_user("adopt.accounts@example.com", [OTHER_ROLE])
		with self.assertRaises(frappe.PermissionError):
			self.as_manager(create_sales_rep, user=other, reports_to=self.manager, sales_manager=1, sales_rep=1)
		self.assertEqual(self.roles(other), {OTHER_ROLE})

	def test_direct_insert_cannot_change_an_unrelated_users_roles(self):
		other = self.make_user("adopt.direct@example.com", ["Sales Manager", OTHER_ROLE])
		with self.assertRaises(frappe.PermissionError):
			self.as_manager(self.insert_rep, other, sales_manager=0, sales_rep=1)
		self.assertEqual(self.roles(other), {"Sales Manager", OTHER_ROLE})
		self.assertFalse(frappe.db.exists("Sales Rep", other))

	def test_administrator_cannot_be_adopted(self):
		with self.assertRaises(frappe.PermissionError):
			self.as_manager(self.insert_rep, "Administrator", sales_rep=1)

	# -- still allowed -----------------------------------------------------

	def test_manager_creates_new_user_as_manager_and_rep(self):
		result = self.as_manager(
			create_sales_rep,
			user="adopt.new@example.com",
			full_name="New Person",
			reports_to=self.manager,
			sales_manager=1,
			sales_rep=1,
			send_welcome_email=0,
		)
		self.assertTrue(result["user_created"])
		self.assertTrue({"Sales Manager", "Field Sales Rep"} <= self.roles("adopt.new@example.com"))

	def test_manager_adopts_user_with_no_roles(self):
		plain = self.make_user("adopt.plain@example.com", [])
		self.as_manager(create_sales_rep, user=plain, reports_to=self.manager, sales_manager=0, sales_rep=1)
		self.assertIn("Field Sales Rep", self.roles(plain))

	def test_manager_adopts_existing_plain_rep_and_promotes(self):
		rep = self.make_user("adopt.rep@example.com", ["Field Sales Rep"])
		self.as_manager(create_sales_rep, user=rep, reports_to=self.manager, sales_manager=1, sales_rep=1)
		self.assertTrue({"Sales Manager", "Field Sales Rep"} <= self.roles(rep))

	def test_system_manager_adopts_any_user(self):
		other = self.make_user("adopt.any@example.com", ["Sales Manager", OTHER_ROLE])
		create_sales_rep(user=other, reports_to=self.manager, sales_manager=0, sales_rep=1)
		roles = self.roles(other)
		self.assertTrue({OTHER_ROLE, "Field Sales Rep"} <= roles)
		self.assertNotIn("Sales Manager", roles)
