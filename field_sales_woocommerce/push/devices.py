# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Phones registered for push: one Mobile Device row per Firebase token."""

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

MAX_DEVICES_PER_USER = 10
# A token registered by someone else moves only once its owner has been away this long (or is disabled).
HANDOVER_AFTER = timedelta(days=1)
PLATFORMS = ("android", "ios")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_:\-.]{20,512}")


def _clean_token(token) -> str:
	token = (token or "").strip() if isinstance(token, str) else ""
	if not TOKEN_PATTERN.fullmatch(token):
		frappe.throw(_("Not a valid device token."), frappe.ValidationError)
	return token


def register(user: str, token: str, platform: str, app_version: str | None = None) -> dict:
	"""
	Save ``token`` for ``user``. Only the newest ``MAX_DEVICES_PER_USER`` phones are kept.

	A token already saved for someone else (a phone handed to a new rep) moves to ``user`` only
	when its owner is disabled or has not been seen for ``HANDOVER_AFTER``. While the owner is
	active, pushes to it are switched off and ``handover_pending`` is returned: knowing a token
	must not be enough to receive another user's notifications.

	:return: ``{"enabled": bool}`` for this device, plus ``"handover_pending": True`` when refused.
	"""
	token = _clean_token(token)
	platform = (platform or "").strip().lower()
	if platform not in PLATFORMS:
		frappe.throw(_("Platform must be one of: {0}").format(", ".join(PLATFORMS)), frappe.ValidationError)
	app_version = (app_version or "")[:40] or None

	name = frappe.db.get_value("Mobile Device", {"fcm_token": token}, "name", for_update=True)
	if name:
		row = frappe.db.get_value(
			"Mobile Device", name, ["user", "enabled", "last_seen", "handover_to"], as_dict=True
		)
		if row.user != user and _owner_active(row):
			# Someone else's token while its owner still uses the app: never take it over (a
			# known token would hand them the owner's notifications). Pushes to it stop instead,
			# so the owner's alerts don't show on a phone they may no longer hold; the owner's
			# next registration takes it back, and if they stay away it moves later.
			# handover_to marks that this turned pushes off, so the owner's next registration
			# turns them back on; a phone the owner had switched off is left as it is.
			if row.enabled:
				frappe.db.set_value(
					"Mobile Device", name, {"enabled": 0, "handover_to": user}, update_modified=False
				)
			return {"enabled": False, "handover_pending": True}

		values = {
			"user": user,
			"platform": platform,
			"app_version": app_version,
			"last_seen": now_datetime(),
			"handover_to": None,
		}
		if row.user != user or row.handover_to:
			# Moved to a new user (old owner gone), or the owner re-registered after a
			# handover attempt turned it off: back on.
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


def _owner_active(row) -> bool:
	"""
	Whether the token's current owner still counts as using it: an enabled user seen within
	``HANDOVER_AFTER`` (this phone's last registration, or their last desk/web activity).
	"""
	owner = frappe.db.get_value("User", row.user, ["enabled", "last_active"], as_dict=True)
	if not owner or not owner.enabled:
		return False
	cutoff = now_datetime() - HANDOVER_AFTER
	seen = [get_datetime(t) for t in (row.last_seen, owner.last_active) if t]
	return any(t >= cutoff for t in seen)


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
	frappe.db.set_value("Mobile Device", name, {"enabled": 1 if enabled else 0, "handover_to": None})
	return bool(enabled)


def remove_all(user: str) -> None:
	"""
	Forget every phone of ``user`` (their app sign-ins were revoked). A phone that is still
	signed in, such as the one that changed the password, registers again with its new keys.
	"""
	frappe.db.delete("Mobile Device", {"user": user})
