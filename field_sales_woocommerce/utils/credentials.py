# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
API key / secret of a user, as the mobile app signs in with them (``token api_key:api_secret``).

Frappe's own ``generate_keys`` is System Manager only (``frappe.only_for``), so the app's login
and password endpoints set the keys here, after doing their own checks. The secret is stored
encrypted in __Auth like any Password field; the User column only holds the mask.
"""

import frappe
from frappe.utils.password import set_encrypted_password


def issue_api_secret(user: str) -> tuple[str, str]:
	"""
	A new API secret for ``user`` (and an API key if it has none). Every device signed in with
	the old secret is signed out.

	:return: ``(api_key, api_secret)``
	"""
	api_key = frappe.db.get_value("User", user, "api_key") or frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=15)
	set_encrypted_password("User", user, api_secret, "api_secret")
	frappe.db.set_value(
		"User", user, {"api_key": api_key, "api_secret": "*" * len(api_secret)}, update_modified=False
	)
	return api_key, api_secret


def rotate_api_secret(user: str) -> str | None:
	"""
	Replace the API secret of ``user``, if it has API keys, so app tokens issued before stop
	working (after a password change, "sign out everywhere" or a removed login).

	:return: the new secret, or ``None`` when the user never had keys.
	"""
	if not user or user == "Guest":
		return None
	from field_sales_woocommerce.push.devices import remove_all

	# Phones signed in with the old secret must stop getting this user's notifications too.
	remove_all(user)
	if not frappe.db.get_value("User", user, "api_key"):
		return None
	return issue_api_secret(user)[1]
