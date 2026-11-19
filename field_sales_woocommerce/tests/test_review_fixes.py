# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Fixes from the 2026-11 code review, ported from field_sales; the same file runs in every edition.

- F11: a checked-in visit keeps its retailer, date and rep.
- F8: a statement whose entries changed after it was built is not approved; a refund missed by
  an earlier approval is booked against what the statement paid.
- F10: GST is charged on the discounted value.
- F7: an unchanged product's stock figure that a webhook changed is put back by the product sync.
- A new draft (no items yet) can switch its location.
- A store webhook is processed as the system, not Guest, so jobs it queues may print the invoice.
(F12 and F9 are tested in the Field Order and Commission Entry doctype tests.)
"""

import importlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.client import save
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, get_first_day, get_last_day, nowdate

APP = __name__.split(".", 1)[0]
PLATFORM = {"field_sales": "Shopify", "field_sales_woocommerce": "WooCommerce", "field_sales_odoo": "Odoo"}[APP]
api = importlib.import_module(f"{APP}.api")
engine = importlib.import_module(f"{APP}.commission.engine")
statements = importlib.import_module(f"{APP}.commission.statements")
commission_api = importlib.import_module(f"{APP}.commission.api")
products_sync = importlib.import_module(f"{APP}.sync.products")
stock = importlib.import_module(f"{APP}.sync.stock")
webhooks = importlib.import_module(f"{APP}.sync.webhooks")

MAIN, WAREHOUSE, CLOSED = "100", "200", "300"
LAT, LNG = 11.0168, 76.9558  # Coimbatore


class ReviewFixesCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_default("currency", "INR")
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": PLATFORM, "enabled": 0, "default_location_id": MAIN, "default_location_name": "Main"},
		)
		settings = frappe.get_doc("Store Connection")
		settings.set("locations", [])
		for location_id, name, sell_from in ((MAIN, "Main", 1), (WAREHOUSE, "Warehouse", 1), (CLOSED, "Closed", 0)):
			settings.append("locations", {"location_id": location_id, "location_name": name, "sell_from": sell_from})
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
		self.users = 0
		self.manager_user, self.manager = self.make_rep(roles=("Field Sales Rep", "Sales Manager"))
		self.rep_user, self.rep = self.make_rep(reports_to=self.manager.name)
		self.retailer = self.make_retailer("Review Stores", city="Coimbatore")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_rep(self, roles=("Field Sales Rep",), **values):
		self.users += 1
		email = f"review.fix{self.users}@example.com"
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": f"Review{self.users}", "send_welcome_email": 0})
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		rep = frappe.get_doc({"doctype": "Sales Rep", "user": email, **values}).insert(ignore_permissions=True)
		return email, rep

	def make_retailer(self, name, **values):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": name,
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"sales_rep": self.rep.name,
				**values,
			}
		).insert(ignore_permissions=True)

	def make_product(self, variant_id, **values):
		return frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": PLATFORM,
				"product_title": f"Review product {variant_id}",
				"external_product_id": f"9{variant_id}",
				"external_variant_id": variant_id,
				"status": "Active",
				"price": 250,
				"track_inventory": 0,
				**values,
			}
		).insert(ignore_permissions=True)

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")


class TestCheckedInVisitIsFixed(ReviewFixesCase):
	"""F11"""

	def setUp(self):
		super().setUp()
		self.visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.rep.name,
				"retailer": self.retailer.name,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)

	def save_visit_as(self, user, **changes):
		doc = frappe.get_doc("Visit", self.visit.name).as_dict()
		doc.update(changes)
		return self.as_user(user, save, doc)

	def check_in(self):
		self.as_user(self.rep_user, api.check_in, self.visit.name, LAT, LNG, 10)

	def test_retailer_date_and_rep_are_fixed_after_check_in(self):
		far = self.make_retailer("Far Away Stores", city="Chennai", latitude=13.0827, longitude=80.2707)
		self.check_in()
		for user, change in (
			(self.rep_user, {"retailer": far.name}),
			(self.rep_user, {"visit_date": add_days(nowdate(), -1)}),
			(self.manager_user, {"sales_rep": self.manager.name}),
		):
			with self.assertRaises(frappe.PermissionError):
				self.save_visit_as(user, **change)
		self.assertEqual(frappe.db.get_value("Visit", self.visit.name, "retailer"), self.retailer.name)

	def test_a_planned_visit_can_still_move_and_notes_still_save(self):
		far = self.make_retailer("Far Away Stores", city="Chennai")
		self.save_visit_as(self.manager_user, retailer=far.name, visit_date=add_days(nowdate(), 1))
		self.assertEqual(frappe.db.get_value("Visit", self.visit.name, "retailer"), far.name)

		self.save_visit_as(self.manager_user, retailer=self.retailer.name, visit_date=nowdate())
		self.check_in()
		self.save_visit_as(self.rep_user, notes="Owner asked for the catalogue")
		self.assertEqual(frappe.db.get_value("Visit", self.visit.name, "notes"), "Owner asked for the catalogue")


class TestStatementMissesNoRefund(ReviewFixesCase):
	"""F8"""

	def setUp(self):
		super().setUp()
		frappe.db.delete("Commission Entry")
		plan = frappe.get_doc(
			{"doctype": "Commission Plan", "plan_name": "Review 10%", "is_active": 1, "rule_type": "Flat %", "commission_on": "Paid", "flat_rate": 10}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Sales Rep", self.rep.name, {"commission_plan": plan.name, "commission_active": 1})
		self.product = self.make_product("701")

	def order(self, order_id):
		"""A 1,000 submitted order (commission 100) with its store order id."""
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": self.rep.name,
				"order_date": nowdate(),
				"items": [{"product": self.product.name, "qty": 4, "rate": 250, "external_variant_id": "701"}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Field Order", doc.name, {"docstatus": 1, "external_order_id": order_id, "platform": PLATFORM})

	def store_order(self, order_id, refunded_qty=0):
		return {
			"external_order_id": order_id,
			"currency": "INR",
			"financial_status": "partially_refunded" if refunded_qty else "paid",
			"fulfillment_status": "unfulfilled",
			"cancelled": False,
			"lines": [
				{"line_id": "L1", "external_variant_id": "701", "qty": 4, "current_qty": 4, "unit_price": 250, "discount": 0, "tags": []}
			],
			"refunds": [{"refund_id": "R1", "lines": [{"line_id": "L1", "qty": refunded_qty, "amount": 0}]}] if refunded_qty else [],
		}

	def reconcile(self, order):
		conn = MagicMock()
		conn.get_order.return_value = order
		with patch.object(engine, "get_connector", return_value=conn):
			engine.reconcile_order(order["external_order_id"])

	def statement(self):
		today = nowdate()
		return statements.build_statement(self.rep.name, get_first_day(today), get_last_day(today))

	def test_out_of_date_statement_is_refused_until_regenerated(self):
		self.order("8101")
		self.reconcile(self.store_order("8101"))
		statement = self.statement()
		self.assertEqual(flt(statement.net_payable), 100)
		self.reconcile(self.store_order("8101", refunded_qty=1))  # a quarter refunded while it is a Draft

		with self.assertRaises(frappe.ValidationError):
			commission_api.approve_statement(statement.name)
		self.assertEqual(frappe.db.count("Commission Entry", {"commission_statement": statement.name}), 1)

		statements.regenerate_statement(statement.name)
		result = commission_api.approve_statement(statement.name)
		self.assertEqual(flt(result["net_payable"]), 75)

	def test_refund_missed_by_an_approved_statement_is_booked(self):
		self.order("8102")
		self.reconcile(self.store_order("8102"))
		self.statement().submit()
		entry = frappe.db.get_value("Commission Entry", {"entry_type": "Order"}, "name")
		frappe.db.set_value("Commission Entry", entry, "reversed_amount", 25, update_modified=False)  # changed in place earlier

		self.reconcile(self.store_order("8102", refunded_qty=1))

		adjustments = frappe.get_all("Commission Entry", filters={"entry_type": "Adjustment"}, fields=["amount", "adjusts_entry"])
		self.assertEqual([(flt(a.amount), a.adjusts_entry) for a in adjustments], [(-25, entry)])


class TestGstAfterDiscount(ReviewFixesCase):
	"""F10"""

	def test_tax_is_on_the_discounted_value(self):
		product = self.make_product("702", price=100)
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": self.rep.name,
				"order_date": nowdate(),
				"tax_percent": 18,
				"discount_percent": 10,
				"items": [{"product": product.name, "qty": 10, "rate": 100}],
			}
		)
		doc.insert(ignore_permissions=True)  # as Administrator: no discount limit
		self.assertEqual(flt(doc.net_total), 1000)
		self.assertEqual(flt(doc.discount_amount), 100)
		self.assertEqual(flt(doc.tax_amount), 162)  # 18% of 900, not of 1,000
		self.assertEqual(flt(doc.grand_total), 1062)


class TestProductSyncPutsBackStock(ReviewFixesCase):
	"""F7"""

	def test_unchanged_product_gets_the_stores_figure_back(self):
		product = self.make_product("703", external_inventory_id="5703", track_inventory=1, available_qty=10, status="Active")
		item = {
			"external_product_id": "9703",
			"external_variant_id": "703",
			"external_inventory_id": "5703",
			"product_title": "Synced",
			"variant_title": "",
			"sku": "",
			"barcode": "",
			"price": 5.0,
			"compare_at_price": None,
			"collection": "",
			"image_url": "",
			"status": "Active",
			"track_inventory": True,
		}
		conn = MagicMock()
		conn.settings.platform = PLATFORM
		conn.settings.default_location_id = MAIN
		conn.get_product.return_value = [item]
		conn.get_stock.return_value = {"5703": 10.0}

		products_sync.sync_one_product(conn, "9703")
		stock.set_location_stock(product.name, MAIN, 5.0)  # a webhook or live check
		summary = products_sync.sync_one_product(conn, "9703")

		self.assertEqual(summary["unchanged"], 1)
		self.assertEqual(frappe.db.get_value("Platform Product", product.name, "available_qty"), 10)


class TestNewDraftLocation(ReviewFixesCase):
	def test_a_draft_without_items_switches_location(self):
		name = self.as_user(self.rep_user, api.create_order, self.retailer.name, self.rep.name, nowdate())["name"]
		self.assertFalse(frappe.get_doc("Field Order", name).items)

		result = self.as_user(self.rep_user, api.set_order_location, name, WAREHOUSE)

		self.assertEqual(result["location_id"], WAREHOUSE)
		with self.assertRaises(frappe.ValidationError):
			api.set_order_location(name, CLOSED)


class TestWebhookRunsAsSystem(ReviewFixesCase):
	def test_a_store_event_is_processed_as_administrator(self):
		seen = {}

		def handle(conn, event):
			seen["user"] = frappe.session.user
			return {"note": "ok"}

		frappe.set_user("Guest")
		with (
			patch.object(webhooks, "get_connector", return_value=MagicMock()),
			patch.object(webhooks, "_process_order_event", side_effect=handle),
		):
			topic = next(iter(webhooks.ORDER_TOPICS))
			webhooks.process_event(PLATFORM, {"topic": topic, "payload": {"id": 1}})

		self.assertEqual(seen["user"], "Administrator")
