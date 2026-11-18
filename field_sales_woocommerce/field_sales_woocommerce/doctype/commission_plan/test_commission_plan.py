# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestCommissionPlan(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def plan(self, **values):
		return frappe.get_doc({"doctype": "Commission Plan", "plan_name": "Test plan", "is_active": 1, **values})

	def test_flat_plan_needs_a_rate(self):
		with self.assertRaises(frappe.ValidationError):
			self.plan(rule_type="Flat %", flat_rate=0).insert(ignore_permissions=True)
		self.assertEqual(self.plan(rule_type="Flat %", flat_rate=5).insert(ignore_permissions=True).flat_rate, 5)

	def test_penalty_factor_is_between_0_and_1(self):
		with self.assertRaises(frappe.ValidationError):
			self.plan(rule_type="Flat %", flat_rate=5, penalty_factor=1.5).insert(ignore_permissions=True)

	def test_tiered_slab_plan_needs_a_slab(self):
		with self.assertRaises(frappe.ValidationError):
			self.plan(rule_type="Tiered Slab", slabs=[]).insert(ignore_permissions=True)

	def test_per_order_plan_needs_an_amount(self):
		with self.assertRaises(frappe.ValidationError):
			self.plan(rule_type="Per Order", per_order_amount=0).insert(ignore_permissions=True)
