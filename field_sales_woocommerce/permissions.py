# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Row-level access by Sales Rep hierarchy.

A user sees records of their own Sales Rep and of every rep reporting to it,
directly or indirectly (Sales Rep.reports_to). System Manager and
Administrator see everything; a user with no Sales Rep sees nothing.

Retailers are the exception: every rep can see every retailer (to plan a
visit or take an order anywhere), but can edit only those of their hierarchy.
Lists of retailers needing the rep's action use ``hierarchy_filters``.

A retailer login (a user holding only the Field Sales Retailer role, linked
by Retailer.user) sees its own retailer and that retailer's orders, read
only, and nothing else.

Wired up in hooks.py through ``permission_query_conditions`` (list views,
reports, link search) and ``has_permission`` (opening / saving one record).
"""

from collections import defaultdict, deque

import frappe

ADMIN_ROLES = {"System Manager"}

# Permission types that only look at a record (any retailer may be seen).
READ_ONLY_PTYPES = {"read", "select", "print", "email", "report", "export"}

# DocType -> field holding the Sales Rep the record belongs to.
REP_FIELD = {
	"Visit": "sales_rep",
	"Field Order": "sales_rep",
	"Retailer": "sales_rep",
	"Commission Entry": "sales_rep",
	"Commission Statement": "sales_rep",
	"Sales Rep": "name",
}

# Roles that work the app (reps and managers). A retailer login holds none of these.
STAFF_ROLES = {"System Manager", "Sales Manager", "Field Sales Rep"}

# The single role of an invited retailer's login (Retailer.user links it back).
RETAILER_ROLE = "Field Sales Retailer"


def is_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(ADMIN_ROLES & set(frappe.get_roles(user)))


def get_own_rep(user: str | None = None) -> str | None:
	"""The Sales Rep linked to this user (Sales Rep.user is unique)."""
	return frappe.db.get_value("Sales Rep", {"user": user or frappe.session.user}, "name")


def has_staff_role(user: str | None = None) -> bool:
	"""Whether the user works the app (reps, managers, Administrator)."""
	user = user or frappe.session.user
	return user == "Administrator" or bool(STAFF_ROLES & set(frappe.get_roles(user)))


def is_retailer_user(user: str | None = None) -> bool:
	"""
	A retailer login: holds the Field Sales Retailer role and no staff role.
	Such a user's access is their own retailer's My Orders view, nothing else.
	"""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	return RETAILER_ROLE in roles and not (STAFF_ROLES & roles)


def get_own_retailer(user: str | None = None) -> str | None:
	"""The Retailer this user logs in for (Retailer.user is unique)."""
	return frappe.db.get_value("Retailer", {"user": user or frappe.session.user}, "name")


def get_allowed_reps(user: str | None = None) -> list[str]:
	"""
	The user's own Sales Rep followed by every rep under it, at all levels.

	Walks reports_to breadth-first with a visited set, so a cycle in the
	hierarchy (A → B → A) cannot loop. Cached for the rest of the request.
	"""
	user = user or frappe.session.user
	cache = getattr(frappe.local, "field_sales_allowed_reps", None)
	if cache is None:
		cache = frappe.local.field_sales_allowed_reps = {}
	if user in cache:
		return cache[user]

	allowed = []
	own_rep = get_own_rep(user)
	if own_rep:
		reports = defaultdict(list)
		# One query for the whole hierarchy; frappe.get_all bypasses these permission hooks.
		for rep in frappe.get_all("Sales Rep", fields=["name", "reports_to"], filters={"reports_to": ("is", "set")}):
			reports[rep.reports_to].append(rep.name)

		seen = {own_rep}
		queue = deque([own_rep])
		while queue:
			rep = queue.popleft()
			allowed.append(rep)
			for child in reports[rep]:
				if child not in seen:
					seen.add(child)
					queue.append(child)

	cache[user] = allowed
	return allowed


def clear_allowed_reps_cache() -> None:
	frappe.local.field_sales_allowed_reps = {}


# ----------------------------------------------------------------------
# permission_query_conditions
# ----------------------------------------------------------------------


def _query_conditions(doctype: str, user: str | None) -> str:
	user = user or frappe.session.user
	if is_admin(user):
		return ""

	reps = get_allowed_reps(user)
	if not reps:
		return "1=0"

	values = ", ".join(frappe.db.escape(rep) for rep in reps)
	return f"`tab{doctype}`.`{REP_FIELD[doctype]}` in ({values})"


def visit_query(user: str | None = None) -> str:
	return _query_conditions("Visit", user)


def sales_rep_query(user: str | None = None) -> str:
	return _query_conditions("Sales Rep", user)


def field_order_query(user: str | None = None) -> str:
	user = user or frappe.session.user
	if not is_admin(user) and is_retailer_user(user):
		# A retailer login lists its own retailer's orders only.
		own = get_own_retailer(user)
		return f"`tabField Order`.`retailer` = {frappe.db.escape(own)}" if own else "1=0"
	return _query_conditions("Field Order", user)


def commission_entry_query(user: str | None = None) -> str:
	return _query_conditions("Commission Entry", user)


def commission_statement_query(user: str | None = None) -> str:
	return _query_conditions("Commission Statement", user)


def retailer_query(user: str | None = None) -> str:
	user = user or frappe.session.user
	if not is_admin(user) and is_retailer_user(user):
		# A retailer login sees its own retailer; the read-anything rule below is for staff.
		own = get_own_retailer(user)
		return f"`tabRetailer`.`name` = {frappe.db.escape(own)}" if own else "1=0"
	# Every retailer is listed; role permissions still decide who may read at all.
	return ""


def hierarchy_filters(doctype: str, user: str | None = None) -> list:
	"""
	Filters limiting a list to the user's hierarchy, for doctypes whose read
	access is wider (Retailer): e.g. "my retailers that failed to sync".
	"""
	user = user or frappe.session.user
	if is_admin(user):
		return []
	return [[REP_FIELD[doctype], "in", get_allowed_reps(user) or [""]]]


# ----------------------------------------------------------------------
# has_permission
#
# Frappe treats any falsy return (including None) as "denied", so every
# path returns an explicit bool. These hooks can only deny; role
# permissions still decide whether the user may read / write at all.
# ----------------------------------------------------------------------


def visit_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def field_order_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not is_admin(user) and is_retailer_user(user):
		return ptype in READ_ONLY_PTYPES and doc.retailer == get_own_retailer(user)
	return _rep_in_allowed(doc.sales_rep, user)


def commission_entry_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def commission_statement_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def retailer_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not is_admin(user) and is_retailer_user(user):
		# A retailer login reads its own retailer and never edits it from My Orders.
		return ptype in READ_ONLY_PTYPES and doc.name == get_own_retailer(user)
	# sales_rep is optional on Retailer. A rep may create one without it;
	# Retailer.before_insert then assigns it to them.
	if ptype == "create" and not doc.sales_rep:
		return is_admin(user) or bool(get_allowed_reps(user))
	if ptype in READ_ONLY_PTYPES:
		return True
	return _rep_in_allowed(doc.sales_rep, user)


def sales_rep_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	# A new Sales Rep has no name yet when create is checked: a manager may
	# add reps under themselves or anyone below them.
	if ptype == "create" and doc.is_new():
		return _rep_in_allowed(doc.reports_to, user)
	return _rep_in_allowed(doc.name, user)


def _rep_in_allowed(rep: str | None, user: str | None) -> bool:
	if is_admin(user):
		return True
	return bool(rep) and rep in get_allowed_reps(user)
