# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Rep Commission Summary: one row per rep for a period.

The period filters entries by their posting date. Columns, per rep:

* Sales: base amount of the rep's own Order entries (cancelled ones excluded).
* Avg Discount %: discount given over those sales, weighted by line value.
* Earned: rep Order entries that became Earned (still Earned, or since Reversed), plus positive Adjustments.
* Pending: rep Order entries still waiting for their earn condition.
* Reversed: refunds on the earned entries, plus negative Adjustments.
* Override: the rep's manager-override entries, net of their own reversals, once earned.
* Net: Earned - Reversed + Override.
* Paid: the part of Net whose entries are on a Commission Statement marked Paid.
* Unpaid: Net - Paid (entries on Draft or Approved statements, or on none yet).
  Pending entries stay out of both, as they are out of Net.

The store is single, so the "store" filter is the Field Order's Store Location (``location_id``).
Non-admins see only their own rep and the reps under it.
"""

from collections import defaultdict, deque

import frappe
from frappe import _
from frappe.utils import flt, getdate

from field_sales_woocommerce.permissions import get_allowed_reps, is_admin


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))

	return get_columns(), get_data(filters)


def get_columns() -> list[dict]:
	money = {"fieldtype": "Currency", "options": "currency", "width": 120}
	return [
		{"fieldname": "sales_rep", "label": _("Rep"), "fieldtype": "Link", "options": "Sales Rep", "width": 180},
		{"fieldname": "period", "label": _("Period"), "fieldtype": "Data", "width": 190},
		{"fieldname": "sales", "label": _("Sales"), **money},
		{"fieldname": "avg_discount", "label": _("Avg Discount %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "earned", "label": _("Earned"), **money},
		{"fieldname": "pending", "label": _("Pending"), **money},
		{"fieldname": "reversed", "label": _("Reversed"), **money},
		{"fieldname": "override", "label": _("Override"), **money},
		{"fieldname": "net", "label": _("Net"), **money},
		{"fieldname": "paid", "label": _("Paid"), **money},
		{"fieldname": "unpaid", "label": _("Unpaid"), **money},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def get_data(filters) -> list[dict]:
	reps = _reps_in_scope(filters)
	if not reps:
		return []

	entry_filters = {
		"sales_rep": ("in", reps),
		"posting_date": ("between", (getdate(filters.from_date), getdate(filters.to_date))),
		"status": ("!=", "Cancelled"),
	}
	if filters.get("location_id"):
		orders = frappe.get_all("Field Order", filters={"location_id": filters.location_id}, pluck="name")
		if not orders:
			return []
		entry_filters["source_order"] = ("in", orders)

	entries = frappe.get_all(
		"Commission Entry",
		filters=entry_filters,
		fields=[
			"sales_rep",
			"entry_type",
			"beneficiary_type",
			"status",
			"earned_on",
			"currency",
			"base_amount",
			"discount_pct",
			"amount",
			"reversed_amount",
			"commission_statement",
		],
	)
	paid_statements = _paid_statements(entries)

	# Keyed by (rep, currency): amounts of different currencies are never added together.
	totals = defaultdict(lambda: defaultdict(float))
	for e in entries:
		t = totals[(e.sales_rep, e.currency)]
		amount, reversed_amount = flt(e.amount), flt(e.reversed_amount)
		# This entry's share of Net, for the Paid column.
		net = 0

		if e.beneficiary_type == "Manager Override":
			# Only earned money counts as override; pending stays out of the net.
			if e.entry_type == "Adjustment":
				net = amount
			elif e.earned_on:
				net = amount - reversed_amount
			t["override"] += net
		elif e.entry_type == "Adjustment":
			t["earned" if amount > 0 else "reversed"] += amount if amount > 0 else -amount
			net = amount
		else:
			base = flt(e.base_amount)
			t["sales"] += base
			t["gross"] += base / (1 - flt(e.discount_pct) / 100) if flt(e.discount_pct) < 100 else 0
			if e.status == "Pending":
				t["pending"] += amount
			elif e.earned_on:
				t["earned"] += amount
				t["reversed"] += reversed_amount
				net = amount - reversed_amount

		if e.commission_statement in paid_statements:
			t["paid"] += net

	period = f"{getdate(filters.from_date)} to {getdate(filters.to_date)}"
	data = []
	for (rep, currency), t in sorted(totals.items()):
		net = t["earned"] - t["reversed"] + t["override"]
		data.append(
			{
				"sales_rep": rep,
				"period": period,
				"sales": t["sales"],
				"avg_discount": (t["gross"] - t["sales"]) / t["gross"] * 100 if t["gross"] else 0,
				"earned": t["earned"],
				"pending": t["pending"],
				"reversed": t["reversed"],
				"override": t["override"],
				"net": net,
				"paid": t["paid"],
				"unpaid": net - t["paid"],
				"currency": currency,
			}
		)
	return data


def _paid_statements(entries) -> set[str]:
	"""Names of the Paid statements among the entries' statements (one query)."""
	names = {e.commission_statement for e in entries if e.commission_statement}
	if not names:
		return set()
	return set(
		frappe.get_all("Commission Statement", filters={"name": ("in", list(names)), "status": "Paid"}, pluck="name")
	)


def _reps_in_scope(filters) -> list[str]:
	"""Reps the user may see, narrowed by the Sales Rep and Manager filters."""
	allowed = None if is_admin() else set(get_allowed_reps())
	if allowed is not None and not allowed:
		return []

	if filters.get("sales_rep"):
		wanted = {filters.sales_rep}
		allowed = wanted if allowed is None else allowed & wanted
	if filters.get("manager"):
		under = _reps_under(filters.manager)
		allowed = under if allowed is None else allowed & under
		if not allowed:
			return []

	if allowed is None:
		return frappe.get_all("Sales Rep", pluck="name")
	return list(allowed)


def _reps_under(manager: str) -> set[str]:
	"""Every rep below ``manager`` through reports_to, at all levels (the manager is not included)."""
	reports = defaultdict(list)
	for rep in frappe.get_all("Sales Rep", fields=["name", "reports_to"], filters={"reports_to": ("is", "set")}):
		reports[rep.reports_to].append(rep.name)

	seen = {manager}
	queue = deque([manager])
	while queue:
		for child in reports[queue.popleft()]:
			if child not in seen:
				seen.add(child)
				queue.append(child)
	return seen - {manager}
