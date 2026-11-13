# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Single sign-on for the app inside wp-admin (the Agamsale WordPress plugin, wordpress-plugin/).

The plugin shows the app in an iframe on another site, where browsers block or partition
Frappe's cookies, so the iframe does not sign in with a cookie. Instead:

1. The plugin signs a short-lived HS256 token for the logged-in WordPress user, with the
   secret shared with Store Connection's ``wp_shared_secret``
   (claims: ``iss`` = the WordPress site URL, ``aud`` = ``agamsale-wordpress``, ``email``, ``exp``).
2. The app (frontend/src/wordpress/embed.js) asks the plugin for a token over ``postMessage``
   and sends it as ``Authorization: Bearer`` with every request.
3. ``authenticate`` (an ``auth_hooks`` entry) verifies it and signs the request in as the
   enabled Agamsale user with the same email.

A browser that does keep the cookie (the app on a subdomain of the store) signs in with it as
usual: the token only applies to requests with no signed-in user.
"""

from __future__ import annotations

from urllib.parse import urlparse

import frappe
import jwt
from frappe import _

AUDIENCE = "agamsale-wordpress"
LEEWAY_SECONDS = 30
# The plugin issues 5-minute tokens; refuse ones meant to live much longer.
MAX_LIFETIME_SECONDS = 15 * 60
MIN_SECRET_LENGTH = 32


class InvalidToken(Exception):
	"""Not a valid token from this store's plugin; the message is safe to log."""


def page_context() -> dict | None:
	"""
	``field_sales_embedded_page`` hook: boot values for the app page opened inside wp-admin.

	Applies to a guest request (no cookie sign-in) that the plugin opened (``?wp_embed=1``)
	or that a frame loaded (``Sec-Fetch-Dest: iframe``, e.g. the frame reloaded on a deeper
	path), on a site with the plugin secret set. The page then boots without a login redirect
	and signs its API calls with the plugin's tokens.
	"""
	if frappe.session.user not in (None, "", "Guest"):
		return None
	opened_by_plugin = frappe.form_dict.get("wp_embed") == "1"
	in_frame = (frappe.get_request_header("Sec-Fetch-Dest") or "").lower() == "iframe"
	if not (opened_by_plugin or in_frame) or not shared_secret():
		return None
	return {"wp_embedded": True}


def authenticate():
	"""
	``auth_hooks`` entry: sign in a request carrying a token from the WordPress plugin.

	Does nothing when someone is already signed in or the request has no such token. A bad or
	expired token is a 401 (the app fetches a new token and retries once); a valid token for
	an email with no Agamsale access is a 403 that says so.
	"""
	if frappe.session.user not in (None, "", "Guest"):
		return
	token = _bearer_token()
	if not token or not _looks_like_plugin_token(token):
		return

	# frappe.set_user() empties frappe.local.form_dict (this request's arguments): restore it.
	form_dict = frappe.local.form_dict
	try:
		claims = decode(token)
	except InvalidToken:
		raise frappe.AuthenticationError
	user = user_for_email(claims["email"])
	frappe.set_user(user)
	frappe.local.form_dict = form_dict
	frappe.local.wordpress_user_id = claims.get("sub")


def decode(token: str) -> dict:
	"""
	Verify a plugin token and return its claims.

	Checks the HS256 signature with ``wp_shared_secret``, ``exp`` / ``iat`` (30 s leeway), a
	lifetime of at most 15 minutes, ``aud``, and that ``iss`` is Store Connection's store.

	:raises InvalidToken:
	"""
	secret = shared_secret()
	if not secret:
		raise InvalidToken("The WordPress plugin secret is not set on Store Connection")
	try:
		claims = jwt.decode(
			token,
			secret,
			algorithms=["HS256"],
			audience=AUDIENCE,
			leeway=LEEWAY_SECONDS,
			options={"require": ["exp", "iat", "iss", "aud", "email"]},
		)
	except jwt.PyJWTError as e:
		raise InvalidToken(f"Invalid WordPress token ({type(e).__name__})") from e

	if int(claims["exp"]) - int(claims["iat"]) > MAX_LIFETIME_SECONDS:
		raise InvalidToken("WordPress token lives too long")
	store_url = frappe.db.get_single_value("Store Connection", "store_url")
	if not same_site_url(str(claims["iss"]), store_url or ""):
		raise InvalidToken("WordPress token is for another store")
	return claims


def user_for_email(email: str) -> str:
	"""The enabled Agamsale user with ``email``; PermissionError (with the email) if none."""
	from field_sales_woocommerce.www.fieldsales import has_app_access

	email = (email or "").strip()
	user = frappe.db.get_value("User", {"email": email, "enabled": 1}, "name") if email else None
	# The built-in accounts are never reached from another system.
	if not user or user in ("Administrator", "Guest") or not has_app_access(user):
		frappe.throw(
			_(
				"Your WordPress account ({0}) has no Agamsale access. Ask an administrator to add "
				"an Agamsale user with this email and a Field Sales role."
			).format(email or _("no email")),
			frappe.PermissionError,
		)
	return user


def shared_secret() -> str:
	"""Store Connection's WordPress plugin secret, or "" when unset or too short to use."""
	from frappe.utils.password import get_decrypted_password

	secret = (
		get_decrypted_password("Store Connection", "Store Connection", "wp_shared_secret", raise_exception=False)
		or ""
	).strip()
	return secret if len(secret) >= MIN_SECRET_LENGTH else ""


def same_site_url(a: str, b: str) -> bool:
	"""
	Whether two site URLs name the same WordPress site: host, port and path, ignoring the
	scheme, a ``www.`` prefix, letter case and a trailing slash (``home_url()`` and the
	saved Store URL often differ only in these).
	"""

	def key(url: str) -> tuple[str, str] | None:
		url = url.strip()
		if not url:
			return None
		parsed = urlparse(url if "://" in url else f"https://{url}")
		host = (parsed.netloc or "").lower().removeprefix("www.")
		return (host, parsed.path.rstrip("/").lower()) if host else None

	ka, kb = key(a), key(b)
	return ka is not None and ka == kb


def _bearer_token() -> str | None:
	scheme, _sep, token = (frappe.get_request_header("Authorization") or "").partition(" ")
	if scheme.lower() != "bearer":
		return None
	return token.strip() or None


def _looks_like_plugin_token(token: str) -> bool:
	"""Cheap check before verifying: a JWT meant for this app (not a Frappe OAuth token)."""
	try:
		claims = jwt.decode(token, options={"verify_signature": False})
	except jwt.PyJWTError:
		return False
	aud = claims.get("aud") if isinstance(claims, dict) else None
	return aud == AUDIENCE or (isinstance(aud, list) and AUDIENCE in aud)
