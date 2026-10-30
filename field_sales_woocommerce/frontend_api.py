# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Endpoints for the Field Sales desktop app (frontend/). Lists go through
frappe.get_list, so the Sales Rep hierarchy rules apply as they do in the desk."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, nowdate, strip_html

from field_sales_woocommerce.commission import commission_enabled
from field_sales_woocommerce.connectors import ConnectorError, get_connector
from field_sales_woocommerce.payments.razorpay import PAYMENT_FIELDS, payment_links_disabled_reason
from field_sales_woocommerce.permissions import hierarchy_filters, is_retailer_user
from field_sales_woocommerce.sync.orders import MAX_AUTO_ATTEMPTS
from field_sales_woocommerce.utils.phone import whatsapp_digits
from field_sales_woocommerce.www.fieldsales import APP_ROLES, has_app_access

SEARCH_LIMIT = 6
MISSED_VISIT_DAYS = 7
NOT_VISITED_DAYS = 30


def get_attention_presets(today: str | None = None) -> dict[str, dict]:
	"""
	The dashboard's "Needs attention" lists, defined once.

	The dashboard counts with these filters and the app's list pages apply the
	same ones (via ``get_list_preset``), so a card and the list it opens always
	agree. Each preset is one doctype; "Failed Syncs" is two presets on one page.
	"""
	today = today or nowdate()
	# Every retailer is visible to a rep; these lists are the ones they act on.
	mine = hierarchy_filters("Retailer")
	return {
		"missed_visits": {
			"doctype": "Visit",
			"label": _("Missed visits (last {0} days)").format(MISSED_VISIT_DAYS),
			"filters": [
				["status", "=", "Planned"],
				["visit_date", "between", [add_days(today, -MISSED_VISIT_DAYS), add_days(today, -1)]],
			],
		},
		"not_visited_30d": {
			"doctype": "Retailer",
			"label": _("Not visited in {0} days").format(NOT_VISITED_DAYS),
			# Frappe compares an empty date as 0001-01-01, so this also matches
			# retailers that were never visited.
			"filters": [
				["status", "=", "Active"],
				["last_visit", "<", add_days(today, -NOT_VISITED_DAYS)],
				*mine,
			],
		},
		"draft_orders": {
			"doctype": "Field Order",
			"label": _("Draft orders"),
			"filters": [["docstatus", "=", 0]],
		},
		"pending_retailers": {
			"doctype": "Retailer",
			"label": _("Customers waiting to sync"),
			"filters": [["sync_status", "=", "Pending"], *mine],
		},
		"out_of_stock": {
			"doctype": "Platform Product",
			"label": _("Out of stock"),
			"filters": [["status", "=", "Active"], ["track_inventory", "=", 1], ["available_qty", "<=", 0]],
		},
		"failed_orders": {
			"doctype": "Field Order",
			"label": _("Orders that failed to sync"),
			"filters": [["docstatus", "=", 1], ["sync_status", "=", "Failed"]],
		},
		"failed_retailers": {
			"doctype": "Retailer",
			"label": _("Customers that failed to sync"),
			"filters": [["sync_status", "=", "Failed"], *mine],
		},
	}


def get_card_presets(today: str | None = None) -> dict[str, dict]:
	"""
	Presets for dashboard cards outside "Needs attention" (which the lists open).

	``get_list_preset`` serves these exactly like the attention presets.
	"""
	today = today or nowdate()
	return {
		"orders_today": {
			"doctype": "Field Order",
			"label": _("Orders submitted today"),
			# The same filters the "Orders Today" card counts.
			"filters": [["docstatus", "=", 1], ["order_date", "=", today]],
		},
	}


@frappe.whitelist()
def get_list_preset(key: str) -> dict:
	"""
	One "Needs attention" preset for a list page: ``{"key", "doctype", "label", "filters"}``.
	Only the definition: the list itself is fetched with get_list, under the user's permissions.
	"""
	_check_access()
	preset = get_attention_presets().get(key) or get_card_presets().get(key)
	if not preset:
		frappe.throw(_("Unknown list preset: {0}").format(key), frappe.DoesNotExistError)
	return {"key": key, **preset}


@frappe.whitelist()
def get_session() -> dict:
	"""
	The signed-in user, their app roles, their Sales Rep and the connected store.

	A retailer login (Field Sales Retailer role only, linked by Retailer.user)
	also gets ``is_retailer`` and its ``retailer``; an inactive or unlinked one
	is refused here, so My Orders never loads for it.

	:return: ``{"user", "full_name", "user_image", "roles", "is_admin", "is_manager", "is_retailer", "retailer", "sales_rep", "store", "commission_enabled"}``
	"""
	_check_access()
	user = frappe.session.user
	roles = [r for r in frappe.get_roles(user) if r in APP_ROLES]
	if user == "Administrator" and "System Manager" not in roles:
		roles.append("System Manager")

	retailer = None
	if is_retailer_user():
		retailer = frappe.db.get_value(
			"Retailer", {"user": user}, ["name", "retailer_name", "status"], as_dict=True
		)
		if not retailer:
			frappe.throw(
				_("Your login is not linked to a customer any more. Contact your sales rep."),
				frappe.PermissionError,
			)
		if retailer.status == "Inactive":
			frappe.throw(_("Your account is inactive. Contact your sales rep."), frappe.PermissionError)

	info = frappe.db.get_value("User", user, ["full_name", "user_image"], as_dict=True) or {}
	return {
		"user": user,
		"full_name": info.get("full_name") or user,
		"user_image": info.get("user_image"),
		"roles": roles,
		"is_admin": "System Manager" in roles,
		"is_manager": bool({"System Manager", "Sales Manager"} & set(roles)),
		"is_retailer": bool(retailer),
		"retailer": retailer,
		"sales_rep": frappe.db.get_value(
			"Sales Rep", {"user": user}, ["name", "full_name", "territory", "reports_to"], as_dict=True
		),
		"store": _store_info(),
		"commission_enabled": commission_enabled(),
	}


@frappe.whitelist()
def get_sidebar_counts() -> dict:
	"""Counts shown next to sidebar items, scoped to what the user can see."""
	_check_access()
	return {
		"retailers": _count("Retailer", {"status": "Active"}),
		"failed_orders": _count(**_preset_args("failed_orders")),
		"unread_notifications": frappe.db.count(
			"Notification Log", {"for_user": frappe.session.user, "read": 0}
		),
	}


@frappe.whitelist()
def get_notifications(limit: int = 50) -> list[dict]:
	"""The current user's latest Notification Log rows, newest first (subject as plain text)."""
	_check_access()
	rows = frappe.get_all(
		"Notification Log",
		filters={"for_user": frappe.session.user},
		fields=["name", "subject", "document_type", "document_name", "from_user", "read", "type", "creation"],
		order_by="creation desc",
		limit_page_length=min(cint(limit) or 50, 200),
	)
	for row in rows:
		row["subject"] = strip_html(row.subject or "")
	return rows


@frappe.whitelist()
def search(txt: str) -> dict:
	"""Command palette: retailers and orders matching ``txt``."""
	_check_access()
	txt = (txt or "").strip()
	if not txt:
		return {"retailers": [], "orders": []}

	like = f"%{txt}%"
	retailers = frappe.get_list(
		"Retailer",
		or_filters={"name": ("like", like), "retailer_name": ("like", like), "city": ("like", like), "phone": ("like", like)},
		fields=["name", "retailer_name", "city", "sync_status"],
		order_by="modified desc",
		limit_page_length=SEARCH_LIMIT,
	)
	orders = frappe.get_list(
		"Field Order",
		or_filters={"name": ("like", like), "retailer_name": ("like", like)},
		fields=["name", "retailer_name", "grand_total", "sync_status", "docstatus", "order_date"],
		order_by="modified desc",
		limit_page_length=SEARCH_LIMIT,
	)
	return {"retailers": retailers, "orders": orders}


@frappe.whitelist()
def get_dashboard() -> dict:
	"""
	Everything the Dashboard page shows, scoped to the user's hierarchy.

	:return: ``{"currency", "today": {...}, "attention": {...}, "visits_today", "recent_orders", "team"}``
	"""
	_check_access()
	today = nowdate()

	visits_today = frappe.get_list(
		"Visit",
		filters={"visit_date": today, "status": ("!=", "Cancelled")},
		fields=["name", "retailer", "retailer_name", "city", "sales_rep_name", "status", "check_in_time", "time_on_site_min"],
		order_by="status desc, modified desc",
		limit_page_length=50,
	)
	orders_today = frappe.get_list(
		"Field Order",
		filters={"order_date": today, "docstatus": 1},
		fields=["name", "grand_total"],
		limit_page_length=0,
	)
	counts = {status: 0 for status in ("Planned", "In Progress", "Completed")}
	for visit in visits_today:
		counts[visit.status] = counts.get(visit.status, 0) + 1

	return {
		"currency": frappe.db.get_default("currency"),
		"today": {
			"visits_planned": len(visits_today),
			"visits_completed": counts["Completed"],
			"visits_in_progress": counts["In Progress"],
			"orders": len(orders_today),
			"order_value": sum(flt(o.grand_total) for o in orders_today),
		},
		"attention": _attention_counts(today),
		"visits_today": visits_today[:8],
		"recent_orders": frappe.get_list(
			"Field Order",
			fields=["name", "retailer_name", "order_date", "grand_total", "docstatus", "sync_status"],
			order_by="creation desc",
			limit_page_length=6,
		),
		"team": _team_this_month(today),
		"charts": _charts(today),
		"failures": _failures(),
		"draft_orders": frappe.get_list(
			"Field Order",
			filters={"docstatus": 0},
			fields=["name", "retailer_name", "order_date", "grand_total"],
			order_by="modified desc",
			limit_page_length=5,
		),
	}


@frappe.whitelist()
def get_product_activity(product: str) -> dict:
	"""
	A product's last 30 days of sales, its recent orders and its sibling variants.
	Orders are read through get_list, so a rep only sees their own.

	:return: ``{"orders_30d", "units_30d", "value_30d", "recent_orders", "variants"}``
	"""
	_check_access()
	frappe.has_permission("Platform Product", "read", product, throw=True)
	since = add_days(nowdate(), -30)

	recent = frappe.get_list(
		"Field Order",
		filters=[["Field Order Item", "product", "=", product], ["Field Order", "docstatus", "!=", 2]],
		fields=["name", "retailer_name", "order_date", "docstatus", "sync_status", "`tabField Order Item`.qty as qty", "`tabField Order Item`.amount as amount"],
		order_by="`tabField Order`.order_date desc",
		limit_page_length=10,
	)
	recent_30d = frappe.get_list(
		"Field Order",
		filters=[["Field Order Item", "product", "=", product], ["Field Order", "docstatus", "=", 1], ["Field Order", "order_date", ">=", since]],
		fields=["name", "`tabField Order Item`.qty as qty", "`tabField Order Item`.amount as amount"],
		limit_page_length=0,
	)

	external_product_id = frappe.db.get_value("Platform Product", product, "external_product_id")
	variants = (
		frappe.get_list(
			"Platform Product",
			filters={"external_product_id": external_product_id, "name": ("!=", product)},
			fields=["name", "variant_title", "sku", "price", "available_qty", "track_inventory", "status"],
			order_by="price asc",
			limit_page_length=20,
		)
		if external_product_id
		else []
	)

	return {
		"orders_30d": len({r.name for r in recent_30d}),
		"units_30d": sum(flt(r.qty) for r in recent_30d),
		"value_30d": flt(sum(flt(r.amount) for r in recent_30d), 2),
		"recent_orders": recent,
		"variants": variants,
	}


@frappe.whitelist()
def get_order_sync_info(field_order: str) -> dict:
	"""
	What the order page's sync panel shows beyond the order's own fields.

	:return: ``{"location_name", "max_attempts", "order_mode", "store_url", "order_url", "draft_url"}``;
		the URLs are store admin pages (``store_url``: the order, else its draft), None when
		they cannot be built.
	"""
	_check_access()
	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("read")

	location_id, location_name, order_mode = frappe.db.get_value(
		"Store Connection", "Store Connection", ["default_location_id", "default_location_name", "order_mode"]
	) or (None, None, None)
	order_url = draft_url = None
	if doc.external_order_id or doc.external_draft_id:
		try:
			conn = get_connector()
			if doc.external_order_id:
				order_url = conn.get_order_url(doc.external_order_id)
			if doc.external_draft_id:
				draft_url = conn.get_order_url(None, doc.external_draft_id)
		except ConnectorError:
			pass  # store disconnected: the panel shows the IDs without a link
	return {
		"location_name": doc.location_name
		or (location_name if doc.location_id in (None, "", location_id) else doc.location_id),
		"max_attempts": MAX_AUTO_ATTEMPTS,
		"order_mode": order_mode,
		"store_url": order_url or draft_url,
		"order_url": order_url,
		"draft_url": draft_url,
	}


@frappe.whitelist()
def get_rep_month_stats(sales_rep: str | None = None) -> dict:
	"""
	Month-to-date numbers per Sales Rep the user can see (one rep with ``sales_rep``),
	computed as the Rep Performance report does. Inactive reps with no activity are left out.

	:return: ``{rep: {"completed_visits", "orders", "order_value", "achievement_pct", ...}}``
	"""
	_check_access()
	from field_sales_woocommerce.field_sales_woocommerce.report.rep_performance.rep_performance import (
		get_rep_stats,
		get_reps_in_scope,
	)

	today = nowdate()
	reps = get_reps_in_scope({"sales_rep": sales_rep} if sales_rep else {})
	keys = (
		"planned_visits",
		"completed_visits",
		"missed_visits",
		"completion_pct",
		"orders",
		"order_value",
		"avg_order_value",
		"conversion_pct",
		"retailers_visited",
		"failed_syncs",
		"monthly_target",
		"period_target",
		"achievement_pct",
	)
	return {
		row["sales_rep"]: {k: row.get(k) for k in keys}
		for row in get_rep_stats(get_first_day(today), today, reps)
	}


def _attention_counts(today: str) -> dict:
	"""Counts for the "Needs attention" cards, from the same presets the lists use."""
	counts = {key: _count(p["doctype"], p["filters"]) for key, p in get_attention_presets(today).items()}
	counts["failed_syncs"] = counts["failed_orders"] + counts["failed_retailers"]
	return counts


CHART_DAYS = 30


def _charts(today: str) -> dict:
	"""The desk workspace's four charts, as rows for frappe-ui charts (grouped in SQL, scoped by get_list)."""
	since = add_days(today, -(CHART_DAYS - 1))
	days = [add_days(since, i) for i in range(CHART_DAYS)]

	visits = {
		str(r.visit_date): r.count
		for r in frappe.get_list(
			"Visit",
			filters={"status": "Completed", "visit_date": (">=", since)},
			fields=["visit_date", {"COUNT": "*", "as": "count"}],
			group_by="visit_date",
		)
	}
	value = {
		str(r.order_date): flt(r.value)
		for r in frappe.get_list(
			"Field Order",
			filters={"docstatus": 1, "order_date": (">=", since)},
			fields=["order_date", {"SUM": "grand_total", "as": "value"}],
			group_by="order_date",
		)
	}
	by_status = frappe.get_list(
		"Visit",
		filters={"visit_date": (">=", get_first_day(today))},
		fields=["status", {"COUNT": "*", "as": "count"}],
		group_by="status",
	)
	by_rep = frappe.get_list(
		"Field Order",
		filters={"docstatus": 1, "order_date": (">=", add_days(today, -6))},
		fields=["sales_rep", {"COUNT": "*", "as": "orders"}, {"SUM": "grand_total", "as": "value"}],
		group_by="sales_rep",
	)
	names = dict(
		frappe.get_all(
			"Sales Rep", filters={"name": ("in", [r.sales_rep for r in by_rep])}, fields=["name", "full_name"], as_list=True
		)
	) if by_rep else {}

	return {
		"visits_30d": [{"date": str(d), "visits": visits.get(str(d), 0)} for d in days],
		"order_value_30d": [{"date": str(d), "value": flt(value.get(str(d), 0.0), 2)} for d in days],
		"visits_by_status": [{"status": r.status, "count": r.count} for r in by_status],
		"orders_by_rep_7d": sorted(
			[{"rep": names.get(r.sales_rep) or r.sales_rep, "orders": r.orders, "value": flt(r.value, 2)} for r in by_rep],
			key=lambda r: -r["value"],
		),
	}


def _failures() -> list[dict]:
	"""Failed order and retailer syncs, newest first (the workspace's Sync Failures list)."""
	presets = get_attention_presets()
	orders = frappe.get_list(
		"Field Order",
		filters=presets["failed_orders"]["filters"],
		fields=["name", "retailer_name as title", "sync_error", "last_sync_attempt"],
		order_by="last_sync_attempt desc",
		limit_page_length=5,
	)
	retailers = frappe.get_list(
		"Retailer",
		filters=presets["failed_retailers"]["filters"],
		fields=["name", "retailer_name as title", "sync_error", "last_sync_attempt"],
		order_by="last_sync_attempt desc",
		limit_page_length=5,
	)
	rows = [{**o, "type": "Order"} for o in orders] + [{**r, "type": "Retailer"} for r in retailers]
	return sorted(rows, key=lambda r: str(r.get("last_sync_attempt") or ""), reverse=True)[:5]


def _team_this_month(today: str) -> list[dict]:
	"""Month-to-date per rep for managers (same numbers as Rep Performance); empty for reps."""
	if not ({"System Manager", "Sales Manager"} & set(frappe.get_roles())):
		return []

	from field_sales_woocommerce.field_sales_woocommerce.report.rep_performance.rep_performance import (
		get_rep_stats,
		get_reps_in_scope,
	)

	rows = get_rep_stats(get_first_day(today), today, get_reps_in_scope({}))
	keys = ("sales_rep", "sales_rep_name", "territory", "completed_visits", "orders", "order_value", "achievement_pct", "conversion_pct")
	return [{k: row.get(k) for k in keys} for row in rows[:10]]


@frappe.whitelist(methods=["GET"])
def get_order_payment_info(field_order: str) -> dict:
	"""
	Order Detail's Payment card: the order's payment fields, whether Razorpay is enabled,
	whether the user may create links, and the retailer's WhatsApp number (digits with the
	country prefix, e.g. ``919876543210``, for the retailer's country; ``None`` if not a mobile).
	"""
	from field_sales_woocommerce.field_sales_woocommerce.doctype.retailer.retailer import country_code_of

	_check_access()
	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("read")
	retailer = frappe.db.get_value("Retailer", doc.retailer, ["phone", "country"], as_dict=True) or {}
	# Retailers without a country predate the field and are Indian.
	whatsapp = whatsapp_digits(retailer.get("phone"), country_code_of(retailer.get("country")) or "IN")
	return {
		"name": doc.name,
		**{f: doc.get(f) for f in PAYMENT_FIELDS},
		"docstatus": doc.docstatus,
		"grand_total": doc.grand_total,
		"order_label": doc.store_order_name or doc.name,
		"store_checkout_url": doc.store_checkout_url,
		"retailer_name": doc.retailer_name,
		"razorpay_enabled": bool(cint(frappe.db.get_single_value("Razorpay Settings", "enabled"))),
		"payment_links_available": payment_links_disabled_reason() is None,
		"can_create_link": bool(doc.has_permission("write")),
		"whatsapp_number": whatsapp,
	}


def _check_access():
	if frappe.session.user == "Guest" or not has_app_access():
		frappe.throw(_("You do not have access to Field Sales"), frappe.PermissionError)


def _preset_args(key: str) -> dict:
	preset = get_attention_presets()[key]
	return {"doctype": preset["doctype"], "filters": preset["filters"]}


def _count(doctype: str, filters: dict | list) -> int:
	rows = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "*", "as": "count"}])
	return rows[0].count if rows else 0


def _store_info() -> dict:
	"""Store name for the sidebar header: the shop's domain without scheme or path."""
	values = frappe.db.get_value(
		"Store Connection", "Store Connection", ["platform", "enabled", "store_url"], as_dict=True
	) or {}
	url = (values.get("store_url") or "").strip().lower()
	host = url.split("://", 1)[-1].split("/", 1)[0]
	return {
		"name": host.removesuffix(".myshopify.com") or None,
		"platform": values.get("platform"),
		"enabled": bool(values.get("enabled")),
	}
