# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The background jobs every Field Sales edition schedules (hooks.scheduler_events).

Without them product sync, order retries, payment reconciling, the daily summary and monthly
commission statements silently never run. The same file runs in field_sales,
field_sales_woocommerce and field_sales_odoo, so no edition can drop a job unnoticed.
"""

import importlib

import frappe
from frappe.tests import UnitTestCase

APP = __name__.split(".", 1)[0]
hooks = importlib.import_module(f"{APP}.hooks")

# Every edition runs these. Paths are relative to the app package.
SHARED_JOBS = {
	f"{APP}.doctype.sync_log.sync_log.clear_old_logs",
	"sync.shop_settings.scheduled_sync",
	"utils.anonymise.anonymise_inactive_retailers",
	"payments.razorpay.reconcile_open_links",
	"sync.orders.retry_store_payments",
	"sync.products.scheduled_sync",
	"sync.orders.retry_failed_orders",
	"daily_summary.send_daily_summary",
	"commission.statements.generate_monthly_statements",
}
# Only for platforms with draft orders (WooCommerce's Draft mode makes a real `pending` order).
DRAFT_JOBS = {"sync.orders.link_completed_drafts"}
HAS_DRAFTS = {"field_sales": True, "field_sales_woocommerce": False, "field_sales_odoo": True}


def scheduled_methods() -> list[str]:
	events = getattr(hooks, "scheduler_events", None) or {}
	methods = []
	for key, value in events.items():
		if key == "cron":
			for cron_methods in value.values():
				methods.extend(cron_methods)
		else:
			methods.extend(value)
	return methods


class TestSchedulerHooks(UnitTestCase):
	def test_every_expected_job_is_scheduled(self):
		expected = SHARED_JOBS | (DRAFT_JOBS if HAS_DRAFTS[APP] else set())
		scheduled = {m.removeprefix(f"{APP}.") for m in scheduled_methods()}
		self.assertEqual(sorted(expected - scheduled), [], "jobs missing from hooks.scheduler_events")
		if not HAS_DRAFTS[APP]:
			self.assertFalse(DRAFT_JOBS & scheduled, "this platform has no draft orders to link")

	def test_every_scheduled_method_exists(self):
		for method in scheduled_methods():
			with self.subTest(method=method):
				self.assertTrue(method.startswith(f"{APP}."), "a job of another app")
				self.assertTrue(callable(frappe.get_attr(method)))

	def test_each_job_is_scheduled_once(self):
		methods = scheduled_methods()
		self.assertEqual(len(methods), len(set(methods)))
