# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Serves the Field Sales desktop app (Vue SPA) at /fieldsales/* and its alias /fs/* (hooks.website_route_rules)."""

from urllib.parse import quote

import frappe
from frappe import _

from field_sales_woocommerce.permissions import is_retailer_user

no_cache = 1

APP_ROLES = ("System Manager", "Sales Manager", "Field Sales Rep", "Field Sales Retailer")


def get_context(context):
	# Opened inside a store's admin (no Frappe cookie there): the app signs its API calls
	# with the store's session token instead, so no login redirect and no CSRF token.
	embedded = _embedded_boot()
	if embedded:
		context.boot = get_boot(embedded)
		return context

	path = frappe.request.path if frappe.request else ""
	if _is_login_page(path):
		# The app's own login screen: guests and signed-in users alike may load the
		# shell (a signed-in user without an app role can sign in as someone who has one).
		frappe.db.commit()
		context.boot = get_boot()
		return context

	if frappe.session.user == "Guest":
		_redirect(f"{_app_base()}/login?redirect-to={quote(path or '/fieldsales')}")

	if not has_app_access():
		frappe.throw(_("You do not have access to Field Sales"), frappe.PermissionError)

	# get_csrf_token may create the token on this GET request; keep it.
	frappe.db.commit()
	context.boot = get_boot()
	return context


def has_app_access(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(APP_ROLES) & set(frappe.get_roles(user)))


def get_boot(embedded: dict | None = None) -> dict:
	"""
	Values the SPA reads from `window` before it mounts (see frontend/src/main.js).

	:param embedded: Extra values for a page opened inside a store's admin (``embedded``,
		``shop``, ``host``, ``shopify_api_key``); such a page gets no CSRF token.
	"""
	boot = frappe._dict(
		{
			"frappe_version": frappe.__version__,
			"site_name": frappe.local.site,
			"default_route": _app_base(),
			"sysdefaults": frappe.defaults.get_defaults(),
			# Realtime socket (composables/useRealtime.js): its own port under bench start.
			"socketio_port": frappe.conf.socketio_port,
			"dev_server": frappe._dev_server,
		}
	)
	if embedded:
		boot.update(embedded)
	else:
		boot.csrf_token = frappe.sessions.get_csrf_token()
		# A retailer login starts on My Orders; everyone else on the dashboard.
		if is_retailer_user():
			boot.default_route = f"{_app_base()}/my-orders"
	return boot


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_context_for_dev():
	"""
	Boot values for `yarn dev`, where Vite serves index.html instead of this page.

	GET, because the CSRF token is one of the values: a signed-in user's POST
	is refused without the token it is asking for.
	"""
	if not frappe.conf.developer_mode:
		frappe.throw(_("This method is only meant for developer mode"))
	return get_boot()


def _embedded_boot() -> dict | None:
	"""Boot values from the first ``field_sales_embedded_page`` hook that recognises the request."""
	for method in frappe.get_hooks("field_sales_embedded_page"):
		values = frappe.get_attr(method)()
		if values:
			return values
	return None


def _app_base() -> str:
	"""The path the app was opened under: /fs or /fieldsales."""
	path = frappe.request.path if frappe.request else ""
	return "/fs" if path == "/fs" or path.startswith("/fs/") else "/fieldsales"


def _is_login_page(path: str) -> bool:
	"""Whether this request is for the app's own login screen (either base path)."""
	return path.rstrip("/") in ("/fieldsales/login", "/fs/login")


def _redirect(location: str):
	frappe.local.flags.redirect_location = location
	# 302: a permanent redirect would be cached and keep sending users to login.
	redirect = frappe.Redirect()
	redirect.http_status_code = 302
	raise redirect
