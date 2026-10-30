# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Rep Performance: visits, geo-compliance, orders, retailer coverage, products,
sync health and target achievement, grouped by rep / territory / city /
retailer type / date bucket, with a comparison period.

How it stays efficient: every metric is read with a handful of grouped
queries keyed on (sales_rep, dimension), where the dimension is the retailer
attribute or date bucket being grouped on (none for Sales Rep / Territory).
Python then rolls those keyed rows up into the report's groups, and into
manager rows for the tree view, by adding additive parts (counts, sums) and
taking unions of sets (retailers, products), so totals are exact, never
averages of averages. Queries are MariaDB-specific (DATE_FORMAT, SUBDATE).
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import Count, IfNull, Max, Min, Sum
from pypika.terms import PseudoColumn
from frappe.utils import (
	add_days,
	add_months,
	cint,
	date_diff,
	flt,
	formatdate,
	get_first_day,
	get_last_day,
	getdate,
	nowdate,
)

from field_sales_woocommerce.permissions import get_allowed_reps, is_admin

DATE_GROUPS = ("Day", "Week", "Month")
NOT_SET = "(Not Set)"
NOT_VISITED_DAYS = 30

DATE_ADD = CustomFunction("DATE_ADD", ["date", "interval"])
SUBDATE = CustomFunction("SUBDATE", ["date", "days"])
WEEKDAY = CustomFunction("WEEKDAY", ["date"])
DATE_FORMAT = CustomFunction("DATE_FORMAT", ["date", "format"])

# Parts that roll up by addition; everything shown is derived from these.
ADDITIVE = (
	"planned", "completed", "missed", "checked_in", "inside", "dist_sum", "dist_cnt",
	"time_sum", "time_cnt", "orders", "value", "units", "failed", "not_visited",
	"net", "discount", "discounted", "cmp_discount",
	"monthly_target", "target", "cmp_planned", "cmp_completed", "cmp_orders", "cmp_value",
)


def execute(filters=None):
	ctx = _build_context(filters)
	comps = _collect(ctx)
	rows = _build_rows(ctx, comps)
	return (
		get_columns(ctx),
		rows,
		None,
		_get_chart(ctx, rows),
		_get_summary(ctx, comps),
	)


# ----------------------------------------------------------------------
# Filters, scope, periods
# ----------------------------------------------------------------------


def _build_context(filters) -> frappe._dict:
	filters = frappe._dict(filters or {})
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))

	from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	group_by = filters.group_by or "Sales Rep"
	compare = filters.compare_with if filters.compare_with not in (None, "", "None") else None
	reps = get_reps_in_scope(filters)

	ctx = frappe._dict(
		from_date=from_date,
		to_date=to_date,
		today=getdate(nowdate()),
		group_by=group_by,
		compare=compare,
		city=filters.city,
		retailer_type=filters.retailer_type,
		only_below_target=cint(filters.show_only_below_target),
		reps=reps,
		# Unnarrowed admin scope: drill-downs can skip a huge "sales_rep in (...)" filter.
		all_reps=is_admin() and not (filters.sales_rep or filters.territory),
	)
	if compare:
		ctx.cmp_from, ctx.cmp_to, ctx.cmp_shift = _compare_range(from_date, to_date, compare, group_by)
	return ctx


def get_reps_in_scope(filters, user: str | None = None) -> dict:
	"""
	Sales Reps the user may see (own + everyone under them; all for admins),
	narrowed by the Sales Rep (one or many) and Territory filters.

	:return: ``{rep: {"name", "full_name", "territory", "reports_to", "monthly_target", "active"}}``
	"""
	filters = frappe._dict(filters or {})
	user = user or frappe.session.user

	allowed = None if is_admin(user) else set(get_allowed_reps(user))
	if allowed is not None and not allowed:
		return {}

	selected = filters.get("sales_rep")
	if isinstance(selected, str):
		selected = frappe.parse_json(selected) if selected.startswith("[") else [selected]
	if selected:
		allowed = set(selected) if allowed is None else allowed & set(selected)
		if not allowed:
			return {}

	rep_filters = {}
	if allowed is not None:
		rep_filters["name"] = ("in", list(allowed))
	if filters.get("territory"):
		rep_filters["territory"] = filters.territory

	return {
		r.name: r
		for r in frappe.get_all(
			"Sales Rep",
			filters=rep_filters,
			fields=["name", "full_name", "territory", "reports_to", "monthly_target", "active"],
		)
	}


def _compare_range(from_date, to_date, compare: str, group_by: str):
	"""
	The comparison period, and how to shift its dates onto the current one
	(so its date buckets line up with the current period's buckets).

	"Previous Period" over whole calendar months compares with the same number
	of whole months before; otherwise with the same number of days before.
	"""
	if compare == "Same Period Last Year":
		return add_months(from_date, -12), add_months(to_date, -12), (1, "YEAR")

	whole_months = from_date == get_first_day(from_date) and to_date == get_last_day(to_date)
	if whole_months:
		months = (to_date.year - from_date.year) * 12 + to_date.month - from_date.month + 1
		return add_months(from_date, -months), add_days(from_date, -1), (months, "MONTH")

	days = date_diff(to_date, from_date) + 1
	return add_days(from_date, -days), add_days(from_date, -1), (days, "DAY")


def prorated_target(monthly_target, start, end) -> float:
	"""Monthly target spread over [start, end]: a full month gets the full target."""
	monthly_target = flt(monthly_target)
	if not monthly_target or start > end:
		return 0.0

	total, day = 0.0, getdate(start)
	end = getdate(end)
	while day <= end:
		month_end = get_last_day(day)
		segment_end = min(end, month_end)
		total += monthly_target * (date_diff(segment_end, day) + 1) / month_end.day
		day = add_days(segment_end, 1)
	return total


# ----------------------------------------------------------------------
# Grouping
# ----------------------------------------------------------------------


def _dimension(ctx, date_col, retailer, shift=None):
	"""
	SQL term for the part of the group that is not derived from the rep:
	a retailer attribute or a date bucket. ``None`` for Sales Rep / Territory.

	``shift`` = (n, "DAY" | "MONTH" | "YEAR") moves comparison-period dates
	onto the current period before bucketing.
	"""
	if ctx.group_by == "City":
		return IfNull(retailer.city, "")
	if ctx.group_by == "Retailer Type":
		return IfNull(retailer.retailer_type, "")
	if ctx.group_by not in DATE_GROUPS:
		return None

	day = date_col
	if shift:
		day = DATE_ADD(day, PseudoColumn(f"INTERVAL {cint(shift[0])} {shift[1]}"))
	if ctx.group_by == "Week":
		return SUBDATE(day, WEEKDAY(day))  # Monday of the week
	if ctx.group_by == "Month":
		return DATE_FORMAT(day, "%Y-%m-01")
	return day


def _group_key(ctx, rep: str, dim) -> str:
	if ctx.group_by == "Sales Rep":
		return rep
	if ctx.group_by == "Territory":
		return (ctx.reps[rep].territory if rep in ctx.reps else None) or NOT_SET
	if ctx.group_by in DATE_GROUPS:
		return str(getdate(dim))
	return dim or NOT_SET


def _date_buckets(ctx) -> list[tuple[str, object, object]]:
	"""Every bucket in the period as ``(key, start, end)``, clipped to the period."""
	buckets, day = [], ctx.from_date
	while day <= ctx.to_date:
		if ctx.group_by == "Day":
			start, end = day, day
		elif ctx.group_by == "Week":
			start = add_days(day, -day.weekday())
			end = add_days(start, 6)
		else:
			start, end = get_first_day(day), get_last_day(day)
		buckets.append((str(start), max(start, ctx.from_date), min(end, ctx.to_date)))
		day = add_days(end, 1)
	return buckets


def _new_comp() -> dict:
	comp = dict.fromkeys(ADDITIVE, 0.0)
	comp.update(visited=set(), new=set(), products={}, reps=set(), has_target=False)
	return comp


def _merge(into: dict, other: dict) -> None:
	for key in ADDITIVE:
		into[key] += other[key]
	into["visited"] |= other["visited"]
	into["new"] |= other["new"]
	into["reps"] |= other["reps"]
	into["has_target"] = into["has_target"] or other["has_target"]
	for product, (title, value) in other["products"].items():
		into["products"][product] = (title, into["products"].get(product, (title, 0.0))[1] + value)


# ----------------------------------------------------------------------
# Queries
# ----------------------------------------------------------------------


def _collect(ctx) -> dict:
	"""``{group_key: comp}`` for the current period (and comparison values)."""
	comps = defaultdict(_new_comp)
	if not ctx.reps:
		return comps

	reps = list(ctx.reps)

	for row in _visit_rows(ctx, ctx.from_date, ctx.to_date, reps):
		comp = comps[_group_key(ctx, row.sales_rep, row.dim)]
		comp["reps"].add(row.sales_rep)
		for key in ("planned", "completed", "missed", "checked_in", "inside", "dist_sum", "dist_cnt", "time_sum", "time_cnt"):
			comp[key] += flt(row[key])

	for row in _order_rows(ctx, ctx.from_date, ctx.to_date, reps):
		comp = comps[_group_key(ctx, row.sales_rep, row.dim)]
		comp["reps"].add(row.sales_rep)
		for key in ("orders", "value", "units", "failed", "net", "discount", "discounted"):
			comp[key] += flt(row[key])

	for row in _visited_retailer_rows(ctx, reps):
		comps[_group_key(ctx, row.sales_rep, row.dim)]["visited"].add(row.retailer)

	for row in _new_retailer_rows(ctx, reps):
		comps[_group_key(ctx, row.sales_rep, row.dim)]["new"].add(row.retailer)

	for row in _product_rows(ctx, reps):
		products = comps[_group_key(ctx, row.sales_rep, row.dim)]["products"]
		title, value = products.get(row.product, (row.product_title, 0.0))
		products[row.product] = (title or row.product_title, value + flt(row.value))

	if ctx.group_by not in DATE_GROUPS:
		for row in _not_visited_rows(ctx, reps):
			comps[_group_key(ctx, row.sales_rep, row.dim)]["not_visited"] += flt(row.count)

	if ctx.compare:
		for row in _visit_rows(ctx, ctx.cmp_from, ctx.cmp_to, reps, shift=ctx.cmp_shift):
			comp = comps[_group_key(ctx, row.sales_rep, row.dim)]
			comp["cmp_planned"] += flt(row.planned)
			comp["cmp_completed"] += flt(row.completed)
		for row in _order_rows(ctx, ctx.cmp_from, ctx.cmp_to, reps, shift=ctx.cmp_shift):
			comp = comps[_group_key(ctx, row.sales_rep, row.dim)]
			comp["cmp_orders"] += flt(row.orders)
			comp["cmp_value"] += flt(row.value)
			comp["cmp_discount"] += flt(row.discount)

	_apply_targets(ctx, comps)
	return comps


def _retailer_filters(ctx, query, retailer):
	if ctx.city:
		query = query.where(retailer.city == ctx.city)
	if ctx.retailer_type:
		query = query.where(retailer.retailer_type == ctx.retailer_type)
	return query


def _with_dim(query, dim):
	return query.select(dim.as_("dim")).groupby(dim) if dim is not None else query


def _visit_rows(ctx, from_date, to_date, reps, shift=None):
	visit = frappe.qb.DocType("Visit")
	retailer = frappe.qb.DocType("Retailer")
	case = frappe.qb.terms.Case

	completed = visit.status == "Completed"
	checked_in = visit.check_in_time.isnotnull()
	# Planned (or started) and its day has passed without being completed.
	missed = visit.status.isin(["Planned", "In Progress"]) & (visit.visit_date < ctx.today)
	has_distance = checked_in & visit.distance_from_retailer_m.isnotnull()

	query = (
		frappe.qb.from_(visit)
		.join(retailer)
		.on(retailer.name == visit.retailer)
		.select(
			visit.sales_rep,
			Count("*").as_("planned"),
			Sum(case().when(completed, 1).else_(0)).as_("completed"),
			Sum(case().when(missed, 1).else_(0)).as_("missed"),
			Sum(case().when(checked_in, 1).else_(0)).as_("checked_in"),
			Sum(case().when(checked_in & (visit.within_geofence == 1), 1).else_(0)).as_("inside"),
			Sum(case().when(has_distance, visit.distance_from_retailer_m).else_(0)).as_("dist_sum"),
			Sum(case().when(has_distance, 1).else_(0)).as_("dist_cnt"),
			Sum(case().when(completed, visit.time_on_site_min).else_(0)).as_("time_sum"),
			Sum(case().when(completed, 1).else_(0)).as_("time_cnt"),
		)
		.where(visit.visit_date.between(from_date, to_date))
		.where(visit.status != "Cancelled")
		.where(visit.sales_rep.isin(reps))
		.groupby(visit.sales_rep)
	)
	query = _with_dim(query, _dimension(ctx, visit.visit_date, retailer, shift))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _order_rows(ctx, from_date, to_date, reps, shift=None):
	order = frappe.qb.DocType("Field Order")
	retailer = frappe.qb.DocType("Retailer")
	failed = frappe.qb.terms.Case().when(order.sync_status == "Failed", 1).else_(0)
	discounted = frappe.qb.terms.Case().when(order.discount_amount > 0, 1).else_(0)

	query = (
		frappe.qb.from_(order)
		.join(retailer)
		.on(retailer.name == order.retailer)
		.select(
			order.sales_rep,
			Count("*").as_("orders"),
			Sum(IfNull(order.grand_total, 0)).as_("value"),
			Sum(IfNull(order.total_qty, 0)).as_("units"),
			Sum(failed).as_("failed"),
			Sum(IfNull(order.net_total, 0)).as_("net"),
			Sum(IfNull(order.discount_amount, 0)).as_("discount"),
			Sum(discounted).as_("discounted"),
		)
		.where(order.docstatus == 1)
		.where(order.order_date.between(from_date, to_date))
		.where(order.sales_rep.isin(reps))
		.groupby(order.sales_rep)
	)
	query = _with_dim(query, _dimension(ctx, order.order_date, retailer, shift))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _visited_retailer_rows(ctx, reps):
	"""Distinct (rep, dim, retailer) for Active retailers with a completed visit in the period."""
	visit = frappe.qb.DocType("Visit")
	retailer = frappe.qb.DocType("Retailer")

	query = (
		frappe.qb.from_(visit)
		.join(retailer)
		.on(retailer.name == visit.retailer)
		.select(visit.sales_rep, visit.retailer)
		.distinct()
		.where(visit.status == "Completed")
		.where(retailer.status == "Active")
		.where(visit.visit_date.between(ctx.from_date, ctx.to_date))
		.where(visit.sales_rep.isin(reps))
	)
	dim = _dimension(ctx, visit.visit_date, retailer)
	if dim is not None:
		query = query.select(dim.as_("dim"))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _new_retailer_rows(ctx, reps):
	"""Retailers whose first-ever completed visit falls in the period, attributed to that visit."""
	visit = frappe.qb.DocType("Visit")
	retailer = frappe.qb.DocType("Retailer")
	any_visit = frappe.qb.DocType("Visit").as_("any_visit")

	first = (
		frappe.qb.from_(any_visit)
		.select(any_visit.retailer, Min(any_visit.visit_date).as_("first_date"))
		.where(any_visit.status == "Completed")
		.groupby(any_visit.retailer)
	).as_("first_visit")

	query = (
		frappe.qb.from_(visit)
		.join(first)
		.on((first.retailer == visit.retailer) & (first.first_date == visit.visit_date))
		.join(retailer)
		.on(retailer.name == visit.retailer)
		.select(visit.sales_rep, visit.retailer)
		.distinct()
		.where(visit.status == "Completed")
		.where(visit.visit_date.between(ctx.from_date, ctx.to_date))
		.where(visit.sales_rep.isin(reps))
	)
	dim = _dimension(ctx, visit.visit_date, retailer)
	if dim is not None:
		query = query.select(dim.as_("dim"))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _product_rows(ctx, reps):
	"""Order value per (rep, dim, product): gives top product and unique SKUs at any level."""
	item = frappe.qb.DocType("Field Order Item")
	order = frappe.qb.DocType("Field Order")
	retailer = frappe.qb.DocType("Retailer")

	query = (
		frappe.qb.from_(item)
		.join(order)
		.on((order.name == item.parent) & (item.parenttype == "Field Order"))
		.join(retailer)
		.on(retailer.name == order.retailer)
		.select(
			order.sales_rep,
			item.product,
			Max(item.product_title).as_("product_title"),
			Sum(IfNull(item.amount, 0)).as_("value"),
		)
		.where(order.docstatus == 1)
		.where(order.order_date.between(ctx.from_date, ctx.to_date))
		.where(order.sales_rep.isin(reps))
		.where(item.product.isnotnull())
		.groupby(order.sales_rep, item.product)
	)
	query = _with_dim(query, _dimension(ctx, order.order_date, retailer))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _not_visited_rows(ctx, reps):
	"""
	Active retailers (by assigned rep) with no completed visit in the 30 days
	up to the period end (or today, if the period ends later).
	"""
	visit = frappe.qb.DocType("Visit")
	retailer = frappe.qb.DocType("Retailer")
	as_of = min(ctx.to_date, ctx.today)

	last = (
		frappe.qb.from_(visit)
		.select(visit.retailer, Max(visit.visit_date).as_("last_date"))
		.where(visit.status == "Completed")
		.where(visit.visit_date <= as_of)
		.groupby(visit.retailer)
	).as_("last_visit")

	query = (
		frappe.qb.from_(retailer)
		.left_join(last)
		.on(last.retailer == retailer.name)
		.select(retailer.sales_rep, Count("*").as_("count"))
		.where(retailer.status == "Active")
		.where(retailer.sales_rep.isin(reps))
		.where(retailer.creation <= f"{as_of} 23:59:59")
		.where(last.last_date.isnull() | (last.last_date < add_days(as_of, -NOT_VISITED_DAYS)))
		.groupby(retailer.sales_rep)
	)
	query = _with_dim(query, _dimension(ctx, None, retailer))
	return _retailer_filters(ctx, query, retailer).run(as_dict=True)


def _apply_targets(ctx, comps) -> None:
	"""Prorated targets where a group maps to reps: Sales Rep, Territory, date buckets."""
	if ctx.group_by in ("City", "Retailer Type"):
		return

	if ctx.group_by in DATE_GROUPS:
		for key, start, end in _date_buckets(ctx):
			comp = comps[key]
			for rep in ctx.reps.values():
				comp["target"] += prorated_target(rep.monthly_target, start, end)
			comp["monthly_target"] = sum(flt(r.monthly_target) for r in ctx.reps.values())
			comp["has_target"] = bool(comp["monthly_target"])
		return

	for rep in ctx.reps.values():
		comp = comps[_group_key(ctx, rep.name, None)]
		comp["reps"].add(rep.name)
		comp["monthly_target"] += flt(rep.monthly_target)
		comp["target"] += prorated_target(rep.monthly_target, ctx.from_date, ctx.to_date)
		comp["has_target"] = comp["has_target"] or bool(flt(rep.monthly_target))


# ----------------------------------------------------------------------
# Rows
# ----------------------------------------------------------------------


def get_columns(ctx=None):
	group_label = _(ctx.group_by) if ctx else _("Sales Rep")
	return [
		{"label": group_label, "fieldname": "group", "fieldtype": "Data", "width": 220},
		{"label": _("Planned Visits"), "fieldname": "planned_visits", "fieldtype": "Int", "width": 110},
		{"label": _("Completed Visits"), "fieldname": "completed_visits", "fieldtype": "Int", "width": 125},
		{"label": _("Missed Visits"), "fieldname": "missed_visits", "fieldtype": "Int", "width": 105},
		{"label": _("Completion %"), "fieldname": "completion_pct", "fieldtype": "Percent", "width": 115},
		{"label": _("Inside Geofence %"), "fieldname": "inside_geofence_pct", "fieldtype": "Percent", "width": 135},
		{"label": _("Avg Distance (m)"), "fieldname": "avg_distance_m", "fieldtype": "Float", "precision": 0, "width": 125},
		{"label": _("Avg Time on Site (min)"), "fieldname": "avg_time_on_site", "fieldtype": "Float", "precision": 1, "width": 160},
		{"label": _("Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 85},
		{"label": _("Order Value"), "fieldname": "order_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Avg Order Value"), "fieldname": "avg_order_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Discount Given"), "fieldname": "discount_given", "fieldtype": "Currency", "width": 125},
		{"label": _("Avg Discount %"), "fieldname": "avg_discount_pct", "fieldtype": "Percent", "width": 120},
		{"label": _("Orders with Discount"), "fieldname": "discounted_orders", "fieldtype": "Int", "width": 150},
		{"label": _("Units Sold"), "fieldname": "units_sold", "fieldtype": "Float", "precision": 0, "width": 100},
		{"label": _("Conversion %"), "fieldname": "conversion_pct", "fieldtype": "Percent", "width": 115},
		{"label": _("Active Retailers Visited"), "fieldname": "retailers_visited", "fieldtype": "Int", "width": 170},
		{"label": _("New Retailers"), "fieldname": "new_retailers", "fieldtype": "Int", "width": 115},
		{"label": _("Not Visited in 30 Days"), "fieldname": "retailers_not_visited", "fieldtype": "Int", "width": 165},
		{"label": _("Top Product"), "fieldname": "top_product", "fieldtype": "Data", "width": 180},
		{"label": _("Unique SKUs Sold"), "fieldname": "unique_skus", "fieldtype": "Int", "width": 130},
		{"label": _("Failed Syncs"), "fieldname": "failed_syncs", "fieldtype": "Int", "width": 105},
		{"label": _("Monthly Target"), "fieldname": "monthly_target", "fieldtype": "Currency", "width": 130},
		{"label": _("Period Target"), "fieldname": "period_target", "fieldtype": "Currency", "width": 130},
		{"label": _("Achievement %"), "fieldname": "achievement_pct", "fieldtype": "Percent", "width": 125},
		{"label": _("Gap"), "fieldname": "gap", "fieldtype": "Currency", "width": 120},
		{"label": _("Order Value Δ %"), "fieldname": "value_delta_pct", "fieldtype": "Percent", "width": 130},
		{"label": _("Rank"), "fieldname": "rank", "fieldtype": "Int", "width": 70},
	]


def _pct(part, whole):
	return flt(part * 100.0 / whole, 2) if whole else None


def _metrics(ctx, comp: dict) -> dict:
	top = max(comp["products"].items(), key=lambda p: p[1][1], default=None)
	value, target = flt(comp["value"], 2), flt(comp["target"], 2)
	return {
		"planned_visits": int(comp["planned"]),
		"completed_visits": int(comp["completed"]),
		"missed_visits": int(comp["missed"]),
		"completion_pct": _pct(comp["completed"], comp["planned"]),
		"inside_geofence_pct": _pct(comp["inside"], comp["checked_in"]),
		"avg_distance_m": flt(comp["dist_sum"] / comp["dist_cnt"], 0) if comp["dist_cnt"] else None,
		"avg_time_on_site": flt(comp["time_sum"] / comp["time_cnt"], 1) if comp["time_cnt"] else None,
		"orders": int(comp["orders"]),
		"order_value": value,
		"avg_order_value": flt(value / comp["orders"], 2) if comp["orders"] else None,
		"discount_given": flt(comp["discount"], 2),
		# Discount against what it was taken off (net, before tax), across all orders.
		"avg_discount_pct": _pct(comp["discount"], comp["net"]),
		"discounted_orders": int(comp["discounted"]),
		"units_sold": flt(comp["units"]),
		# Orders can be taken without a visit, so this can exceed 100%.
		"conversion_pct": _pct(comp["orders"], comp["completed"]),
		"retailers_visited": len(comp["visited"]),
		"new_retailers": len(comp["new"]),
		"retailers_not_visited": None if ctx.group_by in DATE_GROUPS else int(comp["not_visited"]),
		"top_product": top[1][0] if top else None,
		"unique_skus": len(comp["products"]),
		"failed_syncs": int(comp["failed"]),
		"monthly_target": flt(comp["monthly_target"], 2) if comp["has_target"] else None,
		"period_target": target if comp["has_target"] else None,
		"achievement_pct": _pct(value, target) if comp["has_target"] else None,
		"gap": flt(target - value, 2) if comp["has_target"] else None,
		"value_delta_pct": _pct(value - comp["cmp_value"], comp["cmp_value"]) if ctx.compare else None,
		# Not a column: feeds the comparison line on the chart.
		"cmp_order_value": flt(comp["cmp_value"], 2) if ctx.compare else None,
	}


def _build_rows(ctx, comps) -> list[dict]:
	if ctx.group_by == "Sales Rep":
		rows = _tree_rows(ctx, comps)
	elif ctx.group_by in DATE_GROUPS:
		rows = [_row(ctx, key, _bucket_label(ctx, key), comps[key]) for key, *_ in _date_buckets(ctx)]
	else:
		rows = [_row(ctx, key, key, comp) for key, comp in comps.items()]
		rows.sort(key=lambda r: (-r["order_value"], r["group"]))

	_rank(rows)
	if ctx.only_below_target:
		rows = _below_target(rows)
	return rows


def _row(ctx, key: str, label: str, comp: dict, indent=None, parent=None) -> dict:
	row = {"group_key": key, "group": label, **_metrics(ctx, comp), "drill": _drill(ctx, key, comp)}
	if indent is not None:
		row.update(indent=indent, parent_key=parent)
	return row


def _tree_rows(ctx, comps) -> list[dict]:
	"""
	Managers as parent rows over the reps reporting to them (Sales Rep.reports_to),
	at any depth. A manager's parent row totals their team; their own figures
	are a child row, so parent = sum of children holds exactly.
	"""
	children = defaultdict(list)
	for rep in ctx.reps.values():
		if rep.reports_to in ctx.reps and rep.reports_to != rep.name:
			children[rep.reports_to].append(rep.name)
	roots = [r for r in ctx.reps.values() if r.reports_to not in ctx.reps or r.reports_to == r.name]

	def own(rep):
		return comps[rep] if rep in comps else _new_comp()

	def team_comp(rep, seen):
		total = _new_comp()
		_merge(total, own(rep))
		for child in children[rep]:
			if child not in seen:
				seen.add(child)
				_merge(total, team_comp(child, seen))
		return total

	def visible(rep) -> bool:
		comp = own(rep)
		return ctx.reps[rep].active or comp["planned"] or comp["orders"]

	rows = []

	def emit(rep, indent, parent, seen):
		name = ctx.reps[rep].full_name or rep
		kids = [c for c in children[rep] if c not in seen]
		if not kids:
			if visible(rep):
				rows.append(_row(ctx, rep, name, own(rep), indent, parent) | {"sales_rep": rep})
			return

		team_key = f"team::{rep}"
		rows.append(
			_row(ctx, team_key, _("{0} (Team)").format(name), team_comp(rep, set(seen) | {rep}), indent, parent)
			| {"is_team": 1}
		)
		rows.append(_row(ctx, rep, name, own(rep), indent + 1, team_key) | {"sales_rep": rep})
		seen = seen | {rep, *kids}
		for child in sorted(kids, key=lambda c: -own(c)["value"]):
			emit(child, indent + 1, team_key, seen)

	for root in sorted(roots, key=lambda r: -own(r.name)["value"]):
		emit(root.name, 0, None, {root.name})

	# Reps in a reporting loop (A -> B -> A) have no root above them: show them at the top.
	shown = {r["group_key"] for r in rows} | {r["group_key"][len("team::"):] for r in rows if r.get("is_team")}
	for rep in sorted(set(ctx.reps) - shown):
		if rep not in shown:
			emit(rep, 0, None, {rep})
			shown |= {r["group_key"] for r in rows} | {r["group_key"][len("team::"):] for r in rows if r.get("is_team")}
	return rows


def _rank(rows: list[dict]) -> None:
	"""Rank by Order Value among leaf rows (team rows are totals, not ranked)."""
	leaves = [r for r in rows if not r.get("is_team")]
	for rank, row in enumerate(sorted(leaves, key=lambda r: -r["order_value"]), start=1):
		row["rank"] = rank


def _below_target(rows: list[dict]) -> list[dict]:
	"""Leaf rows under 100% of target; team rows kept (with full-team totals) if any member is."""
	keep = {
		r["group_key"]
		for r in rows
		if not r.get("is_team") and r["achievement_pct"] is not None and r["achievement_pct"] < 100
	}
	parents = {r["group_key"]: r.get("parent_key") for r in rows}
	for key in list(keep):
		parent = parents.get(key)
		while parent:
			keep.add(parent)
			parent = parents.get(parent)
	return [r for r in rows if r["group_key"] in keep]


def _bucket_label(ctx, key: str) -> str:
	day = getdate(key)
	if ctx.group_by == "Month":
		return day.strftime("%b %Y")
	if ctx.group_by == "Week":
		return _("Week of {0}").format(formatdate(day))
	return formatdate(day)


def _drill(ctx, key: str, comp: dict) -> dict:
	"""List-view filters for the Visits / Orders behind a row (``None`` where a list can't express it)."""
	start, end = ctx.from_date, ctx.to_date
	reps = None if ctx.all_reps else list(ctx.reps)
	extra = {}

	if ctx.group_by in ("Sales Rep", "Territory"):
		reps = sorted(comp["reps"]) or [key]
	elif ctx.group_by in DATE_GROUPS:
		bucket = next((b for b in _date_buckets(ctx) if b[0] == key), None)
		if bucket:
			start, end = bucket[1], bucket[2]
	elif ctx.group_by == "City":
		extra = {"city": key if key != NOT_SET else ["is", "not set"]}
	else:
		return {}  # Retailer Type is not on Visit / Field Order

	def with_reps(filters):
		if reps is not None:
			filters["sales_rep"] = ["in", reps]
		return filters

	dates = [str(start), str(end)]
	drill = {"visits": with_reps({"visit_date": ["between", dates], "status": ["!=", "Cancelled"], **extra})}
	if not extra:  # Field Order has no city
		drill["orders"] = with_reps({"order_date": ["between", dates], "docstatus": 1})
	return drill


# ----------------------------------------------------------------------
# Chart and summary
# ----------------------------------------------------------------------


def _get_chart(ctx, rows: list[dict]) -> dict | None:
	if ctx.group_by in DATE_GROUPS:
		if not rows:
			return None
		datasets = [{"name": _("Order Value"), "values": [r["order_value"] for r in rows]}]
		if ctx.compare:
			# Comparison values were shifted onto these buckets in _collect.
			datasets.append({"name": _(ctx.compare), "values": [flt(r["cmp_order_value"]) for r in rows]})
		return {
			"data": {"labels": [r["group"] for r in rows], "datasets": datasets},
			"type": "line",
			"fieldtype": "Currency",
			"lineOptions": {"regionFill": 1},
		}

	leaves = [r for r in rows if not r.get("is_team") and (r["order_value"] or r["period_target"])]
	if not leaves:
		return None
	leaves = sorted(leaves, key=lambda r: -r["order_value"])[:20]
	datasets = [{"name": _("Order Value"), "chartType": "bar", "values": [r["order_value"] for r in leaves]}]
	if any(r["period_target"] for r in leaves):
		datasets.append(
			{"name": _("Target"), "chartType": "line", "values": [flt(r["period_target"]) for r in leaves]}
		)
	return {
		"data": {"labels": [r["group"] for r in leaves], "datasets": datasets},
		"type": "axis-mixed",
		"fieldtype": "Currency",
		"colors": ["#2490ef", "#e24c4c"],
	}


def _get_summary(ctx, comps) -> list[dict]:
	total = _new_comp()
	for comp in comps.values():
		_merge(total, comp)

	# Targets for the whole scope (date buckets carry per-bucket shares, so recompute).
	target = sum(prorated_target(r.monthly_target, ctx.from_date, ctx.to_date) for r in ctx.reps.values())
	cmp_target = (
		sum(prorated_target(r.monthly_target, ctx.cmp_from, ctx.cmp_to) for r in ctx.reps.values())
		if ctx.compare
		else 0
	)

	def ratio(a, b):
		return a / b if b else None

	current = {
		"visits": total["planned"],
		"completion": _pct(total["completed"], total["planned"]),
		"orders": total["orders"],
		"value": total["value"],
		"aov": ratio(total["value"], total["orders"]),
		"conversion": _pct(total["orders"], total["completed"]),
		"achievement": _pct(total["value"], target),
		"discount": total["discount"],
	}
	previous = {
		"visits": total["cmp_planned"],
		"completion": _pct(total["cmp_completed"], total["cmp_planned"]),
		"orders": total["cmp_orders"],
		"value": total["cmp_value"],
		"aov": ratio(total["cmp_value"], total["cmp_orders"]),
		"conversion": _pct(total["cmp_orders"], total["cmp_completed"]),
		"achievement": _pct(total["cmp_value"], cmp_target),
		"discount": total["cmp_discount"],
	}

	# (key, label, datatype, is_rate, lower_is_better)
	cards = (
		("visits", _("Total Visits"), "Int", False, False),
		("completion", _("Completion %"), "Percent", True, False),
		("orders", _("Total Orders"), "Int", False, False),
		("value", _("Total Value"), "Currency", False, False),
		("aov", _("Avg Order Value"), "Currency", False, False),
		("conversion", _("Conversion %"), "Percent", True, False),
		("achievement", _("Achievement %"), "Percent", True, False),
		("discount", _("Total Discount"), "Currency", False, True),
	)
	summary = []
	for key, label, datatype, is_rate, lower_is_better in cards:
		value, before = current[key], previous[key]
		indicator, suffix = "Blue", ""
		if ctx.compare and value is not None and before is not None:
			# Rates change in points; counts and amounts in percent.
			change = (value - before) if is_rate else _pct(value - before, before)
			if change is not None:
				arrow = "▲" if change > 0 else "▼" if change < 0 else "■"
				unit = _("pts") if is_rate else "%"
				suffix = f" · {arrow} {abs(flt(change, 1))}{unit}"
				better = change < 0 if lower_is_better else change > 0
				indicator = "Blue" if not change else "Green" if better else "Red"
		summary.append({"label": label + suffix, "value": flt(value, 2), "datatype": datatype, "indicator": indicator})
	return summary


# ----------------------------------------------------------------------
# Used by the daily manager summary
# ----------------------------------------------------------------------


def get_rep_stats(from_date, to_date, reps: dict) -> list[dict]:
	"""Flat per-rep rows (no tree, no comparison) for ``reps`` over the range."""
	if not reps:
		return []
	ctx = frappe._dict(
		from_date=getdate(from_date),
		to_date=getdate(to_date),
		today=getdate(nowdate()),
		group_by="Sales Rep",
		compare=None,
		reps=reps,
		all_reps=False,
	)
	comps = _collect(ctx)
	rows = []
	for rep, info in reps.items():
		comp = comps[rep] if rep in comps else _new_comp()
		if not (info.active or comp["planned"] or comp["orders"]):
			continue
		rows.append(
			{"sales_rep": rep, "sales_rep_name": info.full_name, "territory": info.territory, **_metrics(ctx, comp)}
		)
	return sorted(rows, key=lambda r: (-r["order_value"], r["sales_rep"]))
