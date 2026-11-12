# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Permission-scoped mobile list APIs with cursor/keyset pagination,
server-enforced page limits (default 50, max 100), lightweight card fields,
lazy-loaded detail endpoints, separate count queries, and debounced prefix search.
"""

import base64
import json
import re
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, getdate, nowdate

from field_sales_woocommerce.commission import commission_enabled
from field_sales_woocommerce.frontend_api import _check_access, get_attention_presets, get_card_presets
from field_sales_woocommerce.permissions import (
	commission_statement_query,
	field_order_query,
	get_allowed_reps,
	get_own_retailer,
	is_admin,
	is_retailer_user,
	retailer_query,
	sales_rep_query,
	visit_query,
)
from field_sales_woocommerce.utils.search import escape_like

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_SEARCH_SUGGESTIONS = 20
MIN_SEARCH_LENGTH = 2


# ──────────────────────────────────────────────────────────────────────────────
# Cursor Encoding / Decoding & Safety Helpers
# ──────────────────────────────────────────────────────────────────────────────


def encode_cursor(payload: dict) -> str:
	"""Encode cursor dict as URL-safe base64 string without trailing padding."""
	raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
	return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor_str: str | None, expected_keys: tuple[str, ...]) -> dict | None:
	"""
	Safely decode, validate, and sanitize a pagination cursor string.

	Treats cursor as untrusted client input:
	- Max length capped at 512 bytes to prevent DoS.
	- Strict base64 and JSON schema validation.
	- Returns None on any parsing or validation failure (falls back to page 1).
	"""
	if not cursor_str or not isinstance(cursor_str, str):
		return None
	cursor_str = cursor_str.strip()
	if not cursor_str or len(cursor_str) > 512:
		return None

	# Add base64 padding if stripped
	rem = len(cursor_str) % 4
	if rem:
		cursor_str += "=" * (4 - rem)

	try:
		raw = base64.urlsafe_b64decode(cursor_str.encode("ascii")).decode("utf-8")
		data = json.loads(raw)
		if not isinstance(data, dict):
			return None
		for k in expected_keys:
			if k not in data or data[k] is None:
				return None
		return data
	except Exception:
		return None


def clamp_limit(limit: int | str | None) -> int:
	"""Server-enforced page size: default 50, maximum 100."""
	try:
		val = cint(limit) or DEFAULT_PAGE_SIZE
		return min(max(1, val), MAX_PAGE_SIZE)
	except Exception:
		return DEFAULT_PAGE_SIZE


def _build_keyset_condition(
	order_by: list[tuple[str, str]],
	cursor: dict,
	params: dict,
	param_prefix: str = "_kc_",
) -> str:
	"""
	Builds a keyset pagination WHERE condition supporting composite sort keys.

	Example for [('order_date', 'desc'), ('modified', 'desc'), ('name', 'desc')]:
	  (order_date < :d)
	  OR (order_date = :d AND modified < :m)
	  OR (order_date = :d AND modified = :m AND name < :n)
	"""
	disjunctions = []
	for i in range(len(order_by)):
		conjunctions = []
		# Previous columns must match exactly
		for j in range(i):
			col, _ = order_by[j]
			pname = f"{param_prefix}eq_{j}_{col}"
			params[pname] = cursor[col]
			conjunctions.append(f"`{col}` = %({pname})s")
		# The current column applies inequality according to its sort direction
		col, direction = order_by[i]
		op = "<" if direction.lower() == "desc" else ">"
		pname = f"{param_prefix}cmp_{i}_{col}"
		params[pname] = cursor[col]
		conjunctions.append(f"`{col}` {op} %({pname})s")

		disjunctions.append(" AND ".join(conjunctions))

	return f"({' OR '.join(f'({d})' for d in disjunctions)})"


def _format_field(doctype: str, field: str) -> str:
	field = field.strip()
	if " as " in field.lower():
		parts = field.split(" as ", 1)
		return f"`tab{doctype}`.`{parts[0].strip()}` as `{parts[1].strip()}`"
	return f"`tab{doctype}`.`{field}`"


def _execute_keyset_query(
	doctype: str,
	fields: list[str],
	order_by: list[tuple[str, str]],
	cursor_dict: dict | None,
	where_clauses: list[str],
	params: dict,
	limit: int,
) -> dict:
	"""
	Executes a keyset query fetching (limit + 1) rows to determine has_more.
	Generates next_cursor from the last item on the page.
	"""
	limit = clamp_limit(limit)
	fetch_count = limit + 1

	if cursor_dict:
		keyset_sql = _build_keyset_condition(order_by, cursor_dict, params)
		where_clauses.append(keyset_sql)

	where_str = " AND ".join(f"({w})" for w in where_clauses) if where_clauses else "1=1"
	order_str = ", ".join(f"`tab{doctype}`.`{col}` {direction.upper()}" for col, direction in order_by)
	fields_str = ", ".join(_format_field(doctype, f) for f in fields)

	query = f"""
		SELECT {fields_str}
		FROM `tab{doctype}`
		WHERE {where_str}
		ORDER BY {order_str}
		LIMIT {fetch_count}
	"""

	rows = frappe.db.sql(query, params, as_dict=True)

	has_more = len(rows) > limit
	if has_more:
		rows = rows[:limit]

	next_cursor = None
	if has_more and rows:
		last = rows[-1]
		cursor_payload = {col: str(last.get(col) or "") for col, _ in order_by}
		next_cursor = encode_cursor(cursor_payload)

	return {
		"items": rows,
		"has_more": has_more,
		"next_cursor": next_cursor,
		"limit": limit,
	}


# ──────────────────────────────────────────────────────────────────────────────
# 1. Orders Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


ORDER_CARD_FIELDS = [
	"name",
	"order_date",
	"retailer",
	"retailer_name",
	"grand_total",
	"docstatus",
	"sync_status",
	"payment_status",
	"amount_paid",
	"sales_rep",
	"visit",
	"total_qty",
	"net_total",
	"discount_amount",
	"tax_amount",
	"location_name",
	"remarks",
	"modified",
]

PAYMENT_STATUSES = ("Unpaid", "Link Sent", "Partially Paid", "Paid", "Link Expired", "Link Cancelled")

ORDER_KEYS = (("order_date", "desc"), ("modified", "desc"), ("name", "desc"))


@frappe.whitelist(methods=["GET", "POST"])
def get_orders_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	status: str | None = None,
	docstatus: int | str | None = None,
	retailer: str | None = None,
	sales_rep: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	search: str | None = None,
	payment_status: str | None = None,
) -> dict:
	"""
	Keyset-paginated mobile orders list. Returns card fields only (excludes child items).
	Applies Frappe permission queries and hierarchy / retailer isolation.
	"""
	_check_access()
	user = frappe.session.user

	perm = field_order_query(user)
	if perm == "1=0":
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	where = []
	params = {}

	if perm:
		where.append(perm)

	# Validate untrusted filters
	if is_retailer_user(user):
		own = get_own_retailer(user)
		if not own:
			return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}
		where.append("`tabField Order`.`retailer` = %(own_retailer)s")
		params["own_retailer"] = own
	elif retailer:
		where.append("`tabField Order`.`retailer` = %(f_retailer)s")
		params["f_retailer"] = retailer.strip()

	if docstatus is not None and str(docstatus).strip() != "":
		where.append("`tabField Order`.`docstatus` = %(f_docstatus)s")
		params["f_docstatus"] = cint(docstatus)

	if status and status.strip() in ("Pending", "Synced", "Failed"):
		where.append("`tabField Order`.`sync_status` = %(f_status)s")
		params["f_status"] = status.strip()

	if payment_status and payment_status.strip() in PAYMENT_STATUSES:
		where.append("`tabField Order`.`payment_status` = %(f_payment_status)s")
		params["f_payment_status"] = payment_status.strip()

	if sales_rep and not is_retailer_user(user):
		allowed = get_allowed_reps(user)
		if is_admin(user) or sales_rep.strip() in allowed:
			where.append("`tabField Order`.`sales_rep` = %(f_rep)s")
			params["f_rep"] = sales_rep.strip()

	if date_from:
		where.append("`tabField Order`.`order_date` >= %(f_date_from)s")
		params["f_date_from"] = str(getdate(date_from))

	if date_to:
		where.append("`tabField Order`.`order_date` <= %(f_date_to)s")
		params["f_date_to"] = str(getdate(date_to))

	if search and len(search.strip()) >= MIN_SEARCH_LENGTH:
		term = search.strip()
		where.append("(`tabField Order`.`name` LIKE %(f_search)s OR `tabField Order`.`retailer_name` LIKE %(f_search)s)")
		params["f_search"] = f"{escape_like(term)}%"

	decoded_cursor = decode_cursor(cursor, ("order_date", "modified", "name"))
	return _execute_keyset_query(
		doctype="Field Order",
		fields=ORDER_CARD_FIELDS,
		order_by=list(ORDER_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Visits Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


VISIT_CARD_FIELDS = [
	"name",
	"visit_date",
	"retailer",
	"retailer_name",
	"city",
	"status",
	"sales_rep",
	"sales_rep_name",
	"check_in_time",
	"check_out_time",
	"time_on_site_min",
	"visit_purpose",
	"notes",
	"order_taken",
	"no_order_reason",
	"modified",
]

VISIT_KEYS = (("visit_date", "desc"), ("modified", "desc"), ("name", "desc"))


@frappe.whitelist(methods=["GET", "POST"])
def get_visits_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	status: str | None = None,
	retailer: str | None = None,
	sales_rep: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	search: str | None = None,
) -> dict:
	"""
	Keyset-paginated mobile visits list. Returns card fields only (excludes debug/geofence history).
	Retailer logins cannot access visits.
	"""
	_check_access()
	user = frappe.session.user

	if is_retailer_user(user):
		frappe.throw(_("Retailer accounts do not have access to visits"), frappe.PermissionError)

	perm = visit_query(user)
	if perm == "1=0":
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	where = []
	params = {}

	if perm:
		where.append(perm)

	if status and status.strip() in ("Planned", "In Progress", "Completed", "Cancelled"):
		where.append("`tabVisit`.`status` = %(f_status)s")
		params["f_status"] = status.strip()

	if retailer:
		where.append("`tabVisit`.`retailer` = %(f_retailer)s")
		params["f_retailer"] = retailer.strip()

	if sales_rep:
		allowed = get_allowed_reps(user)
		if is_admin(user) or sales_rep.strip() in allowed:
			where.append("`tabVisit`.`sales_rep` = %(f_rep)s")
			params["f_rep"] = sales_rep.strip()

	if date_from:
		where.append("`tabVisit`.`visit_date` >= %(f_date_from)s")
		params["f_date_from"] = str(getdate(date_from))

	if date_to:
		where.append("`tabVisit`.`visit_date` <= %(f_date_to)s")
		params["f_date_to"] = str(getdate(date_to))

	if search and len(search.strip()) >= MIN_SEARCH_LENGTH:
		term = search.strip()
		where.append("(`tabVisit`.`name` LIKE %(f_search)s OR `tabVisit`.`retailer_name` LIKE %(f_search)s OR `tabVisit`.`city` LIKE %(f_search)s)")
		params["f_search"] = f"{escape_like(term)}%"

	decoded_cursor = decode_cursor(cursor, ("visit_date", "modified", "name"))
	return _execute_keyset_query(
		doctype="Visit",
		fields=VISIT_CARD_FIELDS,
		order_by=list(VISIT_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Customers / Retailers Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


CUSTOMER_CARD_FIELDS = [
	"name",
	"retailer_name",
	"city",
	"phone",
	"status",
	"sales_rep",
	"sync_status",
	"last_visit",
	"owner_name",
	"retailer_type",
	"latitude",
	"longitude",
	"modified",
]

RETAILER_TYPES = ("Kirana", "Supermarket", "Wholesaler", "Medical", "Other")

CUSTOMER_KEYS = (("modified", "desc"), ("name", "desc"))


def _preset_conditions(doctype: str, preset: str) -> list[str]:
	"""
	SQL conditions of a dashboard preset for ``doctype``, built by Frappe's own filter
	compiler (values escaped, empty dates compared as 0001-01-01, like the card's count).
	"""
	from frappe.model.db_query import DatabaseQuery

	data = get_attention_presets().get(preset) or get_card_presets().get(preset)
	if not data or data["doctype"] != doctype:
		frappe.throw(_("Unknown list preset: {0}").format(preset), frappe.ValidationError)
	query = DatabaseQuery(doctype)
	return [query.prepare_filter_condition(f) for f in data["filters"]]


@frappe.whitelist(methods=["GET", "POST"])
def get_customers_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	status: str | None = None,
	city: str | None = None,
	sales_rep: str | None = None,
	search: str | None = None,
	retailer_type: str | None = None,
	preset: str | None = None,
) -> dict:
	"""
	Keyset-paginated mobile customers list. Excludes tax documents and heavy contact tables.
	Retailer login only sees their own retailer profile.

	``preset`` names a Retailer "Needs attention" preset (e.g. ``not_visited_30d``), so the
	list a dashboard card opens applies the same filters the card counted.
	"""
	_check_access()
	user = frappe.session.user

	perm = retailer_query(user)
	if perm == "1=0":
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	where = []
	params = {}

	if perm:
		where.append(perm)

	if is_retailer_user(user):
		own = get_own_retailer(user)
		if not own:
			return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}
		where.append("`tabRetailer`.`name` = %(own_retailer)s")
		params["own_retailer"] = own
	else:
		if status and status.strip() in ("Active", "Inactive"):
			where.append("`tabRetailer`.`status` = %(f_status)s")
			params["f_status"] = status.strip()

		if retailer_type and retailer_type.strip() in RETAILER_TYPES:
			where.append("`tabRetailer`.`retailer_type` = %(f_type)s")
			params["f_type"] = retailer_type.strip()

		if city:
			where.append("`tabRetailer`.`city` = %(f_city)s")
			params["f_city"] = city.strip()

		if sales_rep:
			allowed = get_allowed_reps(user)
			if is_admin(user) or sales_rep.strip() in allowed:
				where.append("`tabRetailer`.`sales_rep` = %(f_rep)s")
				params["f_rep"] = sales_rep.strip()

		if preset:
			where.extend(_preset_conditions("Retailer", preset))

	if search and len(search.strip()) >= MIN_SEARCH_LENGTH:
		term = search.strip()
		where.append(
			"(`tabRetailer`.`name` LIKE %(f_search)s OR `tabRetailer`.`retailer_name` LIKE %(f_search)s OR `tabRetailer`.`phone` LIKE %(f_search)s OR `tabRetailer`.`city` LIKE %(f_search)s)"
		)
		params["f_search"] = f"{escape_like(term)}%"

	decoded_cursor = decode_cursor(cursor, ("modified", "name"))
	return _execute_keyset_query(
		doctype="Retailer",
		fields=CUSTOMER_CARD_FIELDS,
		order_by=list(CUSTOMER_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Products & Location Stock Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


PRODUCT_CARD_FIELDS = [
	"name",
	"product_title as title",
	"variant_title",
	"sku",
	"barcode",
	"uom",
	"price",
	"compare_at_price",
	"available_qty",
	"track_inventory",
	"status",
	"image",
	"modified",
]

PRODUCT_KEYS = (("modified", "desc"), ("name", "desc"))


@frappe.whitelist(methods=["GET", "POST"])
def get_products_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	status: str | None = "Active",
	tab: str | None = None,
	search: str | None = None,
	barcode: str | None = None,
	track_inventory: int | str | None = None,
	location_id: str | None = None,
) -> dict:
	"""
	Keyset-paginated products list with location stock and barcode support.
	Excludes raw HTML descriptions and multi-location tables from list queries.
	"""
	_check_access()
	frappe.has_permission("Platform Product", "read", throw=True)

	where = []
	params = {}

	# Tab filters: 'Active', 'low', 'all'
	if tab == "low":
		where.append("`tabPlatform Product`.`status` = 'Active'")
		where.append("`tabPlatform Product`.`track_inventory` = 1")
		where.append("`tabPlatform Product`.`available_qty` <= 5")
	elif tab == "all":
		pass
	elif status and status.strip() in ("Active", "Archived", "Draft"):
		where.append("`tabPlatform Product`.`status` = %(f_status)s")
		params["f_status"] = status.strip()

	if track_inventory is not None and str(track_inventory).strip() != "":
		where.append("`tabPlatform Product`.`track_inventory` = %(f_track)s")
		params["f_track"] = cint(track_inventory)

	if barcode and barcode.strip():
		code = barcode.strip()
		variants = [code]
		if re.match(r"^0\d{12}$", code):
			variants.append(code[1:])
		elif re.match(r"^\d{12}$", code):
			variants.append(f"0{code}")
		where.append("`tabPlatform Product`.`barcode` IN %(f_barcodes)s")
		params["f_barcodes"] = tuple(variants)

	if search and len(search.strip()) >= MIN_SEARCH_LENGTH:
		term = search.strip()
		where.append(
			"(`tabPlatform Product`.`name` LIKE %(f_search)s OR `tabPlatform Product`.`product_title` LIKE %(f_search)s OR `tabPlatform Product`.`sku` LIKE %(f_search)s OR `tabPlatform Product`.`barcode` LIKE %(f_search)s)"
		)
		params["f_search"] = f"{escape_like(term)}%"

	decoded_cursor = decode_cursor(cursor, ("modified", "name"))
	res = _execute_keyset_query(
		doctype="Platform Product",
		fields=PRODUCT_CARD_FIELDS,
		order_by=list(PRODUCT_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)

	# If a selling location is selected, fetch and overlay stock for this page only
	if location_id and res.get("items"):
		loc_id = location_id.strip()
		tracked_names = [it["name"] for it in res["items"] if it.get("track_inventory")]
		if tracked_names:
			stock_rows = frappe.get_all(
				"Product Stock",
				filters={"product": ["in", tracked_names], "location_id": loc_id},
				fields=["product", "available_qty"],
			)
			stock_map = {row["product"]: flt(row["available_qty"]) for row in stock_rows}
			for item in res["items"]:
				if item.get("track_inventory"):
					item["available_qty"] = stock_map.get(item["name"], 0.0)
					item["location_id"] = loc_id

	return res


# ──────────────────────────────────────────────────────────────────────────────
# 5. Sales Team Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


TEAM_CARD_FIELDS = [
	"name",
	"full_name",
	"territory",
	"active",
	"reports_to",
	"user",
	"modified",
]

TEAM_KEYS = (("full_name", "asc"), ("name", "asc"))


@frappe.whitelist(methods=["GET", "POST"])
def get_team_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	search: str | None = None,
) -> dict:
	"""
	Keyset-paginated sales team list scoped to manager hierarchy.
	Retailer accounts cannot access this list.
	"""
	_check_access()
	user = frappe.session.user

	if is_retailer_user(user):
		frappe.throw(_("Retailer accounts do not have access to the sales team"), frappe.PermissionError)

	perm = sales_rep_query(user)
	if perm == "1=0":
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	where = []
	params = {}

	if perm:
		where.append(perm)

	if search and len(search.strip()) >= MIN_SEARCH_LENGTH:
		term = search.strip()
		where.append("(`tabSales Rep`.`name` LIKE %(f_search)s OR `tabSales Rep`.`full_name` LIKE %(f_search)s)")
		params["f_search"] = f"{escape_like(term)}%"

	decoded_cursor = decode_cursor(cursor, ("full_name", "name"))
	return _execute_keyset_query(
		doctype="Sales Rep",
		fields=TEAM_CARD_FIELDS,
		order_by=list(TEAM_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Commission Statements Mobile List API
# ──────────────────────────────────────────────────────────────────────────────


COMMISSION_CARD_FIELDS = [
	"name",
	"sales_rep",
	"period_start",
	"period_end",
	"status",
	"net_payable",
	"modified",
]

COMMISSION_KEYS = (("period_start", "desc"), ("name", "desc"))


@frappe.whitelist(methods=["GET", "POST"])
def get_commission_statements_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	sales_rep: str | None = None,
	status: str | None = None,
) -> dict:
	"""
	Keyset-paginated commission statements list. Excludes individual entry child rows.
	"""
	_check_access()
	user = frappe.session.user

	if not commission_enabled():
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	if is_retailer_user(user):
		frappe.throw(_("Retailers do not have access to commission statements"), frappe.PermissionError)

	perm = commission_statement_query(user)
	if perm == "1=0":
		return {"items": [], "has_more": False, "next_cursor": None, "limit": clamp_limit(limit)}

	where = []
	params = {}

	if perm:
		where.append(perm)

	if status and status.strip() in ("Draft", "Approved", "Paid"):
		where.append("`tabCommission Statement`.`status` = %(f_status)s")
		params["f_status"] = status.strip()

	if sales_rep:
		allowed = get_allowed_reps(user)
		if is_admin(user) or sales_rep.strip() in allowed:
			where.append("`tabCommission Statement`.`sales_rep` = %(f_rep)s")
			params["f_rep"] = sales_rep.strip()

	decoded_cursor = decode_cursor(cursor, ("period_start", "name"))
	return _execute_keyset_query(
		doctype="Commission Statement",
		fields=COMMISSION_CARD_FIELDS,
		order_by=list(COMMISSION_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)
	return _execute_keyset_query(
		doctype="Commission Statement",
		fields=COMMISSION_CARD_FIELDS,
		order_by=list(COMMISSION_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 7. Retailer Payments & Orders Mobile APIs
# ──────────────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["GET", "POST"])
def get_retailer_orders_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
	status: str | None = None,
) -> dict:
	"""Retailer My Orders list, strictly scoped to the authenticated retailer user."""
	_check_access()
	user = frappe.session.user
	own = get_own_retailer(user)
	if not own and not is_admin(user):
		frappe.throw(_("Login is not linked to an active customer"), frappe.PermissionError)

	return get_orders_list(
		cursor=cursor,
		limit=limit,
		retailer=own,
		status=status,
	)


@frappe.whitelist(methods=["GET", "POST"])
def get_retailer_payments_list(
	cursor: str | None = None,
	limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Retailer payment history scoped to their own retailer orders."""
	_check_access()
	user = frappe.session.user
	own = get_own_retailer(user)
	if not own and not is_admin(user):
		frappe.throw(_("Login is not linked to an active customer"), frappe.PermissionError)

	# In Field Sales, payments are attached to submitted Field Orders
	where = ["`tabField Order`.`docstatus` = 1", "`tabField Order`.`payment_status` = 'Paid'"]
	params = {}
	if own:
		where.append("`tabField Order`.`retailer` = %(own_retailer)s")
		params["own_retailer"] = own

	payment_fields = [
		"name",
		"order_date as payment_date",
		"grand_total as amount",
		"payment_status as status",
		"razorpay_payment_id as payment_reference",
		"paid_on",
		"name as order_id",
		# Keyset keys (ORDER_KEYS): the next cursor is read from these.
		"order_date",
		"modified",
	]

	decoded_cursor = decode_cursor(cursor, ("order_date", "modified", "name"))
	return _execute_keyset_query(
		doctype="Field Order",
		fields=payment_fields,
		order_by=list(ORDER_KEYS),
		cursor_dict=decoded_cursor,
		where_clauses=where,
		params=params,
		limit=limit,
	)


# ──────────────────────────────────────────────────────────────────────────────
# 8. Detail Endpoints (Heavy Fields On-Demand)
# ──────────────────────────────────────────────────────────────────────────────


# Returned as None to a retailer login by get_order_detail (keys kept so clients parse the same shape).
RETAILER_HIDDEN_ORDER_FIELDS = ("sync_error", "location_id")


@frappe.whitelist(methods=["GET"])
def get_order_detail(order_id: str) -> dict:
	"""
	Fetches full order details including child items table, sync errors,
	and payment actions only when the detail screen opens.
	"""
	_check_access()
	order_id = (order_id or "").strip()
	if not order_id:
		frappe.throw(_("Order ID is required"), frappe.ValidationError)

	doc = frappe.get_doc("Field Order", order_id)
	doc.check_permission("read")

	items = [
		{
			"name": item.name,
			"product": item.product,
			"product_title": item.product_title,
			"variant_title": item.variant_title,
			"sku": item.sku,
			"qty": flt(item.qty),
			"rate": flt(item.rate),
			"amount": flt(item.amount),
			"available_qty": flt(item.available_qty or 0.0),
		}
		for item in doc.items
	]

	detail = {
		"name": doc.name,
		"order_date": str(doc.order_date),
		"retailer": doc.retailer,
		"retailer_name": doc.retailer_name,
		"sales_rep": doc.sales_rep,
		"grand_total": flt(doc.grand_total),
		"subtotal": flt(doc.net_total),
		"net_total": flt(doc.net_total),
		"tax_percent": flt(doc.tax_percent or 0.0),
		"discount_percent": flt(doc.discount_percent or 0.0),
		"tax_amount": flt(doc.tax_amount or 0.0),
		"discount_amount": flt(doc.discount_amount or 0.0),
		"docstatus": doc.docstatus,
		"sync_status": doc.sync_status,
		"sync_error": doc.sync_error,
		"store_order_name": doc.store_order_name,
		"store_checkout_url": doc.store_checkout_url,
		"payment_status": doc.payment_status,
		"location_id": doc.location_id,
		"visit": doc.visit,
		"remarks": doc.remarks,
		"items": items,
		"modified": str(doc.modified),
	}
	if is_retailer_user():
		# Store sync internals (error text from the store, its location id) are for staff only.
		for key in RETAILER_HIDDEN_ORDER_FIELDS:
			detail[key] = None
	return detail


@frappe.whitelist(methods=["GET"])
def get_customer_detail_records(customer_id: str) -> dict:
	"""
	Fetches full customer details, plus paginated recent 5 orders and 5 visits.
	"""
	_check_access()
	customer_id = (customer_id or "").strip()
	if not customer_id:
		frappe.throw(_("Customer ID is required"), frappe.ValidationError)

	doc = frappe.get_doc("Retailer", customer_id)
	doc.check_permission("read")

	# Outstanding balance via SQL aggregate, over the orders the user may see: every rep reads
	# every retailer, but another team's orders are not theirs to total.
	order_scope = field_order_query(frappe.session.user) or "1=1"
	unpaid_orders = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(grand_total), 0) as balance
		FROM `tabField Order`
		WHERE retailer = %(ret)s AND docstatus = 1 AND payment_status != 'Paid' AND ({order_scope})
		""",
		{"ret": customer_id},
		as_dict=True,
	)
	outstanding = flt(unpaid_orders[0].balance if unpaid_orders else 0.0, 2)

	recent_orders = get_orders_list(retailer=customer_id, limit=5)
	recent_visits = get_visits_list(retailer=customer_id, limit=5) if not is_retailer_user() else {"items": []}

	return {
		"customer": {
			"name": doc.name,
			"retailer_name": doc.retailer_name,
			"contact_person": getattr(doc, "owner_name", None),
			"phone": doc.phone,
			"email": doc.email,
			"address": f"{getattr(doc, 'address_line1', '') or ''} {getattr(doc, 'address_line2', '') or ''}".strip(),
			"city": doc.city,
			"state": doc.state,
			"pincode": doc.pincode,
			"gstin": doc.gstin,
			"status": doc.status,
			"sales_rep": doc.sales_rep,
			"sync_status": doc.sync_status,
			"outstanding_balance": outstanding,
		},
		"recent_orders": recent_orders.get("items", []),
		"recent_visits": recent_visits.get("items", []),
	}


@frappe.whitelist(methods=["GET"])
def get_visit_detail(visit_id: str) -> dict:
	"""
	Fetches complete visit detail including check-in/out GPS coordinates,
	distance from shop, and notes.
	"""
	_check_access()
	visit_id = (visit_id or "").strip()
	if not visit_id:
		frappe.throw(_("Visit ID is required"), frappe.ValidationError)

	doc = frappe.get_doc("Visit", visit_id)
	doc.check_permission("read")

	return {
		"name": doc.name,
		"visit_date": str(doc.visit_date),
		"retailer": doc.retailer,
		"retailer_name": doc.retailer_name,
		"sales_rep": doc.sales_rep,
		"sales_rep_name": doc.sales_rep_name,
		"status": doc.status,
		"check_in_time": str(doc.check_in_time) if doc.check_in_time else None,
		"check_out_time": str(doc.check_out_time) if doc.check_out_time else None,
		"time_on_site_min": doc.time_on_site_min,
		"latitude": doc.latitude,
		"longitude": doc.longitude,
		"location_accuracy_m": doc.location_accuracy_m,
		"retailer_latitude": doc.retailer_latitude,
		"retailer_longitude": doc.retailer_longitude,
		"distance_from_retailer_m": doc.distance_from_retailer_m,
		"within_geofence": doc.within_geofence,
		"notes": doc.notes,
		"no_order_reason": doc.no_order_reason,
		"no_order_remarks": doc.no_order_remarks,
		"order_taken": doc.order_taken,
	}


@frappe.whitelist(methods=["GET"])
def get_product_detail_stock(product_id: str) -> dict:
	"""Fetches multi-location stock breakdown for a specific product."""
	_check_access()
	frappe.has_permission("Platform Product", "read", product_id, throw=True)
	from field_sales_woocommerce.sync.stock import get_sell_locations

	product = frappe.get_doc("Platform Product", product_id)
	locations = get_sell_locations()

	stock_by_loc = frappe.get_all(
		"Product Stock",
		filters={"product": product_id},
		fields=["location_id", "location_name", "available_qty"],
	)

	return {
		"product": {
			"name": product.name,
			"title": product.product_title,
			"variant_title": product.variant_title,
			"sku": product.sku,
			"barcode": getattr(product, "barcode", "") or "",
			"uom": getattr(product, "uom", "Nos") or "Nos",
			"image": getattr(product, "image", None),
			"collection": getattr(product, "collection", None),
			"price": flt(product.price),
			"compare_at_price": flt(getattr(product, "compare_at_price", 0.0)),
			"available_qty": flt(product.available_qty),
			"track_inventory": bool(product.track_inventory),
			"status": product.status,
		},
		"locations": locations,
		"location_stock": stock_by_loc,
	}


@frappe.whitelist(methods=["GET"])
def get_product_sync_status() -> dict:
	"""
	Returns the latest product sync execution status and details for managers/admins.
	"""
	_check_access()
	frappe.only_for(["System Manager", "Sales Manager"])

	from frappe.utils.background_jobs import is_job_enqueued
	from field_sales_woocommerce.sync.products import JOB_ID

	is_running = False
	try:
		is_running = bool(is_job_enqueued(JOB_ID))
	except Exception:
		pass

	log = frappe.db.get_value(
		"Sync Log",
		{"operation": "Product Sync"},
		["name", "status", "creation", "duration_ms", "error_message", "response_payload"],
		order_by="creation desc",
		as_dict=True,
	)

	summary = {}
	if log and log.get("response_payload"):
		try:
			summary = json.loads(log["response_payload"])
		except Exception:
			pass

	return {
		"is_running": is_running,
		"last_sync": {
			"name": log.get("name") if log else None,
			"status": log.get("status") if log else None,
			"timestamp": str(log.get("creation")) if log else None,
			"duration_ms": log.get("duration_ms") if log else 0,
			"error_message": log.get("error_message") if log else None,
			"summary": summary,
		} if log else None,
	}


# ──────────────────────────────────────────────────────────────────────────────
# 9. Separate Count Query API
# ──────────────────────────────────────────────────────────────────────────────


ALLOWED_COUNT_DOCTYPES = {
	"Field Order",
	"Visit",
	"Retailer",
	"Platform Product",
	"Sales Rep",
	"Commission Statement",
}


# Filter on these as well as the fields the user may read (permlevel rules apply).
COUNT_STANDARD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus"}


def _count_filters(doctype: str, filters: str | dict | None) -> list[list]:
	"""
	``{"field": value}`` equality filters from the client, checked before they reach the query.

	Each key must be a field of ``doctype`` the user may read and each value a plain scalar;
	anything else is a ValidationError (not a database error), so the client gets a clear 417.
	"""
	if filters in (None, "", {}):
		return []
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			frappe.throw(_("Filters must be a JSON object"), frappe.ValidationError)
	if not isinstance(filters, dict):
		frappe.throw(_("Filters must be a JSON object"), frappe.ValidationError)

	allowed = COUNT_STANDARD_FIELDS | set(frappe.get_meta(doctype).get_permitted_fieldnames())
	result = []
	for key, value in filters.items():
		if not isinstance(key, str) or key not in allowed:
			frappe.throw(
				_("Unknown filter field for {0}: {1}").format(
					_(doctype), frappe.utils.escape_html(str(key)[:64])
				),
				frappe.ValidationError,
			)
		if value is not None and not isinstance(value, str | int | float | bool):
			frappe.throw(_("Filter {0} must be a single value").format(key), frappe.ValidationError)
		result.append([key, "=", value])
	return result


@frappe.whitelist(methods=["GET", "POST"])
def get_mobile_count(
	doctype: str,
	preset: str | None = None,
	filters: str | dict | None = None,
) -> dict:
	"""
	Returns the total record count strictly when the UI requires it (e.g. badges, cards).
	Separated entirely from keyset page queries to avoid unneeded COUNT(*) overhead.
	"""
	_check_access()
	user = frappe.session.user

	if doctype not in ALLOWED_COUNT_DOCTYPES:
		frappe.throw(_("Invalid doctype for count query: {0}").format(doctype), frappe.ValidationError)

	filter_list = []
	if preset:
		presets = get_attention_presets() or {}
		cards = get_card_presets() or {}
		p_data = presets.get(preset) or cards.get(preset)
		if p_data and p_data.get("filters"):
			filter_list.extend(p_data["filters"])

	filter_list.extend(_count_filters(doctype, filters))

	# Scoped via frappe.get_list which applies row-level hierarchy permissions
	rows = frappe.get_list(
		doctype,
		filters=filter_list if filter_list else None,
		fields=[{"COUNT": "*", "as": "count"}],
	)
	count = rows[0].count if rows else 0
	return {"doctype": doctype, "count": count}


# ──────────────────────────────────────────────────────────────────────────────
# 10. Server-Side Search with Debounce & Prefix Search
# ──────────────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["GET", "POST"])
@rate_limit(limit=120, seconds=60, user_based=True, ip_based=False)
def mobile_search(
	query: str,
	doctype: str | None = None,
	limit: int = MAX_SEARCH_SUGGESTIONS,
) -> dict:
	"""
	Fast server-side search with:
	- Minimum 2 characters validation.
	- Maximum 20 suggestions.
	- Indexed prefix search on B-tree indexed fields.
	- Permission scoped per DocType.
	"""
	_check_access()
	user = frappe.session.user

	query = (query or "").strip()
	if len(query) < MIN_SEARCH_LENGTH:
		return {"results": [], "query": query}

	limit = min(max(1, cint(limit) or MAX_SEARCH_SUGGESTIONS), MAX_SEARCH_SUGGESTIONS)
	pfx = f"{escape_like(query)}%"
	results = []

	# 1. Customers / Retailers
	if not doctype or doctype == "Retailer":
		r_perm = retailer_query(user)
		if r_perm != "1=0":
			where = ["(`retailer_name` LIKE %(pfx)s OR `name` LIKE %(pfx)s OR `phone` LIKE %(pfx)s OR `city` LIKE %(pfx)s)"]
			params = {"pfx": pfx}
			if r_perm:
				where.append(r_perm)
			rows = frappe.db.sql(
				f"""
				SELECT name, retailer_name as title, city as subtitle, status as badge, 'Retailer' as doctype
				FROM `tabRetailer`
				WHERE {' AND '.join(f'({w})' for w in where)}
				ORDER BY modified DESC
				LIMIT %(limit)s
				""",
				{**params, "limit": limit},
				as_dict=True,
			)
			results.extend(rows)

	# 2. Orders
	if (not doctype or doctype == "Field Order") and len(results) < limit:
		o_perm = field_order_query(user)
		if o_perm != "1=0":
			where = ["(`name` LIKE %(pfx)s OR `retailer_name` LIKE %(pfx)s)"]
			params = {"pfx": pfx}
			if o_perm:
				where.append(o_perm)
			rows = frappe.db.sql(
				f"""
				SELECT name, name as title, retailer_name as subtitle, sync_status as badge, 'Field Order' as doctype
				FROM `tabField Order`
				WHERE {' AND '.join(f'({w})' for w in where)}
				ORDER BY modified DESC
				LIMIT %(limit)s
				""",
				{**params, "limit": limit - len(results)},
				as_dict=True,
			)
			results.extend(rows)

	# 3. Platform Products
	if (not doctype or doctype == "Platform Product") and len(results) < limit and not is_retailer_user(user):
		rows = frappe.db.sql(
			"""
			SELECT name, product_title as title, sku as subtitle, status as badge, 'Platform Product' as doctype
			FROM `tabPlatform Product`
			WHERE (`product_title` LIKE %(pfx)s OR `name` LIKE %(pfx)s OR `sku` LIKE %(pfx)s OR `barcode` LIKE %(pfx)s)
			ORDER BY modified DESC
			LIMIT %(limit)s
			""",
			{"pfx": pfx, "limit": limit - len(results)},
			as_dict=True,
		)
		results.extend(rows)

	# 4. Sales Reps
	if (not doctype or doctype == "Sales Rep") and len(results) < limit and not is_retailer_user(user):
		rep_perm = sales_rep_query(user)
		if rep_perm != "1=0":
			where = ["(`full_name` LIKE %(pfx)s OR `name` LIKE %(pfx)s)"]
			params = {"pfx": pfx}
			if rep_perm:
				where.append(rep_perm)
			rows = frappe.db.sql(
				f"""
				SELECT name, full_name as title, territory as subtitle, IF(active = 1, 'Active', 'Inactive') as badge, 'Sales Rep' as doctype
				FROM `tabSales Rep`
				WHERE {' AND '.join(f'({w})' for w in where)}
				ORDER BY full_name ASC
				LIMIT %(limit)s
				""",
				{**params, "limit": limit - len(results)},
				as_dict=True,
			)
			results.extend(rows)

	return {"results": results[:limit], "query": query}


# ──────────────────────────────────────────────────────────────────────────────
# 11. Duplicate-safe order creation
# ──────────────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["GET", "POST"])
def find_order_by_client_temp_id(client_temp_id: str) -> dict | None:
	"""
	The Field Order the app created with this ``client_temp_id`` (Field Order.client_temp_id),
	so after a timeout it can tell whether the order was saved before creating it again.

	Scoped by ``frappe.get_list``: an order the user may not read is not found. Cancelled
	orders are left out (a cancelled order may be taken again).

	:return: ``{"name", "docstatus"}``, or ``None`` when there is no such order.
	"""
	_check_access()
	client_temp_id = (client_temp_id or "").strip()
	if not client_temp_id or len(client_temp_id) > 140:
		frappe.throw(_("Client Temp ID is required"), frappe.ValidationError)
	rows = frappe.get_list(
		"Field Order",
		filters={"client_temp_id": client_temp_id, "docstatus": ("!=", 2)},
		fields=["name", "docstatus"],
		order_by="creation asc",
		limit=1,
	)
	return {"name": rows[0].name, "docstatus": rows[0].docstatus} if rows else None


@frappe.whitelist(methods=["GET", "POST"])
def find_visit_by_client_temp_id(client_temp_id: str) -> dict | None:
	"""
	The Visit the app created with this ``client_temp_id`` (Visit.client_temp_id), so after a
	timeout it can tell whether the visit was saved before creating it again.

	Scoped by ``frappe.get_list``: a visit the user may not read is not found. Cancelled
	visits are left out (a cancelled visit may be planned again).

	:return: ``{"name", "status"}``, or ``None`` when there is no such visit.
	"""
	_check_access()
	client_temp_id = (client_temp_id or "").strip()
	if not client_temp_id or len(client_temp_id) > 140:
		frappe.throw(_("Client Temp ID is required"), frappe.ValidationError)
	rows = frappe.get_list(
		"Visit",
		filters={"client_temp_id": client_temp_id, "status": ("!=", "Cancelled")},
		fields=["name", "status"],
		order_by="creation asc",
		limit=1,
	)
	return {"name": rows[0].name, "status": rows[0].status} if rows else None


# ──────────────────────────────────────────────────────────────────────────────
# 12. Mobile manifest (edition discovery)
# ──────────────────────────────────────────────────────────────────────────────

# The connector this app ships with. Each edition (field_sales, field_sales_woocommerce,
# field_sales_odoo) answers the same mobile contract under its own namespace.
MOBILE_EDITION = "woocommerce"

# Bumped when a response shape or method the app relies on changes incompatibly.
MOBILE_CONTRACT_VERSION = 1

# What the app may offer against this edition. Every edition serves the shared contract;
# the extras are connector specific.
MOBILE_CAPABILITIES = (
	"orders",
	"visits",
	"customers",
	"products",
	"live_stock",
	"dashboard",
	"commission",
	"razorpay_payments",
	"visit_import",
	"retailer_logins",
	"push_notifications",
)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=60, seconds=60)
def get_mobile_manifest() -> dict:
	"""
	Tells the mobile app which Field Sales edition serves this site, before sign-in.

	``active`` is whether this app owns the Field Sales data (the Field Order DocType):
	a site that has more than one edition installed answers from each, and only the
	owner's methods act on the data. Nothing about users, settings or the store is returned.
	"""
	module = frappe.db.get_value("DocType", "Field Order", "module")
	owner = frappe.local.module_app.get(frappe.scrub(module)) if module else None
	return {
		"edition": MOBILE_EDITION,
		"contract_version": MOBILE_CONTRACT_VERSION,
		"active": owner == __name__.split(".", 1)[0],
		"capabilities": list(MOBILE_CAPABILITIES),
	}


# ──────────────────────────────────────────────────────────────────────────────
# 13. Push notifications (device registration)
# ──────────────────────────────────────────────────────────────────────────────


def _require_staff_user() -> str:
	user = frappe.session.user
	if user == "Guest" or is_retailer_user():
		frappe.throw(_("Push notifications are for Agamsale staff."), frappe.PermissionError)
	return user


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def register_device(token: str, platform: str, app_version: str | None = None) -> dict:
	"""
	Save this phone's Firebase token so the signed-in user's notifications reach it.
	Called after sign-in and whenever Firebase gives the app a new token.

	:return: ``{"enabled": bool, "push_configured": bool}``: whether this device gets pushes,
		and whether the site has push switched on at all.
	"""
	from field_sales_woocommerce.push import devices

	from field_sales_woocommerce.push.fcm import push_enabled

	result = devices.register(_require_staff_user(), token, platform, app_version)
	return {**result, "push_configured": push_enabled()}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def unregister_device(token: str) -> dict:
	"""Forget this phone (on sign-out). :return: ``{"removed": bool}``"""
	from field_sales_woocommerce.push import devices

	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Not signed in."), frappe.PermissionError)
	return {"removed": devices.unregister(user, token)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def set_device_push_enabled(token: str, enabled: bool | int | str) -> dict:
	"""Turn pushes to this phone on or off (Profile switch). :return: ``{"enabled": bool}``"""
	from field_sales_woocommerce.push import devices

	return {"enabled": devices.set_enabled(_require_staff_user(), token, str(enabled).strip().lower() in ("1", "true", "yes", "on"))}
