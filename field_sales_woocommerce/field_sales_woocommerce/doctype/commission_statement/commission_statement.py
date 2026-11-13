# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from field_sales_woocommerce.utils.guards import keep_server_fields

MANAGER_ROLES = {"Sales Manager", "System Manager"}
# Set only by commission/statements.py (``_fill`` with ``flags.from_build``) and db_set: the
# amounts must be the entries' amounts, and status / payment only move through approve and Mark Paid.
BUILT_FIELDS = (
	"sales_rep",
	"period_start",
	"period_end",
	"currency",
	"total_sales",
	"slab_rate_applied",
	"gross_commission",
	"reversals",
	"override_commission",
	"net_payable",
	"entries",
)
WORKFLOW_FIELDS = ("status", "approved_by", "paid_on", "payment_reference")


class CommissionStatement(Document):
	"""
	A rep's commission for one period. Draft until a Sales Manager approves (submits) it;
	Mark Paid then records the payment on the submitted statement. Building the statement from
	entries lives in field_sales_woocommerce/commission/statements.py.

	Its amounts and rows are never taken from the client (Desk form, REST): a statement is
	created by Generate Statements and rebuilt by Regenerate.
	"""

	def validate(self):
		self._keep_server_fields()
		self._validate_period()
		self._validate_unique_period()

	def _keep_server_fields(self):
		if not self.flags.from_build:
			if self.is_new():
				frappe.throw(
					_("Commission Statements are built from commission entries. Use Generate Statements."),
					frappe.PermissionError,
				)
			keep_server_fields(self, BUILT_FIELDS)
		keep_server_fields(self, WORKFLOW_FIELDS)

	def before_update_after_submit(self):
		# status / paid_on / payment_reference are allow_on_submit, and validate() does not
		# run for "Update" on a submitted statement: only mark_paid() (db_set) may change them.
		keep_server_fields(self, WORKFLOW_FIELDS)

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
		# Also for a submit from the Desk form or REST, not only approve_statement().
		from field_sales_woocommerce.commission.statements import check_not_own_commission

		check_not_own_commission(self.sales_rep)

	def on_submit(self):
		self.db_set({"status": "Approved", "approved_by": frappe.session.user})

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		# Plain SQL-level update: the entries of an approved statement are otherwise locked.
		frappe.db.set_value(
			"Commission Entry", {"commission_statement": self.name}, "commission_statement", None, update_modified=False
		)
