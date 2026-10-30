# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

MANAGER_ROLES = {"Sales Manager", "System Manager"}


class CommissionStatement(Document):
	"""
	A rep's commission for one period. Draft until a Sales Manager approves (submits) it;
	Mark Paid then records the payment on the submitted statement. Building the statement from
	entries lives in field_sales/commission/statements.py.
	"""

	def validate(self):
		self._validate_period()
		self._validate_unique_period()

	def _validate_period(self):
		if self.period_start and self.period_end and self.period_end < self.period_start:
			frappe.throw(_("Period End cannot be before Period Start."))

	def _validate_unique_period(self):
		existing = frappe.db.exists(
			"Commission Statement",
			{
				"sales_rep": self.sales_rep,
				"period_start": self.period_start,
				"docstatus": ("!=", 2),
				"name": ("!=", self.name or ""),
			},
		)
		if existing:
			frappe.throw(
				_("{0} already has a Commission Statement for the period starting {1}: {2}").format(
					self.sales_rep, self.period_start, existing
				)
			)

	def before_submit(self):
		user = frappe.session.user
		if user != "Administrator" and not MANAGER_ROLES & set(frappe.get_roles(user)):
			frappe.throw(_("Only a Sales Manager or System Manager can approve a Commission Statement."), frappe.PermissionError)

	def on_submit(self):
		self.db_set({"status": "Approved", "approved_by": frappe.session.user})

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		# Plain SQL-level update: the entries of an approved statement are otherwise locked.
		frappe.db.set_value(
			"Commission Entry", {"commission_statement": self.name}, "commission_statement", None, update_modified=False
		)
