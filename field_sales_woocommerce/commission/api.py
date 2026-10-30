# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Whitelisted commission endpoints for the app's screens.

A rep sees their own numbers; a manager sees the team's (own rep plus all reports below, see
``field_sales_woocommerce.permissions``). Entry lists go through ``frappe.get_list``, so row permissions apply.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, get_first_day, get_last_day, getdate, nowdate

from field_sales_woocommerce.commission import require_commission_enabled
from field_sales_woocommerce.commission.statements import (
	MANAGER_ROLES,
	_open_entries,
	_require_manager,
	_statement_exists,
	build_statement,
)
from field_sales_woocommerce.permissions import get_allowed_reps, get_own_rep, is_admin

PERIODS = ("this_month", "last_month", "this_year", "all")
MAX_PAGE_LENGTH = 2500


@frappe.whitelist()
def get_my_commission_summary(period: str = "this_month") -> dict:
	"""
	The signed-in rep's commission for ``period`` ("this_month", "last_month", "this_year" or "all").

	:return: ``{"has_rep": False}`` when the user is not linked to a Sales Rep, else
		``{"has_rep", "currency", "earned", "pending", "reversed", "net", "next_slab_gap", "top_products"}``.
		``earned`` is before reversals; ``next_slab_gap`` is ``{"amount", "rate"}`` for a Tiered Slab
		plan (sales still needed this month to reach the next slab) or ``None``;
		``top_products`` are the 5 products with the most commission.
	"""
	rep = get_own_rep()
	if not rep:
		return {"has_rep": False}

	start, end = _period_range(period)
	totals = _totals([rep], start, end).get(rep, _empty_totals())
	return {
		"has_rep": True,
		"currency": frappe.db.get_default("currency"),
		"earned": totals["earned"],
		"pending": totals["pending"],
		"reversed": totals["reversed"],
		"net": totals["net"],
		"next_slab_gap": _next_slab_gap(rep),
		"top_products": _top_products(rep, start, end),
	}


@frappe.whitelist()
def get_team_commission(period: str = "this_month") -> list[dict]:
	"""
	Commission totals per rep for ``period``, for the signed-in manager's team (everyone for admins).

	:return: ``[{"sales_rep", "full_name", "commission_plan", "commission_active", "earned", "pending",
		"reversed", "override", "net"}, ...]`` sorted by net, highest first. Every rep of the team is
		listed, with zeros when they have no commission.
	"""
	if is_admin():
		reps = frappe.get_all("Sales Rep", pluck="name")
	else:
		reps = get_allowed_reps()
	if not reps:
		return []

	start, end = _period_range(period)
	totals = _totals(reps, start, end)
	info = {
		r.name: r
		for r in frappe.get_all(
			"Sales Rep", filters={"name": ("in", reps)}, fields=["name", "full_name", "commission_plan", "commission_active"]
		)
	}
	rows = [
		{
			"sales_rep": rep,
			"full_name": info[rep].full_name if rep in info else None,
			"commission_plan": info[rep].commission_plan if rep in info else None,
			"commission_active": cint(info[rep].commission_active) if rep in info else 0,
			**totals.get(rep, _empty_totals()),
		}
		for rep in reps
	]
	return sorted(rows, key=lambda r: r["net"], reverse=True)


@frappe.whitelist()
def get_commission_entries(filters: dict | str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""
	One page of Commission Entries the user may see, newest first.

	:param filters: Standard filters, as a dict or JSON string (e.g. ``{"status": "Earned"}``).
	:param page: 1-based page number.
	:param page_length: Rows per page (at most 2500).
	:return: ``{"entries": [...], "page", "page_length", "total"}``
	"""
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	page = max(1, int(page))
	page_length = min(MAX_PAGE_LENGTH, max(1, int(page_length)))

	total = frappe.get_list("Commission Entry", filters=filters or {}, fields=[{"COUNT": "*", "as": "total"}])
	entries = frappe.get_list(
		"Commission Entry",
		filters=filters or {},
		fields=[
			"name",
			"entry_type",
			"beneficiary_type",
			"sales_rep",
			"source_rep",
			"status",
			"posting_date",
			"earned_on",
			"platform_order",
			"source_order",
			"product",
			"base_amount",
			"effective_rate",
			"amount",
			"reversed_amount",
			"currency",
			"commission_statement",
			"calculation_note",
			"adjustment_reason",
		],
		order_by="posting_date desc, creation desc",
		start=(page - 1) * page_length,
		page_length=page_length,
	)
	return {"entries": entries, "page": page, "page_length": page_length, "total": total[0].total if total else 0}


@frappe.whitelist()
def get_commission_statements(filters: dict | str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""
	One page of Commission Statements the user may see, newest period first.

	:param filters: Standard filters, as a dict or JSON string (e.g. ``{"status": "Draft"}``).
	:param page: 1-based page number.
	:param page_length: Rows per page (at most 2500).
	:return: ``{"statements": [...], "page", "page_length", "total"}``; each row has ``full_name`` of the rep.
	"""
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	page = max(1, int(page))
	page_length = min(MAX_PAGE_LENGTH, max(1, int(page_length)))

	total = frappe.get_list("Commission Statement", filters=filters or {}, fields=[{"COUNT": "*", "as": "total"}])
	statements = frappe.get_list(
		"Commission Statement",
		filters=filters or {},
		fields=[
			"name",
			"sales_rep",
			"sales_rep.full_name as full_name",
			"period_start",
			"period_end",
			"status",
			"docstatus",
			"currency",
			"total_sales",
			"net_payable",
			"paid_on",
		],
		order_by="period_start desc, creation desc",
		start=(page - 1) * page_length,
		page_length=page_length,
	)
	return {
		"statements": statements,
		"page": page,
		"page_length": page_length,
		"total": total[0].total if total else 0,
	}


@frappe.whitelist()
def get_commission_statement(name: str) -> dict:
	"""
	One Commission Statement with its entries.

	:return: The document as a dict plus ``full_name`` (the rep's) and ``can_manage`` (whether the user
		may regenerate, approve and pay statements).
	"""
	doc = frappe.get_doc("Commission Statement", name)
	doc.check_permission("read")
	data = doc.as_dict()
	data["full_name"] = frappe.db.get_value("Sales Rep", doc.sales_rep, "full_name")
	data["can_manage"] = _can_manage()
	return data


@frappe.whitelist(methods=["POST"])
def approve_statement(name: str) -> dict:
	"""
	Approve a Draft Commission Statement (submits it; its entries are frozen from then on).

	:param name: Commission Statement name.
	:return: ``{"name", "status", "net_payable"}``
	"""
	_require_manager()
	doc = frappe.get_doc("Commission Statement", name, for_update=True)
	if doc.docstatus != 0:
		frappe.throw(_("Only a Draft Commission Statement can be approved."))
	doc.submit()
	doc.reload()
	return {"name": doc.name, "status": doc.status, "net_payable": doc.net_payable}


# ----------------------------------------------------------------------
# Management (Sales Manager / System Manager)
# ----------------------------------------------------------------------

PLAN_FIELDS = (
	"is_active",
	"rule_type",
	"commission_on",
	"flat_rate",
	"default_rate",
	"slab_mode",
	"per_order_amount",
	"penalty_factor",
	"override_levels",
	"override_pct",
	"description",
)
RATE_FIELDS = ("match_type", "product", "match_value", "rate")
SLAB_FIELDS = ("from_amount", "to_amount", "rate")
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@frappe.whitelist()
def get_commission_plans() -> list[dict]:
	"""
	Every Commission Plan with its rate and slab rows and the number of reps using it.

	:return: ``[{<plan fields>, "name", "rates": [...], "slabs": [...], "reps": int}, ...]`` by plan name.
	"""
	_require_manager()
	frappe.has_permission("Commission Plan", "read", throw=True)

	used = {
		row.commission_plan: row.total
		for row in frappe.get_all(
			"Sales Rep",
			filters={"commission_plan": ("is", "set")},
			fields=["commission_plan", {"COUNT": "*", "as": "total"}],
			group_by="commission_plan",
		)
	}
	plans = []
	for name in frappe.get_all("Commission Plan", pluck="name", order_by="plan_name asc"):
		doc = frappe.get_doc("Commission Plan", name)
		plans.append(_plan_dict(doc) | {"reps": used.get(name, 0)})
	return plans


@frappe.whitelist(methods=["POST"])
def save_commission_plan(doc: dict | str) -> dict:
	"""
	Create a Commission Plan, or update the one named ``doc["name"]``.

	The plan name is the document name, so an existing plan cannot be renamed here. Rows that do not
	belong to the plan's rule type (rates outside Product Rate, slabs outside Tiered Slab) are dropped.
	Validation errors of the plan are raised as they are.

	:param doc: Plan fields (``plan_name``, ``rule_type``, ...) with ``rates`` and ``slabs`` as lists of rows.
	:return: The saved plan, as ``get_commission_plans`` returns it.
	"""
	require_commission_enabled()
	_require_manager()
	if isinstance(doc, str):
		doc = json.loads(doc or "{}")

	name = doc.get("name")
	if name:
		plan = frappe.get_doc("Commission Plan", name)
		plan.check_permission("write")
	else:
		plan_name = (doc.get("plan_name") or "").strip()
		if not plan_name:
			frappe.throw(_("Plan Name is required."))
		plan = frappe.new_doc("Commission Plan")
		plan.plan_name = plan_name
		plan.check_permission("create")

	for field in PLAN_FIELDS:
		if field in doc:
			plan.set(field, doc[field])

	rule_type = plan.rule_type
	plan.set("rates", [])
	plan.set("slabs", [])
	if rule_type == "Product Rate":
		for row in doc.get("rates") or []:
			plan.append("rates", {f: row.get(f) for f in RATE_FIELDS if row.get(f) not in (None, "")})
	elif rule_type == "Tiered Slab":
		for row in doc.get("slabs") or []:
			plan.append("slabs", {f: row.get(f) for f in SLAB_FIELDS if row.get(f) not in (None, "")})

	plan.save()
	return _plan_dict(plan) | {"reps": frappe.db.count("Sales Rep", {"commission_plan": plan.name})}


@frappe.whitelist(methods=["POST"])
def assign_commission_plan(sales_rep: str, commission_plan: str | None = None, commission_active: int = 0) -> dict:
	"""
	Set a rep's Commission Plan and whether they earn commission.

	:param sales_rep: Sales Rep name.
	:param commission_plan: An active Commission Plan, or empty to clear it.
	:param commission_active: 1 to earn commission (needs a plan).
	:return: ``{"sales_rep", "commission_plan", "commission_active"}``
	"""
	require_commission_enabled()
	_require_manager()
	rep = frappe.get_doc("Sales Rep", sales_rep)
	rep.check_permission("write")

	commission_active = cint(commission_active)
	if commission_plan:
		if not frappe.db.get_value("Commission Plan", commission_plan, "is_active"):
			frappe.throw(_("Commission Plan {0} is not active.").format(commission_plan))
	elif commission_active:
		frappe.throw(_("Choose a Commission Plan to turn commission on."))

	rep.commission_plan = commission_plan or None
	rep.commission_active = commission_active
	rep.save()
	return {"sales_rep": rep.name, "commission_plan": rep.commission_plan, "commission_active": rep.commission_active}


@frappe.whitelist(methods=["POST"])
def create_adjustment(
	sales_rep: str,
	amount: float,
	reason: str,
	posting_date: str | None = None,
	beneficiary_type: str = "Rep",
) -> dict:
	"""
	Add a manual Adjustment to a rep's commission (positive adds, negative deducts).

	It is Earned at once, on ``posting_date``, and joins the rep's next statement.

	:param beneficiary_type: "Rep" or "Manager Override".
	:return: ``{"name", "sales_rep", "amount", "posting_date"}``
	"""
	require_commission_enabled()
	_require_manager()
	if beneficiary_type not in ("Rep", "Manager Override"):
		frappe.throw(_("Beneficiary Type must be Rep or Manager Override."))
	amount = flt(amount)
	if not amount:
		frappe.throw(_("Amount cannot be zero."))
	if not (reason or "").strip():
		frappe.throw(_("Reason is required."))

	posting_date = getdate(posting_date) if posting_date else getdate(nowdate())
	currency = frappe.db.get_default("currency")
	if not currency:
		frappe.throw(_("Set a default Currency before adding an adjustment."))

	entry = frappe.get_doc(
		{
			"doctype": "Commission Entry",
			"entry_type": "Adjustment",
			"beneficiary_type": beneficiary_type,
			"sales_rep": sales_rep,
			"status": "Earned",
			"posting_date": posting_date,
			"earned_on": posting_date,
			"currency": currency,
			"amount": amount,
			"commission_plan": frappe.db.get_value("Sales Rep", sales_rep, "commission_plan"),
			"adjustment_reason": reason.strip(),
		}
	)
	entry.insert()
	return {"name": entry.name, "sales_rep": entry.sales_rep, "amount": entry.amount, "posting_date": str(posting_date)}


@frappe.whitelist(methods=["POST"])
def generate_statements(month: str, sales_rep: str | None = None) -> dict:
	"""
	Draft the Commission Statements of ``month`` ("YYYY-MM") now, as the monthly job does.

	A rep is skipped when they already have a statement for the month or have no open entries.

	:param sales_rep: One rep; empty for every commission-active rep the user may see.
	:return: ``{"created": [statement names], "skipped": [{"rep", "reason"}, ...]}``
	"""
	require_commission_enabled()
	_require_manager()
	if not MONTH_PATTERN.match(month or ""):
		frappe.throw(_("Month must look like YYYY-MM."))
	period_start = getdate(f"{month}-01")
	period_end = get_last_day(period_start)

	allowed = None if is_admin() else set(get_allowed_reps())
	if sales_rep:
		if not frappe.db.exists("Sales Rep", sales_rep):
			frappe.throw(_("Sales Rep {0} does not exist.").format(sales_rep))
		if allowed is not None and sales_rep not in allowed:
			frappe.throw(_("You cannot generate statements for {0}.").format(sales_rep), frappe.PermissionError)
		reps = [sales_rep]
	else:
		reps = [
			r for r in frappe.get_all("Sales Rep", filters={"commission_active": 1}, pluck="name", order_by="name")
			if allowed is None or r in allowed
		]

	created, skipped = [], []
	for rep in reps:
		if _statement_exists(rep, period_start):
			skipped.append({"rep": rep, "reason": _("A statement for this month already exists")})
		elif not _open_entries(rep, period_end):
			skipped.append({"rep": rep, "reason": _("No open commission entries")})
		else:
			created.append(build_statement(rep, period_start, period_end).name)
	return {"created": created, "skipped": skipped}


def _plan_dict(plan) -> dict:
	data = {"name": plan.name, "plan_name": plan.plan_name}
	data.update({f: plan.get(f) for f in PLAN_FIELDS})
	data["rates"] = [{f: row.get(f) for f in RATE_FIELDS} for row in plan.rates]
	data["slabs"] = [{f: row.get(f) for f in SLAB_FIELDS} for row in plan.slabs]
	return data


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _can_manage() -> bool:
	user = frappe.session.user
	return user == "Administrator" or bool(MANAGER_ROLES & set(frappe.get_roles(user)))


def _period_range(period: str):
	"""(start, end) dates of a named period; ``None`` bounds mean unbounded."""
	if period not in PERIODS:
		frappe.throw(_("Period must be one of: {0}").format(", ".join(PERIODS)))

	today = getdate(nowdate())
	if period == "this_month":
		return get_first_day(today), get_last_day(today)
	if period == "last_month":
		last = add_months(today, -1)
		return get_first_day(last), get_last_day(last)
	if period == "this_year":
		return today.replace(month=1, day=1), today.replace(month=12, day=31)
	return None, None


def _empty_totals() -> dict:
	return {"earned": 0.0, "pending": 0.0, "reversed": 0.0, "override": 0.0, "net": 0.0}


def _date_filter(start, end):
	return ("between", (start, end)) if start and end else None


def _totals(reps: list[str], start, end) -> dict[str, dict]:
	"""
	Per rep: earned (Earned / Reversed entries by earned date, plus Adjustments by posting date, before
	reversals), pending (Pending, net of reversals), reversed (reversals and negative Adjustments), override
	(the Manager Override part of earned, net of reversals) and net.
	"""
	totals = {rep: _empty_totals() for rep in reps}
	rows = frappe.get_all(
		"Commission Entry",
		filters={"sales_rep": ("in", reps), "status": ("in", ("Pending", "Earned", "Reversed"))},
		fields=[
			"sales_rep",
			"entry_type",
			"beneficiary_type",
			"status",
			"amount",
			"reversed_amount",
			"posting_date",
			"earned_on",
		],
	)
	for row in rows:
		t = totals[row.sales_rep]
		amount, reversed_amount = flt(row.amount), flt(row.reversed_amount)

		if row.status == "Pending":
			if _in_range(row.posting_date, start, end):
				t["pending"] += amount - reversed_amount
		elif row.entry_type == "Adjustment":
			if _in_range(row.posting_date, start, end):
				if amount < 0:
					t["reversed"] += -amount
				else:
					t["earned"] += amount
				if row.beneficiary_type == "Manager Override":
					t["override"] += amount
		elif _in_range(row.earned_on, start, end):
			t["earned"] += amount
			t["reversed"] += reversed_amount
			if row.beneficiary_type == "Manager Override":
				t["override"] += amount - reversed_amount

	for t in totals.values():
		t["net"] = t["earned"] - t["reversed"]
		for key in t:
			t[key] = flt(t[key], 2)
	return totals


def _in_range(value, start, end) -> bool:
	if not start or not end:
		return True
	return bool(value) and start <= getdate(value) <= end


def _top_products(rep: str, start, end) -> list[dict]:
	filters = {
		"sales_rep": rep,
		"entry_type": "Order",
		"status": ("in", ("Pending", "Earned")),
		"product": ("is", "set"),
	}
	window = _date_filter(start, end)
	if window:
		filters["posting_date"] = window

	per_product: dict[str, float] = {}
	for row in frappe.get_all("Commission Entry", filters=filters, fields=["product", "amount", "reversed_amount"]):
		per_product[row.product] = per_product.get(row.product, 0) + flt(row.amount) - flt(row.reversed_amount)

	top = sorted(per_product.items(), key=lambda kv: kv[1], reverse=True)[:5]
	return [
		{"product": p, "title": frappe.db.get_value("Platform Product", p, "product_title"), "commission": flt(c, 2)}
		for p, c in top
	]


def _next_slab_gap(rep: str) -> dict | None:
	"""Sales still needed this month to reach the next slab of a Tiered Slab plan, with that slab's rate."""
	plan_name = frappe.db.get_value("Sales Rep", rep, "commission_plan")
	if not plan_name or frappe.db.get_value("Commission Plan", plan_name, "rule_type") != "Tiered Slab":
		return None

	today = getdate(nowdate())
	rows = frappe.get_all(
		"Commission Entry",
		filters={
			"sales_rep": rep,
			"entry_type": "Order",
			"beneficiary_type": "Rep",
			"status": ("in", ("Pending", "Earned")),
			"posting_date": ("between", (get_first_day(today), get_last_day(today))),
		},
		fields=[{"SUM": "base_amount", "as": "total"}],
	)
	sales = flt(rows[0].total) if rows else 0

	plan = frappe.get_doc("Commission Plan", plan_name)
	for slab in sorted(plan.slabs, key=lambda s: flt(s.from_amount)):
		if flt(slab.from_amount) > sales:
			return {"amount": flt(flt(slab.from_amount) - sales, 2), "rate": flt(slab.rate)}
	return None
