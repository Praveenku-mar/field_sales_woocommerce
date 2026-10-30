# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe import _
from frappe.model.document import Document

MANAGER_ROLES = {"Sales Manager", "System Manager"}
LOCKED_STATEMENT_STATUSES = ("Approved", "Paid")


class CommissionEntry(Document):
	"""
	One commission line. The engine creates Order entries; a manager may add Adjustments by hand.
	Once a statement is approved its entries are frozen: later changes (refunds, cancellations)
	arrive as new Adjustment entries instead.
	"""

	def validate(self):
		self._validate_adjustment()
		self._validate_statement_lock()
		self._set_dedupe_key()

	def _validate_adjustment(self):
		if self.entry_type != "Adjustment":
			return
		if not (self.adjustment_reason or "").strip():
			frappe.throw(_("Adjustment Reason is required for an Adjustment."))
		if not self.flags.from_engine and not self._is_manager():
			frappe.throw(_("Only a Sales Manager or System Manager can create an Adjustment."), frappe.PermissionError)

	@staticmethod
	def _is_manager() -> bool:
		user = frappe.session.user
		return user == "Administrator" or bool(MANAGER_ROLES & set(frappe.get_roles(user)))

	def _validate_statement_lock(self):
		"""An entry in an Approved / Paid statement is frozen. Unlinking on cancel sets flags.allow_unlink."""
		if self.is_new() or self.flags.allow_unlink:
			return
		before = self.get_doc_before_save()
		statement = before.commission_statement if before else None
		if statement and frappe.db.get_value("Commission Statement", statement, "status") in LOCKED_STATEMENT_STATUSES:
			frappe.throw(
				_("{0} is in the {1} Commission Statement {2} and cannot be changed. Add an Adjustment instead.").format(
					self.name, frappe.db.get_value("Commission Statement", statement, "status"), statement
				)
			)

	def _set_dedupe_key(self):
		# Adjustments are deliberately not deduplicated; NULL does not collide in the unique index.
		if self.entry_type == "Order" and self.field_order_item:
			self.dedupe_key = make_dedupe_key(
				self.field_order_item, self.sales_rep, self.beneficiary_type, self.platform_order
			)
		else:
			self.dedupe_key = None


def make_dedupe_key(field_order_item: str, sales_rep: str | None, beneficiary_type: str | None, platform_order: str | None) -> str:
	"""
	Unique key of an Order entry: one per (order line, beneficiary, kind, platform order).

	Hashed (sha1 hex, 40 chars): a rep's name is an email and could overflow the Data length.
	"""
	key = "|".join([field_order_item, sales_rep or "", beneficiary_type or "", platform_order or ""])
	return hashlib.sha1(key.encode()).hexdigest()
