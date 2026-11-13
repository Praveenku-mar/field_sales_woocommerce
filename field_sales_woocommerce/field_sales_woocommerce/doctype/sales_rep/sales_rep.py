# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from field_sales_woocommerce.permissions import clear_allowed_reps_cache, get_own_rep, is_admin, is_manager_or_admin

# What a rep earns and may give away: never set by the rep themselves (see _validate_not_own_terms).
OWN_TERMS_FIELDS = ("commission_plan", "commission_active", "max_discount_pct")


def sync_sales_rep_roles(user_name: str, is_sales_manager: bool, is_sales_rep: bool) -> None:
	"""
	Keep User roles in sync with the Sales Rep checkboxes.
	Preserves all other roles on the user.
	"""
	if not user_name or user_name in ("Administrator", "Guest"):
		return
	if not frappe.db.exists("User", user_name):
		return

	user_doc = frappe.get_doc("User", user_name)
	existing_roles = {d.role for d in user_doc.get("roles", [])}
	changed = False

	# Sales Manager role
	if is_sales_manager:
		if "Sales Manager" not in existing_roles and frappe.db.exists("Role", "Sales Manager"):
			user_doc.append("roles", {"role": "Sales Manager"})
			changed = True
	else:
		if "Sales Manager" in existing_roles:
			user_doc.set("roles", [d for d in user_doc.get("roles", []) if d.role != "Sales Manager"])
			changed = True

	# Field Sales Rep role (and "Sales Rep" role if present)
	rep_role_names = [r for r in ("Field Sales Rep", "Sales Rep") if frappe.db.exists("Role", r)]
	if is_sales_rep:
		for r in rep_role_names:
			if r not in existing_roles:
				user_doc.append("roles", {"role": r})
				changed = True
	else:
		for r in ("Field Sales Rep", "Sales Rep"):
			if r in existing_roles:
				user_doc.set("roles", [d for d in user_doc.get("roles", []) if d.role != r])
				changed = True

	if changed:
		user_doc.flags.ignore_permissions = True
		user_doc.save(ignore_permissions=True)


class SalesRep(Document):
	def validate(self):
		if self.max_discount_pct is not None and not 0 <= flt(self.max_discount_pct) <= 100:
			frappe.throw(_("Max Discount % must be between 0 and 100."))

		# Re-pointing a rep at another user would hand that user the rep's team and roles.
		if not self.is_new() and self.has_value_changed("user") and not is_admin():
			frappe.throw(_("Only a System Manager can change the user of a Sales Rep."), frappe.PermissionError)

		self._validate_not_own_terms()

		if self.is_new() and self.user and frappe.db.exists("User", self.user):
			user_roles = set(frappe.get_roles(self.user))
			if not cint(self.sales_manager) and "Sales Manager" in user_roles:
				self.sales_manager = 1
			if not cint(self.sales_rep) and ("Field Sales Rep" in user_roles or "Sales Rep" in user_roles):
				self.sales_rep = 1

		if not self.flags.ignore_role_validation:
			if not cint(self.sales_manager) and not cint(self.sales_rep):
				frappe.throw(_("Select at least one role: Sales Manager or Sales Rep."))

		if not self.flags.ignore_permissions:
			if self.is_new() or self.has_value_changed("sales_manager") or self.has_value_changed("sales_rep"):
				if not is_manager_or_admin():
					frappe.throw(
						_("Only Administrator and Sales Manager can assign roles."),
						frappe.PermissionError,
					)

	def _validate_not_own_terms(self):
		"""
		Separation of duties (as ``commission.statements.check_not_own_commission``): a manager
		may write their own Sales Rep row, but not their own commission plan, commission switch
		or discount limit; someone above them or a System Manager sets those. Checked here so the
		Desk form and REST cannot go around ``commission.api.assign_commission_plan``.
		"""
		if self.is_new() or is_admin() or self.name != get_own_rep():
			return
		changed = [f for f in OWN_TERMS_FIELDS if self.has_value_changed(f)]
		if changed:
			frappe.throw(
				_("You cannot change your own {0}. Ask your manager or a System Manager.").format(
					", ".join(_(self.meta.get_label(f)) for f in changed)
				),
				frappe.PermissionError,
			)

	def on_update(self):
		# The hierarchy may have changed; later checks in this request must see it.
		clear_allowed_reps_cache()
		self.sync_user_roles()

	def sync_user_roles(self):
		sync_sales_rep_roles(self.user, bool(cint(self.sales_manager)), bool(cint(self.sales_rep)))

	def on_trash(self):
		clear_allowed_reps_cache()
