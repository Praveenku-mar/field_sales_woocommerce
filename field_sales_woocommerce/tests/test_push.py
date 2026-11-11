# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Push notifications: device registration, the bell → push hook, the FCM client and the push events."""

import importlib
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

APP = __name__.split(".", 1)[0]
devices = importlib.import_module(f"{APP}.push.devices")
events = importlib.import_module(f"{APP}.push.events")
fcm = importlib.import_module(f"{APP}.push.fcm")
notify = importlib.import_module(f"{APP}.push.notify")
credentials = importlib.import_module(f"{APP}.utils.credentials")

FAKE_KEY = {
	"type": "service_account",
	"project_id": "fs-push-test",
	"private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
	"client_email": "push@fs-push-test.iam.gserviceaccount.com",
}
SHOP_NAME = "Push Test Stores"


class TestPush(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.field_sales_role_users = None
		settings = frappe.get_doc("Mobile Push Settings")
		settings.enabled = 1
		settings.firebase_project_id = ""
		settings.service_account_json = json.dumps(FAKE_KEY)
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Mobile Push Settings", "Mobile Push Settings")

		self.manager_user = self.make_user("push.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("push.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep)
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": SHOP_NAME,
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
				"sales_rep": self.rep,
			}
		).insert(ignore_permissions=True)

		self.sent = []
		self.responses = []
		token_patch = patch.object(fcm, "_access_token", return_value="test-access-token")
		post_patch = patch.object(fcm, "_post", side_effect=self.fake_post)
		sleep_patch = patch.object(fcm.time, "sleep")
		# The bench's real server-wide key must not leak in; tests that want one set their own.
		conf_patch = patch.dict(frappe.local.conf, {fcm.BENCH_KEY_CONF: None, fcm.PUSH_DISABLED_CONF: None})
		for p in (token_patch, post_patch, sleep_patch, conf_patch):
			p.start()
			self.addCleanup(p.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()
		frappe.clear_document_cache("Mobile Push Settings", "Mobile Push Settings")
		frappe.cache.delete_keys("field_sales:low_stock_notified:")

	# -- helpers -------------------------------------------------------------

	def fake_post(self, url, payload, access_token):
		self.sent.append(payload["message"])
		return self.responses.pop(0) if self.responses else (200, {"name": "projects/x/messages/1"}, {})

	def make_user(self, email, roles):
		if frappe.db.exists("User", email):
			return email
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		return frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
			ignore_permissions=True
		).name

	def register(self, user, token="fcm-token-rep-0000000000000001"):
		return devices.register(user, token, "android", "1.0.0")

	def sent_to(self, token):
		return [m for m in self.sent if m["token"] == token]

	# -- devices -------------------------------------------------------------

	def test_register_is_an_upsert_and_moves_a_token_to_its_new_user(self):
		token = "fcm-token-shared-phone-000000001"
		self.register(self.rep_user, token)
		self.register(self.rep_user, token)
		self.assertEqual(frappe.db.count("Mobile Device", {"fcm_token": token}), 1)

		devices.set_enabled(self.rep_user, token, False)
		result = self.register(self.manager_user, token)
		row = frappe.db.get_value("Mobile Device", {"fcm_token": token}, ["user", "enabled"], as_dict=True)
		self.assertEqual(row.user, self.manager_user)
		self.assertTrue(row.enabled)
		self.assertTrue(result["enabled"])

	def test_only_the_newest_ten_devices_are_kept(self):
		for i in range(12):
			self.register(self.rep_user, f"fcm-token-many-devices-{i:010d}")
		self.assertEqual(frappe.db.count("Mobile Device", {"user": self.rep_user}), devices.MAX_DEVICES_PER_USER)

	def test_bad_token_or_platform_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			devices.register(self.rep_user, "short", "android")
		with self.assertRaises(frappe.ValidationError):
			devices.register(self.rep_user, "fcm-token-good-length-00000001", "windows")

	def test_revoking_app_sign_ins_forgets_the_users_phones(self):
		self.register(self.rep_user)
		frappe.db.set_value("User", self.rep_user, "api_key", "push-test-key")
		credentials.rotate_api_secret(self.rep_user)
		self.assertFalse(frappe.db.exists("Mobile Device", {"user": self.rep_user}))

	def upload(self, content: str, name: str = "service-account.json") -> str:
		file_doc = frappe.get_doc(
			{"doctype": "File", "file_name": name, "is_private": 1, "content": content}
		).insert(ignore_permissions=True)
		return file_doc.file_url

	def test_uploaded_key_file_is_stored_encrypted_and_deleted(self):
		key = {**FAKE_KEY, "project_id": "fs-push-uploaded"}
		settings = frappe.get_doc("Mobile Push Settings")
		settings.service_account_file = self.upload(json.dumps(key))
		url = settings.service_account_file
		settings.save(ignore_permissions=True)

		settings.reload()
		self.assertEqual(settings.firebase_project_id, "fs-push-uploaded")
		self.assertFalse(settings.service_account_file)
		self.assertEqual(json.loads(settings.get_password("service_account_json")), key)
		self.assertFalse(frappe.db.exists("File", {"file_url": url}))

	def test_google_services_json_is_refused_as_a_key(self):
		app_config = {"project_info": {"project_id": "fs-push-test"}, "client": []}
		settings = frappe.get_doc("Mobile Push Settings")
		settings.service_account_file = self.upload(json.dumps(app_config), "google-services.json")
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_enabling_without_a_key_is_refused(self):
		frappe.db.set_single_value("Mobile Push Settings", {"service_account_json": "", "firebase_project_id": ""})
		settings = frappe.get_doc("Mobile Push Settings")
		settings.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	# -- server-wide key (common_site_config) ---------------------------------

	def bench_key(self, content) -> str:
		import tempfile

		f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
		f.write(content if isinstance(content, str) else json.dumps(content))
		f.close()
		self.addCleanup(os.unlink, f.name)
		frappe.cache.delete_keys("field_sales:bench_firebase_key_problem:")
		return f.name

	def site_push_off(self):
		frappe.db.set_single_value("Mobile Push Settings", "enabled", 0)
		frappe.clear_document_cache("Mobile Push Settings", "Mobile Push Settings")

	def test_server_wide_key_is_used_when_the_site_has_none(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		self.site_push_off()
		path = self.bench_key({**FAKE_KEY, "project_id": "fs-push-bench"})
		with patch.dict(frappe.local.conf, {fcm.BENCH_KEY_CONF: path}):
			self.assertEqual(fcm.get_config()["source"], "bench")
			notify.notify_users([self.rep_user], "order_synced", "Field Order", "FO-BENCH", "Bench key")
		(message,) = self.sent_to(token)
		self.assertEqual(message["data"]["name"], "FO-BENCH")

	def test_site_key_overrides_the_server_wide_key(self):
		path = self.bench_key({**FAKE_KEY, "project_id": "fs-push-bench"})
		with patch.dict(frappe.local.conf, {fcm.BENCH_KEY_CONF: path}):
			config = fcm.get_config()
		self.assertEqual((config["source"], config["project_id"]), ("site", "fs-push-test"))

	def test_a_site_can_turn_push_off(self):
		path = self.bench_key(FAKE_KEY)
		with patch.dict(frappe.local.conf, {fcm.BENCH_KEY_CONF: path, fcm.PUSH_DISABLED_CONF: 1}):
			self.assertIsNone(fcm.get_config())

	def test_a_bad_server_wide_key_turns_push_off_and_is_logged_once(self):
		self.site_push_off()
		path = self.bench_key({"project_info": {}, "client": []})  # google-services.json by mistake
		with patch.dict(frappe.local.conf, {fcm.BENCH_KEY_CONF: path}), patch.object(fcm.frappe, "log_error") as log:
			self.assertIsNone(fcm.get_config())
			self.assertIsNone(fcm.get_config())
		log.assert_called_once()
		self.assertEqual(log.call_args.kwargs["title"], "Firebase key file problem")

	# -- Send Test Notification ------------------------------------------------

	def test_admin_can_send_a_test_to_another_users_phone(self):
		settings_module = importlib.import_module(
			f"{APP}.{APP}.doctype.mobile_push_settings.mobile_push_settings"
		)
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)

		users = settings_module.users_with_phones()
		self.assertIn(self.rep_user, [u["user"] for u in users])

		result = settings_module.send_test_notification(self.rep_user)
		self.assertEqual(result["sent"], 1)
		self.assertEqual(self.sent_to(token)[0]["data"]["kind"], "test")

		with self.assertRaises(frappe.ValidationError):
			settings_module.send_test_notification(self.manager_user)  # no phone

		frappe.set_user(self.rep_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				settings_module.users_with_phones()
		finally:
			frappe.set_user("Administrator")

	# -- bell → push ---------------------------------------------------------

	def test_visit_assignment_is_pushed_without_the_shop_name(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		frappe.set_user(self.manager_user)
		visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.rep,
				"retailer": self.retailer.name,
				"visit_date": add_days(nowdate(), 1),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)
		frappe.set_user("Administrator")

		(message,) = self.sent_to(token)
		log = frappe.db.get_value(
			"Notification Log", {"document_type": "Visit", "document_name": visit.name, "for_user": self.rep_user}
		)
		self.assertEqual(message["data"], {"kind": "visit_assigned", "doctype": "Visit", "name": visit.name, "notification_log": log})
		self.assertEqual(message["notification"]["title"], "New visit assigned")
		self.assertNotIn(SHOP_NAME, json.dumps(message))
		self.assertEqual(message["android"]["notification"]["channel_id"], fcm.ANDROID_CHANNEL)

	def test_a_deduped_bell_entry_is_not_pushed_again(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		for _ in range(2):
			notify.notify_users(
				[self.rep_user], "order_synced", "Field Order", "FO-DEDUPE", "Same subject",
				dedupe_on=["document_type", "document_name", "subject"],
			)
		self.assertEqual(len(self.sent_to(token)), 1)

	def test_nothing_is_pushed_when_push_is_off(self):
		self.register(self.rep_user)
		frappe.db.set_single_value("Mobile Push Settings", "enabled", 0)
		frappe.clear_document_cache("Mobile Push Settings", "Mobile Push Settings")
		notify.notify_users([self.rep_user], "order_synced", "Field Order", "FO-OFF", "Order off")
		self.assertEqual(self.sent, [])
		self.assertTrue(frappe.db.exists("Notification Log", {"document_name": "FO-OFF", "for_user": self.rep_user}))

	def test_disabled_device_gets_nothing(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		devices.set_enabled(self.rep_user, token, False)
		notify.notify_users([self.rep_user], "order_synced", "Field Order", "FO-MUTED", "Muted")
		self.assertEqual(self.sent, [])

	# -- FCM client ----------------------------------------------------------

	def test_unregistered_token_is_switched_off(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		self.responses = [(404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}, {})]
		result = notify.send_push(self.rep_user, "test")
		self.assertEqual(result, {"sent": 0, "failed": 0, "disabled": 1})
		self.assertFalse(frappe.db.get_value("Mobile Device", {"fcm_token": token}, "enabled"))

	def test_server_error_is_retried_once(self):
		self.register(self.rep_user)
		self.responses = [(503, {}, {"Retry-After": "1"}), (200, {}, {})]
		self.assertEqual(notify.send_push(self.rep_user, "test"), {"sent": 1, "failed": 0, "disabled": 0})
		self.assertEqual(len(self.sent), 2)

	# -- new events ----------------------------------------------------------

	def test_order_synced_goes_to_the_orders_rep(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": self.rep,
				"order_date": nowdate(),
				"items": [],
			}
		)
		order.flags.ignore_mandatory = True
		order.flags.ignore_validate = True
		order.insert(ignore_permissions=True)
		events.notify_order_synced(order.name)
		(message,) = self.sent_to(token)
		self.assertEqual(message["data"]["kind"], "order_synced")
		self.assertIn(order.name, message["notification"]["body"])

	def test_statement_approved_and_paid_go_to_the_rep(self):
		token = "fcm-token-rep-0000000000000001"
		self.register(self.rep_user, token)
		statement = frappe._dict(name="CS-PUSH-0001", sales_rep=self.rep)
		frappe.set_user(self.manager_user)
		events.notify_statement(statement, "statement_approved")
		events.notify_statement(statement, "statement_paid")
		frappe.set_user("Administrator")
		self.assertEqual([m["data"]["kind"] for m in self.sent_to(token)], ["statement_approved", "statement_paid"])

	def test_low_stock_fires_only_on_the_crossing(self):
		self.assertTrue(events.crossed_low_stock(6, 5))
		self.assertTrue(events.crossed_low_stock(20, 0))
		self.assertFalse(events.crossed_low_stock(5, 3))  # already low
		self.assertFalse(events.crossed_low_stock(3, 8))  # restocked
		self.assertFalse(events.crossed_low_stock(None, 2))  # new product

	def test_low_stock_is_sent_to_managers_once_a_day(self):
		token = "fcm-token-manager-00000000000001"
		self.register(self.manager_user, token)
		events.notify_low_stock([("PUSH-LOW-1", 2)])
		events.notify_low_stock([("PUSH-LOW-1", 1)])
		(message,) = self.sent_to(token)
		self.assertEqual(message["data"]["kind"], "low_stock")

	def test_many_low_products_in_a_sync_are_one_digest(self):
		token = "fcm-token-manager-00000000000001"
		self.register(self.manager_user, token)
		events.notify_low_stock([(f"PUSH-DIGEST-{i}", 1) for i in range(4)], digest=True)
		(message,) = self.sent_to(token)
		self.assertEqual(message["data"]["kind"], "low_stock_digest")
		self.assertIn("4", message["notification"]["body"])
