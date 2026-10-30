# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt
# ruff: noqa: RUF001, RUF002

"""
Commission Statements: group a rep's Earned entries for a period into one payable document.

A statement is Draft (docstatus 0) while it can still change, Approved once a Sales Manager submits it,
and Paid after ``mark_paid``. Entries are linked to it through ``commission_statement``; from Approved
on they are frozen (see ``CommissionEntry``) and later refunds arrive as Adjustments.

Tiered Slab plans pay by the period's total sales, which is only known here: building a statement
sets the final rate on those entries (Whole Amount: one rate for everything; Marginal: each slab at
its own rate, shown as one blended rate), keeping the discount penalty as a ratio.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, getdate, nowdate

from field_sales_woocommerce.commission import commission_enabled, require_commission_enabled
from field_sales_woocommerce.commission.engine import penalty_multiplier, slab_rate

MANAGER_ROLES = {"Sales Manager", "System Manager"}


def generate_monthly_statements():
	"""
	Scheduler (1st of the month, 02:00): a Draft statement for last month for each
	commission-active rep who has unstatemented Earned entries or Adjustments.

	Entries that became Earned after an earlier statement was made are picked up here too (anything
	earned up to the period end), so none is stranded. A rep who already has a statement for the
	month is skipped.
	"""
	if not commission_enabled():
		return
	period_end = add_days(get_first_day(getdate(nowdate())), -1)
	period_start = get_first_day(period_end)

	for rep in frappe.get_all("Sales Rep", filters={"commission_active": 1}, pluck="name"):
		try:
			if _statement_exists(rep, period_start) or not _open_entries(rep, period_end):
				continue
			build_statement(rep, period_start, period_end)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Commission statement failed for {rep}")


def build_statement(sales_rep: str, period_start, period_end):
	"""Create and save a Draft Commission Statement from the rep's open entries up to ``period_end``."""
	doc = frappe.new_doc("Commission Statement")
	doc.update({"sales_rep": sales_rep, "period_start": period_start, "period_end": period_end})
	_fill(doc)
	doc.insert(ignore_permissions=True)
	_link_entries(doc)
	return doc


@frappe.whitelist(methods=["POST"])
def regenerate_statement(name: str) -> dict:
	"""
	Rebuild a Draft statement from the current entries (after refunds, new earnings or manual changes).

	Only a Draft can be rebuilt; an approved statement is final.

	:param name: Commission Statement name.
	:return: ``{"name", "net_payable", "entries"}``
	"""
	require_commission_enabled()
	_require_manager()
	doc = frappe.get_doc("Commission Statement", name, for_update=True)
	if doc.docstatus != 0 or doc.status != "Draft":
		frappe.throw(_("Only a Draft Commission Statement can be regenerated."))

	frappe.db.set_value(
		"Commission Entry", {"commission_statement": doc.name}, "commission_statement", None, update_modified=False
	)
	_fill(doc)
	doc.save(ignore_permissions=True)
	_link_entries(doc)
	return {"name": doc.name, "net_payable": doc.net_payable, "entries": len(doc.entries)}


@frappe.whitelist(methods=["POST"])
def mark_paid(name: str, paid_on: str | None = None, payment_reference: str | None = None) -> dict:
	"""
	Record payment of an Approved statement.

	:param name: Commission Statement name.
	:param paid_on: Payment date; today if omitted.
	:param payment_reference: Bank / voucher reference.
	:return: ``{"name", "status", "paid_on"}``
	"""
	_require_manager()
	doc = frappe.get_doc("Commission Statement", name, for_update=True)
	if doc.docstatus != 1 or doc.status != "Approved":
		frappe.throw(_("Only an Approved Commission Statement can be marked as paid."))

	paid_on = getdate(paid_on) if paid_on else getdate(nowdate())
	doc.db_set({"status": "Paid", "paid_on": paid_on, "payment_reference": payment_reference or ""})
	return {"name": doc.name, "status": "Paid", "paid_on": str(paid_on)}


# ----------------------------------------------------------------------
# Building
# ----------------------------------------------------------------------


def _require_manager():
	user = frappe.session.user
	if user != "Administrator" and not MANAGER_ROLES & set(frappe.get_roles(user)):
		frappe.throw(_("Only a Sales Manager or System Manager can do this."), frappe.PermissionError)


def _statement_exists(sales_rep: str, period_start) -> bool:
	return bool(
		frappe.db.exists(
			"Commission Statement", {"sales_rep": sales_rep, "period_start": period_start, "docstatus": ("!=", 2)}
		)
	)


def _open_entries(sales_rep: str, period_end) -> list:
	"""Earned Order entries and Adjustments of the rep, up to ``period_end``, not on any statement."""
	common = {"sales_rep": sales_rep, "status": "Earned", "commission_statement": ("is", "not set")}
	fields = [
		"name",
		"entry_type",
		"beneficiary_type",
		"platform_order",
		"currency",
		"commission_plan",
		"base_amount",
		"discount_pct",
		"base_rate",
		"effective_rate",
		"amount",
		"reversed_amount",
		"calculation_note",
	]
	orders = frappe.get_all(
		"Commission Entry",
		filters={**common, "entry_type": "Order", "earned_on": ("<=", period_end)},
		fields=fields,
		order_by="earned_on, creation",
	)
	adjustments = frappe.get_all(
		"Commission Entry",
		filters={**common, "entry_type": "Adjustment", "posting_date": ("<=", period_end)},
		fields=fields,
		order_by="posting_date, creation",
	)
	entries = orders + adjustments

	# A statement is in one currency; entries in another stay open for a statement of their own.
	if entries:
		entries = [e for e in entries if e.currency == entries[0].currency]
	return entries


def _fill(doc) -> None:
	"""Set the statement's rows and totals from the rep's open entries (applying slabs first)."""
	entries = _open_entries(doc.sales_rep, doc.period_end)
	rep_orders = [e for e in entries if e.entry_type == "Order" and e.beneficiary_type == "Rep"]
	slab_rate_applied = _apply_slabs(rep_orders, doc.sales_rep)

	precision = cint(frappe.get_precision("Commission Statement", "net_payable"))
	gross = reversals = override = 0.0
	for e in entries:
		is_rep = e.beneficiary_type == "Rep"
		if e.entry_type == "Order":
			if is_rep:
				gross += flt(e.amount)
				reversals += flt(e.reversed_amount)
			else:
				override += flt(e.amount) - flt(e.reversed_amount)
		elif not is_rep:
			override += flt(e.amount)
		elif flt(e.amount) < 0:
			reversals += -flt(e.amount)
		else:
			gross += flt(e.amount)

	doc.currency = entries[0].currency if entries else frappe.db.get_default("currency")
	doc.total_sales = flt(sum(flt(e.base_amount) for e in rep_orders), precision)
	doc.slab_rate_applied = slab_rate_applied
	doc.gross_commission = flt(gross, precision)
	doc.reversals = flt(reversals, precision)
	doc.override_commission = flt(override, precision)
	doc.net_payable = flt(gross - reversals + override, precision)

	doc.set("entries", [])
	for e in entries:
		is_order = e.entry_type == "Order"
		doc.append(
			"entries",
			{
				"commission_entry": e.name,
				"entry_type": e.entry_type,
				"beneficiary_type": e.beneficiary_type,
				"platform_order": e.platform_order,
				"currency": e.currency,
				"amount": e.amount,
				"reversed_amount": e.reversed_amount if is_order else 0,
				"net_amount": flt(e.amount) - (flt(e.reversed_amount) if is_order else 0),
			},
		)


def _link_entries(doc) -> None:
	names = [row.commission_entry for row in doc.entries]
	if names:
		frappe.db.set_value(
			"Commission Entry", {"name": ("in", names)}, "commission_statement", doc.name, update_modified=False
		)


# ----------------------------------------------------------------------
# Slabs
# ----------------------------------------------------------------------


def _apply_slabs(rep_orders: list, sales_rep: str) -> float:
	"""
	Set the final rate on the Tiered Slab entries from the period's total sales.

	:return: Sales-weighted rate applied (0 if the statement has no slab entries).
	"""
	by_plan: dict[str, list] = {}
	for e in rep_orders:
		if e.commission_plan and frappe.db.get_value("Commission Plan", e.commission_plan, "rule_type") == "Tiered Slab":
			by_plan.setdefault(e.commission_plan, []).append(e)
	if not by_plan:
		return 0

	max_discount = frappe.db.get_value("Sales Rep", sales_rep, "max_discount_pct")
	amount_precision = cint(frappe.get_precision("Commission Entry", "amount"))
	weighted = base_total = 0.0

	for plan_name, group in by_plan.items():
		plan = frappe.get_doc("Commission Plan", plan_name)
		total = sum(flt(e.base_amount) for e in group)
		if plan.slab_mode == "Marginal":
			rate = marginal_commission(plan, total) / total * 100 if total else 0
			mode = "marginal"
		else:
			rate = slab_rate(plan, total)
			mode = "whole amount"

		for e in group:
			ratio = _penalty_ratio(e, plan, max_discount)
			old_amount, old_reversed = flt(e.amount), flt(e.reversed_amount)
			effective = flt(rate * ratio, 4)
			amount = flt(flt(e.base_amount) * effective / 100, amount_precision)
			reversed_amount = flt(amount * old_reversed / old_amount, amount_precision) if old_amount else old_reversed
			note = (
				f"Slab ({mode}) {flt(rate, 4):g}% on period sales {total:,.2f}"
				+ (f" × penalty {flt(ratio, 4):g}" if ratio != 1 else "")
				+ f" = {effective:g}% of {flt(e.base_amount):,.2f} = {amount:,.2f}"
			)
			values = {
				"base_rate": flt(rate, 4),
				"effective_rate": effective,
				"amount": amount,
				"reversed_amount": reversed_amount,
				"calculation_note": note,
			}
			frappe.db.set_value("Commission Entry", e.name, values, update_modified=False)
			e.update(values)

		weighted += rate * total
		base_total += total

	return flt(weighted / base_total, 4) if base_total else 0


def _penalty_ratio(entry, plan, max_discount) -> float:
	"""Discount penalty kept as a ratio: effective / base rate as stored, or recomputed if the base rate was 0."""
	if flt(entry.base_rate) > 0:
		return flt(entry.effective_rate) / flt(entry.base_rate)
	return penalty_multiplier(entry.discount_pct, max_discount, plan.penalty_factor)


def marginal_commission(plan, total: float) -> float:
	"""Commission on ``total`` when each slab pays its own rate on the part of the total inside it."""
	commission = 0.0
	for row in sorted(plan.slabs, key=lambda r: flt(r.from_amount)):
		upper = flt(row.to_amount) or float("inf")
		portion = max(0.0, min(flt(total), upper) - flt(row.from_amount))
		commission += portion * flt(row.rate) / 100
	return commission
