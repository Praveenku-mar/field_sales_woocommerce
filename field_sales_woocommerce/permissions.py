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
MANAGER_ROLES = {"Sales Manager", "System Manager"}

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


def is_manager_or_admin(user: str | None = None) -> bool:
	"""True for Administrator, System Manager, or Sales Manager."""
	user = user or frappe.session.user
	return user == "Administrator" or bool(MANAGER_ROLES & set(frappe.get_roles(user)))


def get_own_rep(user: str | None = None) -> str | None:
	"""The Sales Rep linked to this user (Sales Rep.user is unique)."""
	return frappe.db.get_value("Sales Rep", {"user": user or frappe.session.user}, "name")


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
		# One query for the whole hierarchy, read without these permission hooks (no recursion).
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


def payment_transaction_query(user: str | None = None) -> str:
	"""
	Payment history of the orders in the user's hierarchy: rows linked by ``reference_order``,
	and store checkout rows that only carry the store order id (``store_order_id``).
	"""
	user = user or frappe.session.user
	if is_admin(user):
		return ""
	reps = get_allowed_reps(user)
	if not reps:
		return "1=0"
	values = ", ".join(frappe.db.escape(rep) for rep in reps)
	orders = f"select `name` from `tabField Order` where `sales_rep` in ({values})"
	store_orders = (
		f"select `external_order_id` from `tabField Order` where `sales_rep` in ({values})"
		" and ifnull(`external_order_id`, '') != ''"
	)
	return (
		f"(`tabPayment Transaction`.`reference_order` in ({orders})"
		" or (ifnull(`tabPayment Transaction`.`reference_order`, '') = ''"
		f" and `tabPayment Transaction`.`store_order_id` in ({store_orders})))"
	)


# Sentinel of scope_criterion: the user may see no row of the doctype.
NO_ROWS = object()


def scope_criterion(doctype: str, table, user: str | None = None):
	"""
	The row rule of the ``*_query`` functions above as a ``frappe.qb`` condition on ``table``,
	for queries written with the query builder (values bound, never formatted into SQL).

	:param doctype: "Visit", "Field Order", "Retailer" or "Sales Rep".
	:param table: ``frappe.qb.DocType(doctype)`` the query reads from.
	:return: ``None`` when every row may be seen, ``NO_ROWS`` when none may, else a condition.
	"""
	user = user or frappe.session.user
	if is_admin(user):
		return None
	if doctype in ("Field Order", "Retailer") and is_retailer_user(user):
		own = get_own_retailer(user)
		if not own:
			return NO_ROWS
		return (table.retailer if doctype == "Field Order" else table.name) == own
	if doctype == "Retailer":
		# Every retailer is listed to staff (retailer_query).
		return None
	reps = get_allowed_reps(user)
	if not reps:
		return NO_ROWS
	return table[REP_FIELD[doctype]].isin(reps)


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


def payment_transaction_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""A payment row belongs to its order's rep (``reference_order``, else the store order id)."""
	user = user or frappe.session.user
	if is_admin(user):
		return True
	order = doc.reference_order
	if not order and doc.store_order_id:
		order = frappe.db.get_value("Field Order", {"external_order_id": doc.store_order_id}, "name")
	if not order:
		return False
	return _rep_in_allowed(frappe.db.get_value("Field Order", order, "sales_rep"), user)


def _rep_in_allowed(rep: str | None, user: str | None) -> bool:
	if is_admin(user):
		return True
	return bool(rep) and rep in get_allowed_reps(user)


# ----------------------------------------------------------------------
# validate: who a saved record belonged to
# ----------------------------------------------------------------------


def check_owner_change(doc, method: str | None = None) -> None:
	"""
	``validate`` hook (hooks.doc_events): a saved record may move to another Sales Rep only
	when the user may already edit it, i.e. its stored rep is in their hierarchy.

	The ``has_permission`` hooks above see the record as sent, with the new rep. REST PUT
	checks the stored record first, but ``frappe.client.save`` and the desk's ``savedocs``
	build the document from the request, so without this check a user could take another
	team's record by saving it with their own rep. Server code that saves with
	``ignore_permissions`` and admins are not limited. A record with no stored rep may still
	be claimed (the ``has_permission`` hooks decide who).
	"""
	if doc.is_new() or doc.flags.ignore_permissions or is_admin():
		return
	field = REP_FIELD.get(doc.doctype)
	if not field or field == "name":
		return
	before = doc.get_doc_before_save()
	stored = before.get(field) if before else None
	if stored and stored != doc.get(field) and stored not in get_allowed_reps():
		frappe.throw(
			frappe._("{0} {1} belongs to another team. Only its own team can assign it to someone else.").format(
				frappe._(doc.doctype), doc.name
			),
			frappe.PermissionError,
		)
