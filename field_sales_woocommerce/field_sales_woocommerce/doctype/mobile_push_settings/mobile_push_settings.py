# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import json
from functools import partial

import frappe
from frappe import _
from frappe.model.document import Document

SERVICE_ACCOUNT_KEYS = ("type", "project_id", "private_key", "client_email")
MAX_KEY_FILE_SIZE = 64 * 1024


class MobilePushSettings(Document):
	def onload(self):
		from field_sales_woocommerce.push.fcm import bench_config

		bench = bench_config()
		if bench:
			self.set_onload("bench_key_project", bench["project_id"])

	def validate(self):
		upload = self._take_upload()
		if upload:
			# The private key must not stay on disk in plain text, whatever happens to this save:
			# on success it is deleted below; if the save is rolled back (a bad file included),
			# the upload is removed afterwards in its own transaction. Deleting it inside a
			# transaction that rolls back would restore the File row without its file.
			frappe.db.after_rollback.add(partial(_discard_upload, upload.name))
			self._read_key_file(upload)
		raw = self.service_account_json or ""
		if raw and set(raw) != {"*"}:  # a new key, not the saved mask
			self._use_key(raw)
		if self.enabled and not (self.service_account_json and self.firebase_project_id):
			frappe.throw(_("Upload the Firebase service account key file before enabling push notifications."))
		if upload:
			upload.delete(ignore_permissions=True)

	def on_update(self):
		frappe.cache.delete_keys("field_sales:fcm_access_token:")

	def _take_upload(self):
		"""The uploaded key file's File doc (or ``None``); the field itself never keeps the URL."""
		file_url, self.service_account_file = self.service_account_file, None
		if not file_url:
			return None
		return frappe.get_doc("File", {"file_url": file_url})

	def _read_key_file(self, file_doc):
		"""Read the uploaded key file into the encrypted field."""
		if (file_doc.file_size or 0) > MAX_KEY_FILE_SIZE:
			frappe.throw(_("That file is too large to be a service account key."))
		content = file_doc.get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8", errors="replace")
		self.service_account_json = content.strip()

	def _use_key(self, raw: str):
		try:
			info = json.loads(raw)
		except ValueError:
			frappe.throw(_("That is not a service account key file (not valid JSON)."))
		if not isinstance(info, dict) or any(not info.get(k) for k in SERVICE_ACCOUNT_KEYS):
			frappe.throw(
				_("That is not a service account key file: it must have {0}. Note that google-services.json is the app's file, not this one.").format(
					", ".join(SERVICE_ACCOUNT_KEYS)
				)
			)
		if info["type"] != "service_account":
			frappe.throw(_("The key must be a service account key."))
		previous = self.firebase_project_id
		self.firebase_project_id = info["project_id"]
		if previous and previous != info["project_id"]:
			frappe.msgprint(
				_("Firebase project changed from {0} to {1}. Phones registered with the old project need to sign in again.").format(
					previous, info["project_id"]
				),
				alert=True,
			)


def _discard_upload(file_name: str) -> None:
	"""After a rolled-back save: remove the uploaded key file (row and file on disk) for good."""
	if frappe.db.exists("File", file_name):
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
		frappe.db.commit()


@frappe.whitelist(methods=["GET"])
def get_push_status() -> dict:
	"""Safe settings summary for the SPA; never return the service-account key."""
	frappe.only_for("System Manager")
	from field_sales_woocommerce.push.fcm import get_config

	config = get_config()
	return {
		"configured": bool(config),
		"source": config["source"] if config else None,
		"project_id": config["project_id"] if config else None,
		"registered_phones": frappe.db.count("Mobile Device", {"enabled": 1}),
	}


@frappe.whitelist(methods=["GET"])
def users_with_phones() -> list[dict]:
	"""Users who have a phone registered for push: ``[{"user", "full_name", "phones"}]``, you first."""
	frappe.only_for("System Manager")
	rows = frappe.get_all(
		"Mobile Device",
		filters={"enabled": 1},
		fields=["user", {"COUNT": "*", "as": "phones"}],
		group_by="user",
	)
	result = [
		{"user": r.user, "full_name": frappe.utils.get_fullname(r.user), "phones": r.phones} for r in rows
	]
	me = frappe.session.user
	return sorted(result, key=lambda r: (r["user"] != me, r["full_name"].lower()))


@frappe.whitelist(methods=["POST"])
def send_test_notification(user: str | None = None) -> dict:
	"""Push a test notification to the phones of ``user`` (default: yourself)."""
	frappe.only_for("System Manager")
	from field_sales_woocommerce.push.fcm import get_config
	from field_sales_woocommerce.push.notify import send_push

	user = user or frappe.session.user
	if not get_config():
		frappe.throw(
			_("Push is off: upload a service account key here, or set field_sales_firebase_key for the whole server.")
		)
	if not frappe.db.exists("Mobile Device", {"user": user, "enabled": 1}):
		frappe.throw(
			_("{0} has no phone registered. Sign in to the Agamsale mobile app as {0} and allow notifications first.").format(
				user
			)
		)
	return send_push(user, "test")
