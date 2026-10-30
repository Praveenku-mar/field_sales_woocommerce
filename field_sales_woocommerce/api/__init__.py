# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Whitelisted endpoints used by the Store Connection, Retailer, Visit and Field Order forms and the Platform Product view."""


from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.auth import MAX_PASSWORD_SIZE
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.query_builder.functions import Count, Sum
from frappe.rate_limiter import rate_limit
from frappe.sessions import clear_sessions
from frappe.utils import flt, formatdate, get_link_to_form, get_url, getdate, now_datetime, nowdate
from frappe.utils.password import check_password

from field_sales_woocommerce.connectors import ConnectorError, get_connector
from field_sales_woocommerce.field_sales_woocommerce.doctype.visit.visit import get_geofence_radius
from field_sales_woocommerce.permissions import RETAILER_ROLE, STAFF_ROLES, get_allowed_reps, hierarchy_filters, is_admin
from field_sales_woocommerce.sync.customers import push_retailers
from field_sales_woocommerce.sync.notify import MONITOR_ROLES
from field_sales_woocommerce.sync.orders import enqueue_push_order
from field_sales_woocommerce.sync.products import enqueue_sync
from field_sales_woocommerce.sync.stock import fetch_live_stock
from field_sales_woocommerce.sync.stock import get_sell_locations as _get_sell_locations
from field_sales_woocommerce.utils.geo import has_coords, haversine_m, is_valid_coords, point_geojson
from field_sales_woocommerce.utils.limits import SALES_REPS, check_limit

VISIT_MANAGER_ROLES = {"Sales Manager", "System Manager"}
# Roles a user may hold for a manager to reset their password (the rest are automatic roles).
REP_ONLY_ROLES = {"Field Sales Rep", "All", "Guest", "Desk User"}
# May manage a retailer's login (the Login Access card); mirrors Retailer's LOGIN_EDIT_ROLES.
LOGIN_ROLES = {"Sales Manager", "System Manager"}
# A retailer login user holds the retailer role plus Frappe's automatic roles, nothing else.
RETAILER_LOGIN_ROLES = {"Field Sales Retailer", "All", "Guest"}


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:

	frappe.only_for("System Manager")

	conn = get_connector()
	result = conn.test_connection()
	if result.get("ok"):
		# Set up the order fields (Field Order No, Sales Rep) now; a failure is in Sync Log and
		# is retried before the next order.
		result["order_fields_ready"] = conn.ensure_order_metafields()
		result.update(_sync_shop_settings(result))
	status = "Success" if result.get("ok") else f"Failed: {result.get('message')}"
	values = {"last_test_status": status[:140], "last_tested_on": now_datetime()}
	if result.get("ok") and result.get("shop_name"):
		# Kept for the page header pill; left untouched on a failure.
		values["shop_name"] = result["shop_name"][:140]
	frappe.db.set_single_value("Store Connection", values)
	return result


def _sync_shop_settings(result: dict) -> dict:
	"""Match the site to the store's currency and time zone; a failure never fails the test."""
	from field_sales_woocommerce.sync.shop_settings import sync_shop_settings

	try:
		return sync_shop_settings(
			{"currency": result.get("currency"), "timezone": result.get("timezone"), "country": result.get("country")}
		)
	except Exception:
		frappe.log_error(title=_("Store currency and time zone sync failed"))
		return {"changes": [], "mismatch": []}


@frappe.whitelist()
def get_locations() -> list[dict]:

	frappe.only_for("System Manager")

	try:
		return get_connector().get_locations()
	except ConnectorError as e:
		frappe.throw(e.message, title=_("Could not fetch locations"))


@frappe.whitelist()
def get_sell_locations() -> list[dict]:
	"""
	Locations a rep can sell from, default first (for the app's order and filter pickers).

	:return: ``[{"location_id", "location_name", "is_default"}]``
	"""
	frappe.has_permission("Field Order", "read", throw=True)
	return _get_sell_locations()


@frappe.whitelist(methods=["POST"])
def enqueue_product_sync() -> dict:

	frappe.only_for(["System Manager", "Sales Manager"])

	get_connector()
	if not frappe.db.get_single_value("Store Connection", "default_location_id"):
		frappe.throw(_("Set a Default Location on Store Connection before syncing products"))

	if enqueue_sync():
		return {"queued": True, "message": _("Sync started")}
	return {"queued": False, "message": _("A product sync is already running")}


@frappe.whitelist(methods=["POST"])
def create_order(
	retailer: str, sales_rep: str, order_date: str, visit: str | None = None, location_id: str | None = None
) -> dict:
	"""
	Start a draft Field Order from the app: header only, no items yet — they
	are added on the order page. (``items`` is mandatory on the doctype, so
	the insert skips the mandatory check for the empty table.)

	:param location_id: Store location to sell from; the default location when omitted.
	"""
	frappe.has_permission("Field Order", "create", throw=True)
	frappe.has_permission("Retailer", "read", retailer, throw=True)

	doc = frappe.new_doc("Field Order")
	doc.update(
		{
			"retailer": retailer,
			"sales_rep": sales_rep,
			"order_date": order_date,
			"visit": visit or None,
			"location_id": location_id or None,
		}
	)
	doc.flags.ignore_mandatory = True
	doc.insert()
	return doc.as_dict()


@frappe.whitelist()
def get_user_for_sales_rep(email: str) -> dict:
	"""
	Whether ``email`` is already a user (and already a Sales Rep), for the app's New Sales Rep dialog.

	:return: ``{"exists", "full_name", "sales_rep"}``
	"""
	frappe.has_permission("Sales Rep", "create", throw=True)
	email = (email or "").strip().lower()
	user = frappe.db.get_value("User", email, ["name", "full_name"], as_dict=True)
	return {
		"exists": bool(user),
		"full_name": user.full_name if user else None,
		"sales_rep": frappe.db.exists("Sales Rep", email) if user else None,
	}


@frappe.whitelist(methods=["POST"])
def create_sales_rep(
	user: str,
	full_name: str | None = None,
	mobile: str | None = None,
	territory: str | None = None,
	reports_to: str | None = None,
	monthly_target: float | None = None,
	max_discount_pct: float | None = None,
	send_welcome_email: int = 1,
) -> dict:
	"""
	Create a Sales Rep from the app, creating its User first when ``user`` is a new email.

	A new user gets only the Field Sales Rep role. Sales Managers cannot create users in the
	desk, so that insert skips User permissions; it runs only for someone who may create a
	Sales Rep, and never changes an existing user. Both inserts share the request's
	transaction: if the Sales Rep fails, the user is rolled back too.

	:return: ``{"name", "user_created"}``
	"""
	frappe.has_permission("Sales Rep", "create", throw=True)
	check_limit(SALES_REPS)

	user = (user or "").strip().lower()
	frappe.utils.validate_email_address(user, throw=True)
	if frappe.db.exists("Sales Rep", user):
		frappe.throw(_("{0} is already a Sales Rep").format(user), frappe.DuplicateEntryError)

	# new_doc, not get_doc: the hierarchy hook below must see a new document.
	rep = frappe.new_doc("Sales Rep")
	rep.update(
		{
			"user": user,
			"full_name": (full_name or "").strip() or None,
			"mobile": mobile or None,
			"territory": territory or None,
			"reports_to": reports_to or None,
			"monthly_target": flt(monthly_target) if monthly_target not in (None, "") else None,
			"max_discount_pct": flt(max_discount_pct) if max_discount_pct not in (None, "") else None,
			"active": 1,
		}
	)
	# The hierarchy rule (a manager adds reps under themselves or their team) is checked
	# before any user is created.
	if not frappe.has_permission("Sales Rep", "create", doc=rep):
		frappe.throw(
			_("Set Reports To: you can add reps only under yourself or someone in your team"),
			frappe.PermissionError,
		)

	user_created = False
	if not frappe.db.exists("User", user):
		if not (full_name or "").strip():
			frappe.throw(_("Full Name is required to create the user {0}").format(user))
		first_name, _sep, last_name = full_name.strip().partition(" ")
		new_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": first_name,
				"last_name": last_name.strip() or None,
				"mobile_no": mobile or None,
				"user_type": "System User",
				"send_welcome_email": frappe.utils.cint(send_welcome_email),
				"roles": [{"role": "Field Sales Rep"}],
			}
		)
		new_user.insert(ignore_permissions=True)
		user_created = True

	rep.insert()
	return {"name": rep.name, "user_created": user_created}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60, user_based=True, ip_based=False)
def set_sales_rep_password(
	sales_rep: str, new_password: str, logout_all_sessions: int = 1, old_password: str | None = None
) -> None:
	"""
	Set a new password for a Sales Rep's user, from the app's Sales Rep page.

	- Your own (any rep): needs ``old_password``. Signing out keeps this session.
	- Someone else's: System Managers only, for when email is down. Managers send the
	  rep a reset link instead (``send_sales_rep_password_reset``).

	Nobody may reset Administrator's here. The password policy still applies (User.validate).
	Rate limited per user, since a wrong ``old_password`` is a password guess.
	"""
	rep = frappe.get_doc("Sales Rep", sales_rep)
	user = rep.user

	if user == frappe.session.user:
		rep.check_permission("read")
		try:
			check_password(user, old_password or "", delete_tracker_cache=False)
		except frappe.AuthenticationError:
			frappe.throw(_("Current password is incorrect"), frappe.AuthenticationError)
		if old_password == new_password:
			frappe.throw(_("The new password must be different from the current one"))
	else:
		rep.check_permission("write")
		if user in ("Administrator", "Guest"):
			frappe.throw(_("The password of {0} cannot be changed here").format(user), frappe.PermissionError)
		if not is_admin():
			frappe.throw(_("Send the rep a reset link instead"), frappe.PermissionError)
		_check_password_target(user)

	if not new_password or len(new_password) > MAX_PASSWORD_SIZE:
		frappe.throw(_("Enter a valid new password"))

	user_doc = frappe.get_doc("User", user)
	user_doc.new_password = new_password
	user_doc.logout_all_sessions = frappe.utils.cint(logout_all_sessions)
	# Reps and Sales Managers cannot write User in the desk; the checks above stand in for that.
	user_doc.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60, user_based=True, ip_based=False)
def send_sales_rep_password_reset(sales_rep: str, logout_all_sessions: int = 0) -> dict:
	"""
	Email a Sales Rep's user a one-time password reset link (Frappe's own
	``/update-password`` flow), from the app's Sales Rep page.

	Whoever may edit that Sales Rep (managers above it in Reports To, System Managers),
	for users who pass ``_check_password_target``. Not for your own account: use
	Change Password. The link itself is never returned.

	:param logout_all_sessions: Also sign the rep out of every device now.
	:return: ``{"email": user}``
	"""
	rep = frappe.get_doc("Sales Rep", sales_rep)
	user = rep.user
	if not user:
		frappe.throw(_("Sales Rep {0} has no user").format(sales_rep))
	if user == frappe.session.user:
		frappe.throw(_("Use Change Password for your own account"))

	rep.check_permission("write")
	if user in ("Administrator", "Guest"):
		frappe.throw(_("The password of {0} cannot be changed here").format(user), frappe.PermissionError)
	_check_password_target(user)

	user_doc = frappe.get_doc("User", user)
	if not user_doc.enabled:
		frappe.throw(_("User {0} is disabled. Enable the user before sending a reset link.").format(user))
	# The account Frappe will send through: enabled *and* default outgoing, or site config's mail_server.
	if not EmailAccount.find_default_outgoing():
		frappe.throw(_("Outgoing email is not set up. Add one under Settings > Outgoing Email."))

	# Stores a hashed one-time key on the User and emails the link; the link is not kept here.
	user_doc._reset_password(send_email=True)
	if frappe.utils.cint(logout_all_sessions):
		clear_sessions(user=user, force=True)
	return {"email": user}


def _check_password_target(user: str) -> None:
	"""
	Whose password a manager may reset (directly or by link). A Sales Rep can be linked to
	any existing user, so check the user's roles, not the rep: outside a System Manager,
	only plain reps' passwords may be reset here.
	"""
	if set(frappe.get_roles(user)) - REP_ONLY_ROLES and not is_admin():
		frappe.throw(
			_("Only a System Manager can change the password of a user with roles beyond Field Sales Rep"),
			frappe.PermissionError,
		)


# ------------------------------------------------------------------ retailer logins


def _check_login_manager() -> None:
	"""The Login Access card is for managers; the Retailer doctype re-checks on save."""
	if not LOGIN_ROLES & set(frappe.get_roles()) and frappe.session.user != "Administrator":
		frappe.throw(_("Only a Sales Manager can manage a retailer's login"), frappe.PermissionError)


@frappe.whitelist()
def get_retailer_login(retailer: str) -> dict:
	"""
	The login state for the Login Access card. Managers only (they cannot read User
	rows in the desk, so this is the one place the enabled flag comes from).

	:return: ``{"user", "enabled"}`` with ``user=None`` when the retailer has no login.
	"""
	_check_login_manager()
	user = frappe.db.get_value("Retailer", retailer, "user")
	if not user:
		return {"user": None, "enabled": None}
	return {"user": user, "enabled": bool(frappe.db.get_value("User", user, "enabled"))}


def _check_retailer_login_user(user: str) -> None:
	"""Only a retailer login may be managed here: never staff, never roles beyond the one."""
	if user in ("Administrator", "Guest"):
		frappe.throw(_("The account {0} cannot be a retailer login").format(user), frappe.PermissionError)
	roles = set(frappe.get_roles(user))
	if STAFF_ROLES & roles:
		frappe.throw(_("{0} works in Field Sales and cannot be a retailer login").format(user))
	if roles - RETAILER_LOGIN_ROLES:
		frappe.throw(
			_("{0} has roles beyond Field Sales Retailer and cannot be a retailer login").format(user),
			frappe.PermissionError,
		)


@frappe.whitelist(methods=["POST"])
def create_retailer_login(retailer: str, email: str, send_invite: int = 1) -> dict:
	"""
	Invite a retailer into the app's My Orders view, from its page's Login Access card.

	Creates a Website User for ``email`` (or adopts an existing user who is no Field Sales
	staff), gives it the Field Sales Retailer role and links it as Retailer.user. The invite
	is one branded welcome email ("Welcome to <store>", template ``retailer_invite``) with
	Frappe's one-time set-password link in it — not Frappe's own "Welcome to Frappe" mail.
	It needs an outgoing email account, so it cannot silently never arrive.

	:return: ``{"user", "user_created"}``
	"""
	_check_login_manager()
	doc = frappe.get_doc("Retailer", retailer)
	doc.check_permission("write")
	if doc.user:
		frappe.throw(
			_("Customer {0} already has a login ({1})").format(retailer, doc.user), frappe.DuplicateEntryError
		)

	email = (email or "").strip().lower()
	frappe.utils.validate_email_address(email, throw=True)
	if frappe.db.exists("Sales Rep", email):
		frappe.throw(_("{0} is a Sales Rep and cannot be a retailer login").format(email))
	if frappe.db.exists("Retailer", {"user": email}):
		frappe.throw(_("{0} is already another customer's login").format(email), frappe.DuplicateEntryError)

	send_invite = frappe.utils.cint(send_invite)
	if send_invite and not EmailAccount.find_default_outgoing():
		frappe.throw(_("Outgoing email is not set up. Add one under Settings > Outgoing Email."))

	if frappe.db.exists("User", email):
		_check_retailer_login_user(email)
		user_doc = frappe.get_doc("User", email)
		changed = False
		if not user_doc.enabled:
			# A login removed earlier, invited again: bring the user back.
			user_doc.enabled = 1
			changed = True
		if RETAILER_ROLE not in {row.role for row in user_doc.roles}:
			user_doc.append("roles", {"role": RETAILER_ROLE})
			changed = True
		if changed:
			# Managers cannot write User in the desk; the checks above stand in for that.
			user_doc.save(ignore_permissions=True)
		user_created = False
	else:
		full_name = (doc.owner_name or doc.retailer_name or "").strip()
		first_name, _sep, last_name = full_name.partition(" ")
		new_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name.strip() or email,
				"last_name": last_name.strip() or None,
				"mobile_no": doc.phone or None,
				"user_type": "Website User",
				# The invite below is this login's email; Frappe's own welcome mail is not sent.
				"send_welcome_email": 0,
				"roles": [{"role": RETAILER_ROLE}],
			}
		)
		new_user.insert(ignore_permissions=True)
		user_created = True

	# Through the document, so Retailer's own checks run (one login per user, no staff
	# users, email backfill) and a store push is queued when the store takes the email.
	doc.user = email
	doc.save()
	if send_invite:
		_send_retailer_invite(email, retailer)
	return {"user": email, "user_created": user_created}


def _store_display_name() -> str:
	"""The customer-facing store name: the invoice company, the shop's own name, its domain, else the site."""
	values = frappe.db.get_value(
		"Store Connection", "Store Connection", ["company_name", "shop_name", "store_url"], as_dict=True
	) or {}
	if values.company_name:
		return values.company_name
	if values.shop_name:
		return values.shop_name
	if values.store_url:
		domain = urlsplit(values.store_url).netloc
		if domain:
			return domain.split("@")[-1].split(":")[0]
	return frappe.db.get_default("site_name") or frappe.local.site


def _send_retailer_invite(user: str, retailer: str) -> None:
	"""
	The retailer invite: "Welcome to <store>" with Frappe's one-time set-password link
	(`/update-password?key=…`, stored hashed on the User) so nothing but the greeting
	differs from Frappe's own mail. The link is never kept here.
	"""
	link = frappe.get_doc("User", user)._reset_password(send_email=False)
	store = _store_display_name()
	frappe.sendmail(
		recipients=[user],
		subject=_("Welcome to {0}").format(store),
		template="retailer_invite",
		args={
			"customer": frappe.db.get_value("Retailer", retailer, "retailer_name") or user,
			"store_name": store,
			"user": user,
			"link": link,
			"login_url": get_url("/fieldsales/login"),
		},
	)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60, user_based=True, ip_based=False)
def send_retailer_password_reset(retailer: str, logout_all_sessions: int = 0) -> dict:
	"""
	Email a retailer login a one-time password reset link (Frappe's own
	``/update-password`` flow), from the Login Access card.

	Managers only, for users who pass ``_check_retailer_login_user``. The link itself
	is never returned.

	:param logout_all_sessions: Also sign the retailer out of every device now.
	:return: ``{"email": user}``
	"""
	_check_login_manager()
	user = frappe.db.get_value("Retailer", retailer, "user")
	if not user:
		frappe.throw(_("Customer {0} has no login").format(retailer))
	_check_retailer_login_user(user)

	user_doc = frappe.get_doc("User", user)
	if not user_doc.enabled:
		frappe.throw(_("User {0} is disabled. Remove the login and invite again.").format(user))
	# The account Frappe will send through: enabled *and* default outgoing, or site config's mail_server.
	if not EmailAccount.find_default_outgoing():
		frappe.throw(_("Outgoing email is not set up. Add one under Settings > Outgoing Email."))

	# Stores a hashed one-time key on the User and emails the link; the link is not kept here.
	user_doc._reset_password(send_email=True)
	if frappe.utils.cint(logout_all_sessions):
		clear_sessions(user=user, force=True)
	return {"email": user}


@frappe.whitelist(methods=["POST"])
def revoke_retailer_login(retailer: str) -> dict:
	"""
	Remove a retailer's login: disable its user (signed out everywhere) and clear
	Retailer.user. The retailer record and its history stay untouched; inviting the
	same email later adopts the user again and re-enables it.

	:return: ``{"disabled": user}``
	"""
	_check_login_manager()
	doc = frappe.get_doc("Retailer", retailer)
	doc.check_permission("write")
	user = doc.user
	if not user:
		frappe.throw(_("Customer {0} has no login").format(retailer))
	_check_retailer_login_user(user)

	user_doc = frappe.get_doc("User", user)
	user_doc.enabled = 0
	# Managers cannot write User in the desk; the checks above stand in for that.
	user_doc.save(ignore_permissions=True)
	clear_sessions(user=user, force=True)
	doc.user = None
	doc.save()
	return {"disabled": user}


@frappe.whitelist(methods=["POST"])
def sync_retailers(names: str | list) -> dict:

	names = frappe.parse_json(names) if isinstance(names, str) else names
	if not names:
		frappe.throw(_("Select at least one Retailer"))

	for name in names:
		frappe.has_permission("Retailer", "write", name, throw=True)

	get_connector()  # fails fast if Store Connection is disabled
	push_retailers(names)
	return {"message": _("Sync to store queued for {0} retailer(s)").format(len(names))}


def _check_visit_date(doc) -> None:
	"""A visit can only be checked in to on its own date (in the system time zone)."""
	visit_date, today = getdate(doc.visit_date), getdate(nowdate())
	if visit_date > today:
		frappe.throw(
			_("This visit is planned for {0}. You can check in on that day.").format(formatdate(visit_date)),
			title=_("Not Yet"),
		)
	if visit_date < today:
		frappe.throw(
			_("This visit was planned for {0} and that day has passed. Ask your manager to move it to today.").format(
				formatdate(visit_date)
			),
			title=_("Visit Date Has Passed"),
		)


@frappe.whitelist(methods=["POST"])
def check_in(visit: str, latitude: float, longitude: float, accuracy: float | None = None) -> dict:

	latitude, longitude, accuracy = _parse_position(latitude, longitude, accuracy)

	doc = frappe.get_doc("Visit", visit, for_update=True)
	_check_own_visit(doc)
	if doc.status != "Planned":
		frappe.throw(_("Only a Planned visit can be checked in to. This visit is {0}.").format(_(doc.status)))
	_check_visit_date(doc)

	# Lock the rep so two tabs / devices cannot both open a visit at once.
	frappe.db.get_value("Sales Rep", doc.sales_rep, "name", for_update=True)
	open_visit = frappe.db.get_value(
		"Visit",
		{"sales_rep": doc.sales_rep, "status": "In Progress", "name": ("!=", doc.name)},
		"name",
	)
	if open_visit:
		frappe.throw(
			_("Check out of {0} before checking in to another visit.").format(
				get_link_to_form("Visit", open_visit)
			),
			title=_("Visit Already In Progress"),
		)

	retailer = frappe.db.get_value(
		"Retailer", doc.retailer, ["latitude", "longitude"], as_dict=True, for_update=True
	)
	retailer_location_set = not has_coords(retailer.latitude, retailer.longitude)
	if retailer_location_set:
		# First visit to a shop with no location on file: this check-in becomes the shop's location.
		frappe.db.set_value(
			"Retailer",
			doc.retailer,
			{
				"latitude": latitude,
				"longitude": longitude,
				"location_map": point_geojson(latitude, longitude),
			},
		)
		retailer.latitude, retailer.longitude = latitude, longitude

	doc.update(
		{
			"check_in_time": now_datetime(),
			"latitude": latitude,
			"longitude": longitude,
			"location_accuracy_m": accuracy,
			"retailer_latitude": retailer.latitude,
			"retailer_longitude": retailer.longitude,
			"status": "In Progress",
		}
	)
	# Visit.validate computes distance_from_retailer_m and within_geofence.
	doc.flags.via_check_in_api = True
	doc.save()

	warning = None
	if not doc.within_geofence:
		warning = _("You are {0} m away from the retailer").format(round(doc.distance_from_retailer_m))

	return {
		"status": doc.status,
		"distance_m": doc.distance_from_retailer_m,
		"within_geofence": doc.within_geofence,
		"warning": warning,
		"retailer_location_set": retailer_location_set,
	}


@frappe.whitelist(methods=["POST"])
def check_out(
	visit: str,
	latitude: float,
	longitude: float,
	accuracy: float | None = None,
	no_order_reason: str | None = None,
	no_order_remarks: str | None = None,
) -> dict:
	"""
	Complete an In Progress visit. When no order was taken on it, the rep gives
	the reason (``no_order_reason``, plus ``no_order_remarks`` for "Other").
	"""
	latitude, longitude, accuracy = _parse_position(latitude, longitude, accuracy)

	doc = frappe.get_doc("Visit", visit, for_update=True)
	_check_visit_access(doc)
	if doc.status != "In Progress":
		frappe.throw(_("Only an In Progress visit can be checked out of. This visit is {0}.").format(_(doc.status)))

	if no_order_reason:
		doc.no_order_reason = no_order_reason
	if no_order_remarks:
		doc.no_order_remarks = no_order_remarks
	doc.check_out_time = now_datetime()
	doc.status = "Completed"
	doc.flags.via_check_in_api = True
	doc.save()

	distance_m = within_geofence = None
	if has_coords(doc.retailer_latitude, doc.retailer_longitude):
		distance_m = round(
			haversine_m(latitude, longitude, flt(doc.retailer_latitude), flt(doc.retailer_longitude)), 2
		)
		within_geofence = int(distance_m <= get_geofence_radius(doc.retailer))

	doc.add_comment(
		"Info",
		_("Checked out at {0}, {1} (±{2} m){3}").format(
			latitude,
			longitude,
			round(accuracy),
			_(", {0} m from the retailer").format(round(distance_m)) if distance_m is not None else "",
		),
	)

	warning = None
	if within_geofence == 0:
		warning = _("You are {0} m away from the retailer").format(round(distance_m))

	return {
		"status": doc.status,
		"time_on_site_min": doc.time_on_site_min,
		"distance_m": distance_m,
		"within_geofence": within_geofence,
		"warning": warning,
	}


@frappe.whitelist(methods=["POST"])
def get_live_stock(products: str | list, location_id: str | None = None) -> dict:
	"""
	Current store stock at a location for products being added to an order in the app.

	Also saved on the Platform Products. Only tracked products are returned;
	untracked ones are always available.

	:param products: Platform Product names (list or JSON list).
	:param location_id: The order's location; the default location when omitted.
	:return: ``{product: available_qty}``
	"""
	frappe.has_permission("Platform Product", "read", throw=True)
	products = frappe.parse_json(products) if isinstance(products, str) else products
	try:
		return fetch_live_stock(products or [], location_id or None)
	except ConnectorError as e:
		frappe.throw(e.message, title=_("Could not fetch live stock"))


@frappe.whitelist(methods=["POST"])
def refresh_stock(field_order: str) -> list[dict]:
	"""
	Fetch live stock for a draft Field Order's items at the order's location and save it.

	Updates each row's available_qty and the location's Product Stock, then saves the
	order so stock_warning is recalculated ("Refresh Stock", and before submit).
	"""
	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Stock can only be refreshed on a draft Field Order"))

	try:
		live = fetch_live_stock([row.product for row in doc.items], doc.location_id)
	except ConnectorError as e:
		frappe.throw(e.message, title=_("Could not fetch live stock"))

	for row in doc.items:
		if row.product in live:
			row.available_qty = live[row.product]

	doc.save()  # validate() recalculates stock_warning
	return [
		{
			"idx": row.idx,
			"product": row.product,
			"product_title": row.product_title,
			"qty": row.qty,
			"available_qty": row.available_qty,
			"stock_warning": row.stock_warning,
		}
		for row in doc.items
	]


@frappe.whitelist(methods=["POST"])
def retry_sync(field_order: str) -> dict:

	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("read")
	if not _is_rep_or_their_manager(doc.sales_rep):
		frappe.throw(_("Only the order's sales rep or their Sales Manager can retry the sync."), frappe.PermissionError)
	if doc.docstatus != 1 or doc.sync_status != "Failed":
		frappe.throw(_("Only a submitted Field Order whose sync failed can be retried"))

	get_connector()  # fails fast if Store Connection is disabled
	enqueue_push_order(doc.name)
	return {"message": _("Sync to store queued")}


@frappe.whitelist()
def get_store_order_url(field_order: str) -> str | None:
	"""Admin URL of the synced order (or its draft) on the store, for "Open in Store"."""
	doc = frappe.get_doc("Field Order", field_order)
	doc.check_permission("read")
	if not (doc.external_order_id or doc.external_draft_id):
		return None
	return get_connector().get_order_url(doc.external_order_id, doc.external_draft_id)



SYNC_FAILURE_LIMIT = 500


@frappe.whitelist()
def get_sync_failures() -> dict:
	"""
	Failed Field Orders and Retailers visible to the user (Sales Rep hierarchy applies).

	:return: ``{"orders": [...], "retailers": [...]}``, most recent attempt first.
	"""
	frappe.only_for(MONITOR_ROLES)

	orders = frappe.get_list(
		"Field Order",
		filters={"docstatus": 1, "sync_status": "Failed"},
		fields=["name", "retailer", "retailer_name", "sales_rep", "last_sync_attempt", "sync_attempts", "sync_error"],
		order_by="last_sync_attempt desc",
		limit_page_length=SYNC_FAILURE_LIMIT,
	)
	retailers = frappe.get_list(
		"Retailer",
		filters=[["sync_status", "=", "Failed"], *hierarchy_filters("Retailer")],
		fields=["name", "retailer_name", "sales_rep", "last_sync_attempt", "sync_attempts", "sync_error"],
		order_by="last_sync_attempt desc",
		limit_page_length=SYNC_FAILURE_LIMIT,
	)

	reps = {r.sales_rep for r in orders + retailers if r.sales_rep}
	rep_names = dict(
		frappe.get_all("Sales Rep", filters={"name": ("in", list(reps))}, fields=["name", "full_name"], as_list=True)
	) if reps else {}
	for row in orders + retailers:
		row.sales_rep_name = rep_names.get(row.sales_rep)

	return {"orders": orders, "retailers": retailers}


@frappe.whitelist(methods=["POST"])
def retry_all_sync_failures() -> dict:
	"""
	Queue a push for every failed Field Order and Retailer the user can see ("Retry All").

	:return: ``{"orders": int, "retailers": int, "message": str}``
	"""
	frappe.only_for(MONITOR_ROLES)
	get_connector()  # fails fast if Store Connection is disabled

	orders = frappe.get_list(
		"Field Order", filters={"docstatus": 1, "sync_status": "Failed"}, pluck="name", limit_page_length=0
	)
	retailers = frappe.get_list(
		"Retailer", filters=[["sync_status", "=", "Failed"], *hierarchy_filters("Retailer")], pluck="name", limit_page_length=0
	)

	for name in orders:
		enqueue_push_order(name)
	push_retailers(retailers)

	return {
		"orders": len(orders),
		"retailers": len(retailers),
		"message": _("Retry queued for {0} order(s) and {1} retailer(s)").format(len(orders), len(retailers)),
	}


@frappe.whitelist()
def get_sync_failure_count(filters: str | list | dict | None = None) -> dict:
	"""Number card "Sync Failures": failed orders + retailers in the user's scope; opens the page."""
	count = len(
		frappe.get_list("Field Order", filters={"docstatus": 1, "sync_status": "Failed"}, pluck="name", limit_page_length=0)
	) + len(frappe.get_list(
		"Retailer", filters=[["sync_status", "=", "Failed"], *hierarchy_filters("Retailer")], pluck="name", limit_page_length=0
	))

	result = {"value": count, "fieldtype": "Int"}
	if set(MONITOR_ROLES) & set(frappe.get_roles()):
		result["route"] = ["sync-failures"]
	return result


@frappe.whitelist()
def get_territories(txt: str | None = None) -> list[str]:
	"""Territories in use on Sales Rep (a free-text field), for the report's Territory filter."""
	filters = {"territory": ("is", "set")}
	if txt:
		filters["territory"] = ("like", f"%{txt}%")
	return frappe.get_list(
		"Sales Rep", filters=filters, pluck="territory", distinct=True, order_by="territory asc", limit_page_length=50
	)


@frappe.whitelist()
def get_retailer_cities(txt: str | None = None) -> list[str]:
	"""Cities in use on Retailer, for the report's City filter."""
	filters = {"city": ("is", "set")}
	if txt:
		filters["city"] = ("like", f"%{txt}%")
	return frappe.get_list(
		"Retailer", filters=filters, pluck="city", distinct=True, order_by="city asc", limit_page_length=50
	)


@frappe.whitelist()
def get_retailer_summary(retailer: str) -> dict:
	"""Last 30 days at a retailer, for the summary bar on its form."""
	frappe.has_permission("Retailer", "read", retailer, throw=True)
	since = frappe.utils.add_days(frappe.utils.nowdate(), -30)

	visit = frappe.qb.DocType("Visit")
	visits = (
		frappe.qb.from_(visit)
		.select(Count("*"))
		.where((visit.retailer == retailer) & (visit.status == "Completed") & (visit.visit_date >= since))
	).run()[0][0]

	order = frappe.qb.DocType("Field Order")
	orders, value = (
		frappe.qb.from_(order)
		.select(Count("*"), Sum(order.grand_total))
		.where((order.retailer == retailer) & (order.docstatus == 1) & (order.order_date >= since))
	).run()[0]

	return {"visits_30d": visits, "orders_30d": orders, "value_30d": flt(value)}


@frappe.whitelist()
def get_visit_orders(visit: str) -> list[dict]:
	"""
	Field Orders taken on a visit, newest first, with how many lines are short
	of stock (shown on the visit as "Out of stock" until the order is fixed).
	"""
	frappe.has_permission("Visit", "read", visit, throw=True)

	orders = frappe.get_list(
		"Field Order",
		filters={"visit": visit},
		fields=["name", "order_date", "docstatus", "sync_status", "total_qty", "grand_total", "sync_error"],
		order_by="creation desc",
	)
	if not orders:
		return []

	item = frappe.qb.DocType("Field Order Item")
	counts = {
		parent: (lines, short)
		for parent, lines, short in (
			frappe.qb.from_(item)
			.select(item.parent, Count("*"), Sum(item.stock_warning))
			.where((item.parenttype == "Field Order") & item.parent.isin([o.name for o in orders]))
			.groupby(item.parent)
		).run()
	}
	for order in orders:
		lines, short = counts.get(order.name, (0, 0))
		order.items = lines
		order.short_items = int(short or 0)
		order.status = _visit_order_status(order)
	return orders


def _visit_order_status(order) -> str:
	if order.docstatus == 2:
		return "Cancelled"
	if order.docstatus == 0:
		return "Out of Stock" if order.short_items else "Draft"
	return order.sync_status or "Pending"


def _parse_position(latitude, longitude, accuracy) -> tuple[float, float, float]:
	"""Validate a browser GPS fix: it comes from the client, so check it is a real point."""
	if latitude in (None, "") or longitude in (None, ""):
		frappe.throw(_("Could not read your location. Allow location access and try again."))

	latitude, longitude = flt(latitude), flt(longitude)
	if not has_coords(latitude, longitude) or not is_valid_coords(latitude, longitude):
		frappe.throw(_("Invalid location: {0}, {1}").format(latitude, longitude))

	return latitude, longitude, max(flt(accuracy), 0)


def _check_own_visit(doc) -> None:
	"""
	Only the visit's assigned rep checks in: the check-in records where that rep is, so
	nobody else (a manager or System Manager included) can do it for them.
	"""
	rep = frappe.db.get_value("Sales Rep", doc.sales_rep, ["user", "full_name"], as_dict=True) or frappe._dict()
	if not rep.user or rep.user != frappe.session.user:
		frappe.throw(
			_("Only {0}, the rep assigned to this visit, can check in.").format(rep.full_name or doc.sales_rep),
			frappe.PermissionError,
		)


def _check_visit_access(doc) -> None:
	"""Only the visit's own sales rep, or a manager over that rep, may check out."""
	if not _is_rep_or_their_manager(doc.sales_rep):
		frappe.throw(
			_("Only the visit's sales rep or their Sales Manager can check out."),
			frappe.PermissionError,
		)


def _is_rep_or_their_manager(sales_rep: str) -> bool:
	"""True for the rep's own user, a Sales Manager over that rep, or an admin."""
	if frappe.session.user == frappe.db.get_value("Sales Rep", sales_rep, "user"):
		return True
	return is_admin() or bool(
		VISIT_MANAGER_ROLES & set(frappe.get_roles()) and sales_rep in get_allowed_reps()
	)
