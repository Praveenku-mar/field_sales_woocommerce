# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from field_sales_woocommerce.connectors import CONNECTORS


class StoreConnection(Document):
	def validate(self):
		self._normalize_store_url()
		self._validate_locations()
		self.flags.secret_entered = bool(self.woo_consumer_secret) and not self.is_dummy_password(
			self.woo_consumer_secret
		)

	def on_update(self):
		self._queue_shop_settings_sync()

	def _queue_shop_settings_sync(self):
		if not (self.enabled and self.platform == "WooCommerce" and self.match_shop_settings):
			return
		changed = (
			any(
				self.has_value_changed(f)
				for f in ("enabled", "store_url", "woo_consumer_key", "match_shop_settings")
			)
			or self.flags.secret_entered
			or not self.shop_currency
		)
		if not changed:
			return
		frappe.enqueue(
			"field_sales_woocommerce.sync.shop_settings.scheduled_sync",
			queue="short",
			enqueue_after_commit=True,
			job_id="field_sales_shop_settings_sync",
			deduplicate=True,
			notify_user=frappe.session.user,
		)

	def _validate_locations(self):
		seen = set()
		for row in self.locations:
			row.location_id = (row.location_id or "").strip()
			if row.location_id in seen:
				frappe.throw(_("Row {0}: Location {1} is listed twice").format(row.idx, row.location_id))
			seen.add(row.location_id)

		default_id = (self.default_location_id or "").strip() or "woo"
		self.default_location_id = default_id
		default_name = self.default_location_name or "WooCommerce Stock"
		default_row = next((r for r in self.locations if r.location_id == default_id), None)
		if not default_row:
			default_row = self.append(
				"locations", {"location_id": default_id, "location_name": default_name}
			)
		default_row.sell_from = 1
		self.default_location_name = default_row.location_name or default_name

	def _normalize_store_url(self):
		connector_cls = CONNECTORS.get(self.platform)
		if connector_cls and self.store_url:
			self.store_url = connector_cls.normalize_store_url(self.store_url)
