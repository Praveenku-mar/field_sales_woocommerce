# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Erasing a retailer (utils/anonymise.py) leaves no personal data behind: the login user, notes,
notifications, Sync Log bodies and Razorpay webhook payloads go with the record's own fields,
while amounts, quantities and dates stay for reporting. The same file runs in every edition.
"""

import importlib

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

APP = __name__.split(".", 1)[0]
anonymise_module = importlib.import_module(f"{APP}.utils.anonymise")
anonymise = anonymise_module.anonymise
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]

EMAIL = "erase.buyer@example.com"


class TestAnonymise(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": PLATFORM})
		if not frappe.db.exists("Role", "Field Sales Retailer"):
			frappe.get_doc({"doctype": "Role", "role_name": "Field Sales Retailer", "desk_access": 0}).insert(
				ignore_permissions=True
			)
		rep_user = self.user("erase.rep@example.com", ["Field Sales Rep"])
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": rep_user}).insert(ignore_permissions=True).name
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": "Erase product",
				"external_product_id": "erase-1",
				"external_variant_id": "erase-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Erase Me Stores",
				"owner_name": "Erin Buyer",
				"phone": "9876543210",
				"email": EMAIL,
				"address_line1": "7 Market Street",
				"city": "Salem",
				"sales_rep": self.rep,
			}
		).insert(ignore_permissions=True).name
		self.login = self.user(EMAIL, ["Field Sales Retailer"], mobile_no="9876543210", last_name="Buyer")
		frappe.db.set_value("Retailer", self.retailer, "user", self.login)
		self.visit = frappe.get_doc(
			{"doctype": "Visit", "sales_rep": self.rep, "retailer": self.retailer, "visit_date": nowdate()}
		).insert(ignore_permissions=True).name
		self.order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer,
				"sales_rep": self.rep,
				"order_date": nowdate(),
				"items": [{"product": self.product.name, "qty": 3, "rate": 100}],
			}
		).insert(ignore_permissions=True).name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def user(self, email, roles, **values):
		doc = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Erin", "send_welcome_email": 0, **values}
		).insert(ignore_permissions=True)
		doc.add_roles(*roles)
		return doc.name

	def comment(self, doctype, name, text):
		return frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": doctype,
				"reference_name": name,
				"content": text,
			}
		).insert(ignore_permissions=True).name

	def notification(self, doctype, name):
		return frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": "erase.rep@example.com",
				"subject": "Erase Me Stores paid",
				"document_type": doctype,
				"document_name": name,
				"type": "Alert",
			}
		).insert(ignore_permissions=True).name

	def sync_log(self, doctype, name):
		meta = frappe.get_meta("Sync Log")
		return frappe.get_doc(
			{
				"doctype": "Sync Log",
				"platform": meta.get_field("platform").options.split("\n")[0],
				"operation": meta.get_field("operation").options.split("\n")[0],
				"status": "Failed",
				"reference_doctype": doctype,
				"reference_name": name,
				"request_payload": f'{{"email": "{EMAIL}", "phone": "9876543210"}}',
				"response_payload": '{"customer": {"firstName": "Erin"}}',
			}
		).insert(ignore_permissions=True).name

	def webhook_event(self, order):
		return frappe.get_doc(
			{
				"doctype": "Razorpay Webhook Event",
				"event_id": f"evt_{order}",
				"event": "payment_link.paid",
				"status": "Processed",
				"reference_order": order,
				"payload": f'{{"customer": {{"email": "{EMAIL}"}}}}',
			}
		).insert(ignore_permissions=True).name

	# -- tests -------------------------------------------------------------

	def test_login_user_is_anonymised_and_renamed(self):
		result = anonymise([self.retailer], [self.order])
		self.assertEqual(result["users_anonymised"], 1)
		self.assertFalse(frappe.db.exists("User", EMAIL))
		renamed = frappe.get_all("User", filters={"name": ("like", "redacted-%@redacted.invalid")}, pluck="name")
		self.assertEqual(len(renamed), 1)
		user = frappe.db.get_value(
			"User", renamed[0], ["enabled", "email", "first_name", "last_name", "mobile_no"], as_dict=True
		)
		self.assertEqual(user.enabled, 0)
		self.assertEqual(user.email, renamed[0])
		self.assertEqual(user.first_name, "Redacted")
		self.assertFalse(user.last_name or user.mobile_no)
		self.assertFalse(frappe.db.get_value("Retailer", self.retailer, "user"))

	def test_staff_user_is_never_renamed(self):
		frappe.get_doc("User", self.login).add_roles("Field Sales Rep")
		result = anonymise([self.retailer], [self.order])
		self.assertEqual(result["users_anonymised"], 0)
		self.assertTrue(frappe.db.exists("User", EMAIL))
		self.assertEqual(frappe.db.get_value("User", EMAIL, "last_name"), "Buyer")

	def test_notes_notifications_logs_and_payloads_are_removed(self):
		comments = [self.comment(dt, n, "Erin said call back") for dt, n in
			(("Retailer", self.retailer), ("Visit", self.visit), ("Field Order", self.order))]  # fmt: skip
		notification = self.notification("Field Order", self.order)
		logs = [self.sync_log("Retailer", self.retailer), self.sync_log("Field Order", self.order)]
		event = self.webhook_event(self.order)
		someone_else = frappe.get_doc(
			{"doctype": "Retailer", "retailer_name": "Keep Me Stores", "phone": "9876543211",
			 "address_line1": "8 Market Street", "city": "Salem", "sales_rep": self.rep}
		).insert(ignore_permissions=True).name  # fmt: skip
		other_log = self.sync_log("Retailer", someone_else)

		result = anonymise([self.retailer], [self.order])

		for name in comments:
			self.assertFalse(frappe.db.exists("Comment", name))
		self.assertFalse(frappe.db.exists("Notification Log", notification))
		for name in logs:
			row = frappe.db.get_value("Sync Log", name, ["request_payload", "response_payload"], as_dict=True)
			self.assertFalse(row.request_payload or row.response_payload)
		self.assertFalse(frappe.db.get_value("Razorpay Webhook Event", event, "payload"))
		# Other records' logs are left alone.
		self.assertTrue(frappe.db.get_value("Sync Log", other_log, "request_payload"))
		self.assertEqual(result["comments_deleted"], 3)
		# The app's own notifications about these records (e.g. the visit assignment) go as well.
		self.assertGreaterEqual(result["notifications_deleted"], 1)
		for doctype, name in (("Retailer", self.retailer), ("Visit", self.visit), ("Field Order", self.order)):
			self.assertFalse(frappe.db.exists("Notification Log", {"document_type": doctype, "document_name": name}))
		self.assertEqual(result["sync_log_bodies_cleared"], 2)
		self.assertEqual(result["webhook_payloads_cleared"], 1)

	def test_amounts_quantities_and_dates_stay(self):
		before = frappe.db.get_value("Field Order", self.order, ["grand_total", "total_qty", "order_date"], as_dict=True)
		anonymise([self.retailer], [self.order])
		after = frappe.db.get_value(
			"Field Order", self.order, ["grand_total", "total_qty", "order_date", "retailer_name"], as_dict=True
		)
		self.assertEqual((after.grand_total, after.total_qty, after.order_date),
			(before.grand_total, before.total_qty, before.order_date))  # fmt: skip
		self.assertTrue(after.retailer_name.startswith("Redacted"))
