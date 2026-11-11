# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Firebase Cloud Messaging (HTTP v1) client.

Sends with a Firebase service account key: the one uploaded in this site's Mobile Push
Settings, or else the server-wide key file named by ``field_sales_firebase_key`` in
common_site_config.json, so every site on the bench has push without setup. A short-lived
OAuth token is made from the key and cached in Redis. A token Firebase reports as gone is
switched off on its Mobile Device row so it is not tried again.
"""

import json
import os
import time

import frappe
import requests

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
ANDROID_CHANNEL = "field_sales_alerts"
TOKEN_CACHE_TTL = 50 * 60  # Google tokens last an hour
TIMEOUT = 10
MAX_RETRY_WAIT = 10


SERVICE_ACCOUNT_KEYS = ("type", "project_id", "private_key", "client_email")
BENCH_KEY_CONF = "field_sales_firebase_key"
PUSH_DISABLED_CONF = "field_sales_push_disabled"

# {path: (mtime, key)}: the server-wide key file, read again only when it changes.
_bench_keys: dict[str, tuple[float, dict]] = {}


def get_config() -> dict | None:
	"""
	``{"project_id", "service_account", "source"}`` when push is on, else ``None``.

	This site's Mobile Push Settings key wins (``source`` "site"); otherwise the server-wide
	key file (``source`` "bench"). ``field_sales_push_disabled`` in site_config turns push off.
	"""
	if frappe.utils.cint(frappe.conf.get(PUSH_DISABLED_CONF)):
		return None
	return site_config() or bench_config()


def push_enabled() -> bool:
	return bool(get_config())


def is_service_account(info) -> bool:
	return (
		isinstance(info, dict)
		and all(info.get(k) for k in SERVICE_ACCOUNT_KEYS)
		and info["type"] == "service_account"
	)


def site_config() -> dict | None:
	settings = frappe.get_cached_doc("Mobile Push Settings")
	if not settings.enabled or not settings.firebase_project_id:
		return None
	raw = settings.get_password("service_account_json", raise_exception=False)
	if not raw:
		return None
	try:
		info = json.loads(raw)
	except ValueError:
		return None
	return {"project_id": settings.firebase_project_id, "service_account": info, "source": "site"}


def bench_config() -> dict | None:
	"""The key file named by ``field_sales_firebase_key`` (common_site_config.json), if usable."""
	path = frappe.conf.get(BENCH_KEY_CONF)
	if not path:
		return None
	try:
		mtime = os.path.getmtime(path)
	except OSError:
		_bench_key_problem(path, "the file does not exist or cannot be read")
		return None
	cached = _bench_keys.get(path)
	if cached and cached[0] == mtime:
		info = cached[1]
	else:
		try:
			with open(path) as f:
				info = json.load(f)
		except (OSError, ValueError):
			_bench_key_problem(path, "the file is not valid JSON")
			return None
		if not is_service_account(info):
			_bench_key_problem(path, "the file is not a Firebase service account key")
			return None
		_bench_keys[path] = (mtime, info)
	return {"project_id": info["project_id"], "service_account": info, "source": "bench"}


def _bench_key_problem(path: str, problem: str) -> None:
	"""One Error Log entry per site every few hours, not one per notification."""
	key = f"field_sales:bench_firebase_key_problem:{path}"
	if frappe.cache.get_value(key):
		return
	frappe.cache.set_value(key, 1, expires_in_sec=6 * 60 * 60)
	frappe.log_error(
		title="Firebase key file problem",
		message=f"{BENCH_KEY_CONF} = {path}: {problem}. Push notifications are off until it is fixed.",
	)


def _access_token(config: dict) -> str:
	key = f"field_sales:fcm_access_token:{config['project_id']}"
	token = frappe.cache.get_value(key)
	if token:
		return token

	from google.auth.transport.requests import Request
	from google.oauth2 import service_account

	credentials = service_account.Credentials.from_service_account_info(
		config["service_account"], scopes=[FCM_SCOPE]
	)
	credentials.refresh(Request())
	frappe.cache.set_value(key, credentials.token, expires_in_sec=TOKEN_CACHE_TTL)
	return credentials.token


def _post(url: str, payload: dict, access_token: str) -> tuple[int, dict, dict]:
	"""One HTTP call. :return: ``(status, json body, headers)``"""
	response = requests.post(
		url, json=payload, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT
	)
	try:
		body = response.json()
	except ValueError:
		body = {}
	return response.status_code, body, dict(response.headers)


def build_message(token: str, title: str, body: str, data: dict, tag: str | None = None) -> dict:
	android_notification = {"channel_id": ANDROID_CHANNEL}
	if tag:
		android_notification["tag"] = tag
	return {
		"message": {
			"token": token,
			"notification": {"title": title, "body": body},
			# FCM data values must be strings.
			"data": {k: str(v) for k, v in data.items() if v is not None},
			"android": {"priority": "high", "notification": android_notification},
			"apns": {"payload": {"aps": {"sound": "default"}}},
		}
	}


def _token_is_gone(status: int, body: dict) -> bool:
	"""Firebase says the device token no longer exists or is not a token at all."""
	error = body.get("error") or {}
	codes = {d.get("errorCode") for d in error.get("details") or [] if isinstance(d, dict)}
	if status == 404 or "UNREGISTERED" in codes:
		return True
	return status == 400 and "registration token" in (error.get("message") or "").lower()


def send_to_user(user: str, title: str, body: str, data: dict, tag: str | None = None) -> dict:
	"""
	Push to every enabled device of ``user``.

	:return: ``{"sent": n, "failed": n, "disabled": n}``
	"""
	result = {"sent": 0, "failed": 0, "disabled": 0}
	config = get_config()
	if not config:
		return result
	devices = frappe.get_all(
		"Mobile Device", filters={"user": user, "enabled": 1}, fields=["name", "fcm_token"]
	)
	if not devices:
		return result

	url = FCM_URL.format(project=config["project_id"])
	access_token = _access_token(config)
	for device in devices:
		outcome = _send_one(url, access_token, build_message(device.fcm_token, title, body, data, tag))
		if outcome == "gone":
			frappe.db.set_value("Mobile Device", device.name, "enabled", 0, update_modified=False)
			result["disabled"] += 1
		else:
			result[outcome] += 1
	return result


def _send_one(url: str, access_token: str, payload: dict) -> str:
	""":return: ``"sent"``, ``"gone"`` or ``"failed"``"""
	for attempt in (1, 2):
		try:
			status, body, headers = _post(url, payload, access_token)
		except requests.RequestException as e:
			status, body, headers = 0, {"error": {"message": str(e)}}, {}
		if 200 <= status < 300:
			return "sent"
		if _token_is_gone(status, body):
			return "gone"
		if attempt == 1 and (status == 0 or status == 429 or status >= 500):
			retry_after = headers.get("Retry-After") or headers.get("retry-after")
			try:
				wait = min(float(retry_after), MAX_RETRY_WAIT) if retry_after else 2
			except ValueError:
				wait = 2
			time.sleep(wait)
			continue
		break
	frappe.log_error(
		title="Push notification failed",
		message=f"HTTP {status}\n{frappe.as_json(body)}",
		reference_doctype="Mobile Device",
	)
	return "failed"
