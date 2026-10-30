# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""The desktop app's Dashboard page (frontend/src/pages/Dashboard.vue).

Everything goes through frappe.get_list, so the Sales Rep hierarchy
(permission_query_conditions on reports_to) applies: a rep sees their own
visits and orders, a manager sees their team's."""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, get_first_day, get_first_day_of_week, getdate, nowdate

from field_sales_woocommerce.frontend_api import get_attention_presets
from field_sales_woocommerce.www.fieldsales import has_app_access

PERIODS = ("today", "week", "month")
MANAGER_ROLES = {"System Manager", "Sales Manager"}
TOP_REPS = 10
RECENT_ORDERS = 10


@frappe.whitelist()
def get_dashboard(period: str = "today") -> dict:
	"""
	Cards, charts and lists for ``period`` ("today", "week" or "month", up to today).

	Pending and failed sync counts are the current backlog, not limited to the period.

	:return: ``{"period", "from_date", "to_date", "cards", "orders_by_rep", "orders_by_day",
	        "recent_orders", "active_visits"}``
	"""
	if frappe.session.user == "Guest" or not has_app_access():
		frappe.throw(_("You do not have access to Field Sales"), frappe.PermissionError)
	if period not in PERIODS:
		frappe.throw(_("Period must be one of: {0}").format(", ".join(PERIODS)))

	to_date = getdate(nowdate())
	from_date = {
		"today": to_date,
		"week": get_first_day_of_week(to_date),
		"month": get_first_day(to_date),
	}[period]
	in_period = ("between", [from_date, to_date])
	submitted = {"docstatus": 1, "order_date": in_period}

	totals = frappe.get_list(
		"Field Order",
		filters=submitted,
		fields=[{"COUNT": "*", "as": "orders"}, {"SUM": "grand_total", "as": "value"}],
	)[0]
	presets = get_attention_presets()

	return {
		"period": period,
		"from_date": str(from_date),
		"to_date": str(to_date),
		"cards": {
			# Visits that happened: checked in (in progress or completed), not merely planned.
			"visits_count": _count("Visit", {"visit_date": in_period, "status": ("in", ["In Progress", "Completed"])}),
			"orders_count": totals.orders or 0,
			"order_value": flt(totals.value, 2),
			"pending_sync_count": _count("Field Order", {"docstatus": 1, "sync_status": "Pending"}),
			# Same records as the Sync Failures page: failed orders and failed retailers.
			"failed_sync_count": sum(
				_count(presets[key]["doctype"], presets[key]["filters"]) for key in ("failed_orders", "failed_retailers")
			),
		},
		"orders_by_rep": _orders_by_rep(submitted),
		"orders_by_day": _orders_by_day(submitted, from_date, to_date),
		"recent_orders": frappe.get_list(
			"Field Order",
			fields=["name", "retailer_name", "grand_total", "sync_status", "docstatus", "modified"],
			order_by="modified desc",
			limit_page_length=RECENT_ORDERS,
		),
		"active_visits": frappe.get_list(
			"Visit",
			filters={"check_in_time": ("is", "set"), "check_out_time": ("is", "not set"), "status": ("!=", "Cancelled")},
			fields=["name", "sales_rep", "sales_rep_name as rep_name", "retailer_name", "check_in_time"],
			order_by="check_in_time asc",
			limit_page_length=50,
		),
	}


def _orders_by_rep(filters: dict) -> list[dict]:
	"""Top reps by order value; managers only (the page hides the chart for reps)."""
	if not (MANAGER_ROLES & set(frappe.get_roles())):
		return []

	rows = frappe.get_list(
		"Field Order",
		filters=filters,
		fields=["sales_rep", {"COUNT": "*", "as": "orders"}, {"SUM": "grand_total", "as": "value"}],
		group_by="sales_rep",
		order_by="value desc",
		limit_page_length=TOP_REPS,
	)
	reps = [r.sales_rep for r in rows if r.sales_rep]
	names = (
		dict(frappe.get_list("Sales Rep", filters={"name": ("in", reps)}, fields=["name", "full_name"], as_list=True))
		if reps
		else {}
	)
	return [
		{
			"rep": r.sales_rep,
			"rep_name": names.get(r.sales_rep) or r.sales_rep or _("No rep"),
			"orders": r.orders,
			"value": flt(r.value, 2),
		}
		for r in rows
	]


def _orders_by_day(filters: dict, from_date, to_date) -> list[dict]:
	"""One row per day of the period, zero-filled, so the chart has no gaps."""
	rows = {
		str(r.order_date): r
		for r in frappe.get_list(
			"Field Order",
			filters=filters,
			fields=["order_date", {"COUNT": "*", "as": "orders"}, {"SUM": "grand_total", "as": "value"}],
			group_by="order_date",
		)
	}
	days = [str(add_days(from_date, i)) for i in range(date_diff(to_date, from_date) + 1)]
	return [
		{"date": day, "orders": rows[day].orders if day in rows else 0, "value": flt(rows[day].value, 2) if day in rows else 0.0}
		for day in days
	]


def _count(doctype: str, filters: dict | list) -> int:
	rows = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "*", "as": "count"}])
	return rows[0].count if rows else 0
