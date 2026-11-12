# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Phones registered for push: one Mobile Device row per Firebase token."""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

MAX_DEVICES_PER_USER = 10
PLATFORMS = ("android", "ios")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_:\-.]{20,512}")


def _clean_token(token) -> str:
	token = (token or "").strip() if isinstance(token, str) else ""
	if not TOKEN_PATTERN.fullmatch(token):
		frappe.throw(_("Not a valid device token."), frappe.ValidationError)
	return token


def register(user: str, token: str, platform: str, app_version: str | None = None) -> dict:
	"""
	Save ``token`` for ``user``. A token already saved for someone else (a phone handed to a
	new rep) moves to ``user``. Only the newest ``MAX_DEVICES_PER_USER`` phones are kept.

	:return: ``{"enabled": bool}`` for this device.
	"""
	token = _clean_token(token)
	platform = (platform or "").strip().lower()
	if platform not in PLATFORMS:
		frappe.throw(_("Platform must be one of: {0}").format(", ".join(PLATFORMS)), frappe.ValidationError)
	app_version = (app_version or "")[:40] or None

	name = frappe.db.get_value("Mobile Device", {"fcm_token": token}, "name")
	if name:
		row = frappe.db.get_value("Mobile Device", name, ["user", "enabled"], as_dict=True)
		values = {"user": user, "platform": platform, "app_version": app_version, "last_seen": now_datetime()}
		if row.user != user:
			values["enabled"] = 1
		frappe.db.set_value("Mobile Device", name, values)
		enabled = bool(values.get("enabled", row.enabled))
	else:
		frappe.get_doc(
			{
				"doctype": "Mobile Device",
				"user": user,
				"fcm_token": token,
				"platform": platform,
				"app_version": app_version,
				"enabled": 1,
				"last_seen": now_datetime(),
			}
		).insert(ignore_permissions=True)
		enabled = True

	_prune(user)
	return {"enabled": enabled}


def _prune(user: str) -> None:
	extra = frappe.get_all(
		"Mobile Device",
		filters={"user": user},
		order_by="last_seen desc, creation desc",
		pluck="name",
		offset=MAX_DEVICES_PER_USER,
		limit=100,
	)
	for name in extra:
		frappe.delete_doc("Mobile Device", name, ignore_permissions=True, force=True)


def unregister(user: str, token: str) -> bool:
	""":return: whether a device of ``user`` was removed."""
	name = frappe.db.get_value("Mobile Device", {"fcm_token": _clean_token(token), "user": user}, "name")
	if not name:
		return False
	frappe.delete_doc("Mobile Device", name, ignore_permissions=True, force=True)
	return True


def set_enabled(user: str, token: str, enabled: bool) -> bool:
	""":return: the device's new state."""
	name = frappe.db.get_value("Mobile Device", {"fcm_token": _clean_token(token), "user": user}, "name")
	if not name:
		frappe.throw(_("This device is not registered."), frappe.DoesNotExistError)
	frappe.db.set_value("Mobile Device", name, "enabled", 1 if enabled else 0)
	return bool(enabled)


def remove_all(user: str) -> None:
	"""
	Forget every phone of ``user`` (their app sign-ins were revoked). A phone that is still
	signed in, such as the one that changed the password, registers again with its new keys.
	"""
	frappe.db.delete("Mobile Device", {"user": user})
