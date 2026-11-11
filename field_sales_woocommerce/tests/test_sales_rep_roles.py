# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from field_sales_woocommerce.api import create_sales_rep, get_user_for_sales_rep
from field_sales_woocommerce.field_sales_woocommerce.doctype.sales_rep.sales_rep import sync_sales_rep_roles


class TestSalesRepRoles(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.manager_user = self.make_user("roles.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user, sales_manager=1, sales_rep=1)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_user(self, email, roles):
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
		else:
			user = frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
			)
			user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None, sales_manager=0, sales_rep=1):
		return frappe.get_doc(
			{
				"doctype": "Sales Rep",
				"user": user,
				"reports_to": reports_to,
				"sales_manager": sales_manager,
				"sales_rep": sales_rep,
			}
		).insert(ignore_permissions=True)

	def test_create_sales_rep_with_rep_role_only(self):
		frappe.set_user(self.manager_user)
		email = "newrep.only@example.com"
		res = create_sales_rep(
			user=email,
			full_name="New Rep Only",
			reports_to=self.manager_rep.name,
			sales_manager=0,
			sales_rep=1,
			send_welcome_email=0,
		)
		self.assertTrue(res.get("user_created"))
		user_roles = frappe.get_roles(email)
		self.assertIn("Field Sales Rep", user_roles)
		self.assertNotIn("Sales Manager", user_roles)

		rep = frappe.get_doc("Sales Rep", res["name"])
		self.assertEqual(rep.sales_rep, 1)
		self.assertEqual(rep.sales_manager, 0)

	def test_create_sales_rep_with_manager_role_only(self):
		frappe.set_user(self.manager_user)
		email = "newmgr.only@example.com"
		res = create_sales_rep(
			user=email,
			full_name="New Mgr Only",
			reports_to=self.manager_rep.name,
			sales_manager=1,
			sales_rep=0,
			send_welcome_email=0,
		)
		self.assertTrue(res.get("user_created"))
		user_roles = frappe.get_roles(email)
		self.assertIn("Sales Manager", user_roles)
		self.assertNotIn("Field Sales Rep", user_roles)

		rep = frappe.get_doc("Sales Rep", res["name"])
		self.assertEqual(rep.sales_rep, 0)
		self.assertEqual(rep.sales_manager, 1)

	def test_create_sales_rep_with_both_roles(self):
		frappe.set_user(self.manager_user)
		email = "newboth@example.com"
		res = create_sales_rep(
			user=email,
			full_name="New Both Roles",
			reports_to=self.manager_rep.name,
			sales_manager=1,
			sales_rep=1,
			send_welcome_email=0,
		)
		self.assertTrue(res.get("user_created"))
		user_roles = frappe.get_roles(email)
		self.assertIn("Field Sales Rep", user_roles)
		self.assertIn("Sales Manager", user_roles)

		rep = frappe.get_doc("Sales Rep", res["name"])
		self.assertEqual(rep.sales_rep, 1)
		self.assertEqual(rep.sales_manager, 1)

	def test_create_sales_rep_requires_at_least_one_role(self):
		frappe.set_user(self.manager_user)
		email = "noroles@example.com"
		with self.assertRaises(frappe.ValidationError):
			create_sales_rep(
				user=email,
				full_name="No Roles",
				reports_to=self.manager_rep.name,
				sales_manager=0,
				sales_rep=0,
				send_welcome_email=0,
			)

	def test_create_sales_rep_existing_user_assigns_roles(self):
		email = "existing.user@example.com"
		self.make_user(email, ["Desk User"])

		frappe.set_user(self.manager_user)
		res = create_sales_rep(
			user=email,
			reports_to=self.manager_rep.name,
			sales_manager=1,
			sales_rep=1,
			send_welcome_email=0,
		)
		self.assertFalse(res.get("user_created"))
		user_roles = frappe.get_roles(email)
		self.assertIn("Desk User", user_roles)
		self.assertIn("Field Sales Rep", user_roles)
		self.assertIn("Sales Manager", user_roles)

	def test_get_user_for_sales_rep_returns_roles(self):
		email = "lookup.user@example.com"
		self.make_user(email, ["Field Sales Rep", "Sales Manager"])

		frappe.set_user(self.manager_user)
		info = get_user_for_sales_rep(email)
		self.assertTrue(info.get("exists"))
		self.assertIn("Sales Manager", info.get("roles", []))
		self.assertIn("Field Sales Rep", info.get("roles", []))

	def test_sales_rep_doc_syncs_roles_on_save(self):
		email = "synctest@example.com"
		self.make_user(email, ["Field Sales Rep"])

		rep = self.make_rep(email, reports_to=self.manager_rep.name, sales_manager=0, sales_rep=1)
		self.assertNotIn("Sales Manager", frappe.get_roles(email))

		# Update Sales Rep to also be a Sales Manager
		rep.sales_manager = 1
		rep.save()

		self.assertIn("Sales Manager", frappe.get_roles(email))
		self.assertIn("Field Sales Rep", frappe.get_roles(email))

		# Remove Sales Rep role
		rep.sales_rep = 0
		rep.save()

		self.assertIn("Sales Manager", frappe.get_roles(email))
		self.assertNotIn("Field Sales Rep", frappe.get_roles(email))

	def test_sales_rep_doc_validate_requires_at_least_one_role(self):
		email = "docval@example.com"
		self.make_user(email, ["Field Sales Rep"])
		rep = self.make_rep(email, reports_to=self.manager_rep.name, sales_manager=1, sales_rep=1)

		rep.sales_manager = 0
		rep.sales_rep = 0
		with self.assertRaises(frappe.ValidationError):
			rep.save()

	def test_plain_rep_cannot_create_sales_rep(self):
		plain_rep_user = self.make_user("plainrep@example.com", ["Field Sales Rep"])
		frappe.set_user(plain_rep_user)

		with self.assertRaises(frappe.PermissionError):
			create_sales_rep(
				user="blocked.newrep@example.com",
				full_name="Blocked Rep",
				reports_to=self.manager_rep.name,
				sales_manager=0,
				sales_rep=1,
				send_welcome_email=0,
			)

	def test_plain_rep_cannot_lookup_user_for_sales_rep(self):
		plain_rep_user = self.make_user("plainrep.lookup@example.com", ["Field Sales Rep"])
		frappe.set_user(plain_rep_user)

		with self.assertRaises(frappe.PermissionError):
			get_user_for_sales_rep("someone@example.com")

	def test_plain_rep_cannot_update_roles_on_sales_rep(self):
		target_email = "target.rep@example.com"
		self.make_user(target_email, ["Field Sales Rep"])
		rep = self.make_rep(target_email, reports_to=self.manager_rep.name, sales_manager=0, sales_rep=1)

		plain_rep_user = self.make_user("plainrep.editor@example.com", ["Field Sales Rep"])
		frappe.set_user(plain_rep_user)

		rep = frappe.get_doc("Sales Rep", rep.name)
		rep.sales_manager = 1
		with self.assertRaises(frappe.PermissionError):
			rep.save()

	def test_administrator_can_create_and_assign_roles(self):
		frappe.set_user("Administrator")
		email = "admin.created@example.com"
		res = create_sales_rep(
			user=email,
			full_name="Admin Created",
			reports_to=self.manager_rep.name,
			sales_manager=1,
			sales_rep=1,
			send_welcome_email=0,
		)
		self.assertTrue(res.get("user_created"))
		user_roles = frappe.get_roles(email)
		self.assertIn("Field Sales Rep", user_roles)
		self.assertIn("Sales Manager", user_roles)

	def test_sales_manager_can_update_roles_on_sales_rep(self):
		email = "mgr.update@example.com"
		self.make_user(email, ["Field Sales Rep"])
		rep = self.make_rep(email, reports_to=self.manager_rep.name, sales_manager=0, sales_rep=1)

		frappe.set_user(self.manager_user)
		rep = frappe.get_doc("Sales Rep", rep.name)
		rep.sales_manager = 1
		rep.save()

		user_roles = frappe.get_roles(email)
		self.assertIn("Sales Manager", user_roles)
		self.assertIn("Field Sales Rep", user_roles)
