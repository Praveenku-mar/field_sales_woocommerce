# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from field_sales_woocommerce.commission.engine import enqueue_create_entries
from field_sales_woocommerce.connectors import ConnectorError, get_connector
from field_sales_woocommerce.field_sales_woocommerce.doctype.visit.visit import update_order_taken
from field_sales_woocommerce.payments.razorpay import cancel_link_for_cancelled_order
from field_sales_woocommerce.permissions import get_allowed_reps, is_admin
from field_sales_woocommerce.sync.orders import enqueue_push_order
from field_sales_woocommerce.sync.stock import (
	fetch_live_stock,
	get_location_stock,
	get_sell_locations,
	live_stock_enabled,
)
from field_sales_woocommerce.utils.guards import is_system_manager, keep_server_fields
from field_sales_woocommerce.utils.limits import ORDERS_PER_MONTH, check_limit
from field_sales_woocommerce.utils.money import site_currency

# May set a line's rate other than the product's price.
PRICE_OVERRIDE_ROLES = {"Sales Manager", "System Manager"}
# Written only by the store sync (db_set); location_id is the user's pick and is validated instead.
SYNC_FIELDS = (
	"platform",
	"external_draft_id",
	"store_draft_name",
	"external_order_id",
	"store_checkout_url",
	"sync_status",
	"sync_attempts",
	"last_sync_attempt",
	"sync_error",
)
# GST slabs the app offers; the order's tax is sent to the store as its tax line.
TAX_SLABS = (0, 5, 12, 18, 28)
# The app's own id for an order it created (UUID-based): letters, digits and . _ : -
CLIENT_TEMP_ID = re.compile(r"[A-Za-z0-9._:-]{1,140}")
# Payment state is written only by server code (db_set), even on submitted orders where
# allow_on_submit would otherwise let any writer set it; no exception for System Managers.
PAYMENT_FIELDS = (
	"payment_status",
	"amount_paid",
	"razorpay_payment_link_id",
	"razorpay_payment_link_url",
	"razorpay_payment_id",
	"payment_link_created_on",
	"payment_link_expires_on",
	"paid_on",
	"manual_payment_method",
	"manual_payment_reference",
	"store_marked_paid_on",
)


class FieldOrder(Document):
	# ------------------------------------------------------------------
	# Frappe hooks
	# ------------------------------------------------------------------

	def validate(self):
		self._keep_sync_fields()
		keep_server_fields(self, PAYMENT_FIELDS)
		self._validate_client_temp_id()
		self._validate_visit()
		self._validate_location()
		self._set_row_stock()
		self._validate_item_quantities()
		self._validate_no_duplicate_products()
		self._compute_item_amounts()
		self._compute_totals()
		self._validate_amounts()
		self._validate_discount()

	def before_submit(self):
		check_limit(ORDERS_PER_MONTH)
		self._apply_live_stock()
		self._block_on_stock_warning()

	def on_update(self):
		# A draft counts as an order taken on the visit, even while stock is short.
		update_order_taken(self.visit)
		before = self.get_doc_before_save()
		if before and before.visit and before.visit != self.visit:
			update_order_taken(before.visit)

	def before_update_after_submit(self):
		# validate() does not run for "Update" on a submitted order.
		keep_server_fields(self, PAYMENT_FIELDS)

	def on_submit(self):
		self.db_set("sync_status", "Pending")
		enqueue_push_order(self.name)

	def before_cancel(self):
		if self.payment_status in ("Paid", "Partially Paid"):
			frappe.throw(
				_(
					"This order has a payment of {0}. Reconcile or refund the payment before cancelling the order."
				).format(frappe.utils.fmt_money(self.amount_paid, currency=site_currency())),
				title=_("Order is paid"),
			)
		self._settle_store_order_before_cancel()

	def on_cancel(self):
		self.db_set("sync_status", "Draft")
		self.db_set("client_temp_key", None, update_modified=False)
		update_order_taken(self.visit)
		if self.payment_status == "Link Sent":
			cancel_link_for_cancelled_order(self)
		if self.external_order_id:
			# The store order is cancelled (checked in before_cancel): reconcile its commission
			# entries, which become Cancelled (or get negative Adjustments on approved statements).
			enqueue_create_entries(self.name)

	def _settle_store_order_before_cancel(self):
		"""
		A Field Order that reached the store is cancelled here only once the store order is
		cancelled on WooCommerce (that cancel is what reverses its commission); the app never
		cancels it there itself. Refused while a push is running, and when the store cannot be
		checked.

		Sync state is re-read under a row lock: push_order writes it with db_set (modified is
		unchanged), so this document may predate a push that started after it was loaded.
		"""
		stored = frappe.db.get_value(
			"Field Order",
			self.name,
			["sync_status", "external_order_id", "external_draft_id", "store_order_name", "store_draft_name"],
			as_dict=True,
			for_update=True,
		)
		if stored.sync_status == "Pending":
			frappe.throw(
				_("Order {0} is being sent to the store. Cancel it once that has finished.").format(self.name),
				title=_("Sync in progress"),
			)
		if not (stored.external_order_id or stored.external_draft_id):
			return

		store_ref = stored.store_order_name or stored.store_draft_name or stored.external_order_id or stored.external_draft_id
		try:
			conn = get_connector()
		except frappe.ValidationError:
			frappe.throw(
				_(
					"Order {0} is on the store as {1}, but the Store Connection is off, so it cannot be checked "
					"there. Turn the Store Connection on, then cancel."
				).format(self.name, store_ref),
				title=_("Store not connected"),
			)

		try:
			order_id = stored.external_order_id or conn.delete_draft(stored.external_draft_id, self)
			if not order_id:
				return  # the open draft was deleted (or was already gone)
			order = conn.get_order(order_id)
		except ConnectorError as e:
			frappe.throw(
				_("Could not check order {0} on the store, so it was not cancelled: {1}").format(store_ref, e.message),
				title=_("Store unavailable"),
			)

		if order and not order.get("cancelled"):
			frappe.throw(
				_(
					"Order {0} is still open on the store ({1}). Cancel it there first, then cancel it here: "
					"cancelling only here would leave the store order (and its commission) in place."
				).format(self.name, store_ref),
				title=_("Cancel it on the store first"),
			)
		if not stored.external_order_id:
			# A draft completed on the store that link_completed_drafts() has not linked yet.
			self.db_set("external_order_id", order_id, update_modified=False)

	def on_trash(self):
		update_order_taken(self.visit, exclude=self.name)

	# ------------------------------------------------------------------
	# Validation helpers
	# ------------------------------------------------------------------

	def _keep_sync_fields(self):
		"""
		Store ids and sync state come from the sync, never the client: a planted
		external_draft_id would make push_order reuse (Draft mode) or delete (Confirmed mode)
		another order's draft. New orders, amended and duplicated ones included, start
		unsynced; a System Manager may correct a saved one.
		"""
		if self.is_new() or not is_system_manager():
			keep_server_fields(self, SYNC_FIELDS)

	def _validate_client_temp_id(self):
		"""
		The app's id for an order it created, set once on insert: a retry after a timeout
		finds the order (``find_order_by_client_temp_id``) or is refused as a duplicate here,
		instead of creating a second order.
		"""
		if not self.is_new():
			# Keep the stored key, never recompute it: a duplicate made before the unique key
			# existed holds none (patch client_temp_key) and must stay savable.
			before = self.get_doc_before_save()
			if before:
				self.client_temp_id = before.client_temp_id
				self.client_temp_key = before.client_temp_key
			else:
				self.client_temp_key = frappe.db.get_value("Field Order", self.name, "client_temp_key")
			return
		self.client_temp_id = (self.client_temp_id or "").strip() or None
		if not self.client_temp_id:
			self.client_temp_key = None
			return
		if not CLIENT_TEMP_ID.fullmatch(self.client_temp_id):
			frappe.throw(_("Client Temp ID may hold only letters, digits and . _ : -"))
		existing = frappe.db.get_value(
			"Field Order", {"client_temp_id": self.client_temp_id, "docstatus": ("!=", 2)}, "name"
		)
		if existing:
			frappe.throw(
				_("This order was already created as {0}").format(existing), frappe.DuplicateEntryError
			)
		self.client_temp_key = self.client_temp_id

	def _validate_visit(self):
		"""
		The visit must be to the same retailer, and belong to the user's own rep or someone in
		their team: an order cannot be booked on another team's visit. The order defaults to
		the visit's rep.
		"""
		if not self.visit:
			return
		visit = frappe.db.get_value("Visit", self.visit, ["retailer", "sales_rep", "status"], as_dict=True)
		if not visit:
			return
		if visit.retailer != self.retailer:
			frappe.throw(
				_("Visit {0} is to a different retailer. Pick a visit to {1} or clear the Visit.").format(
					self.visit, self.retailer_name or self.retailer
				)
			)
		if visit.status == "Cancelled":
			frappe.throw(_("Visit {0} is cancelled; an order cannot be taken on it.").format(self.visit))
		if not self.sales_rep:
			self.sales_rep = visit.sales_rep
		if is_admin() or not (self.has_value_changed("visit") or self.has_value_changed("sales_rep")):
			return
		if visit.sales_rep not in get_allowed_reps():
			frappe.throw(
				_("Visit {0} belongs to another sales rep. Pick one of your own visits or clear the Visit.").format(
					self.visit
				),
				frappe.PermissionError,
			)

	def _validate_location(self):
		"""
		A draft sells from one location: the Store Connection default when none is picked, and
		otherwise one of its ``Sell from here`` locations.

		Skipped while the Store Connection has no locations, so orders can be made before it is set up.
		"""
		if self.docstatus != 0:
			return
		locations = {loc["location_id"]: loc for loc in get_sell_locations()}
		if not locations:
			return

		if not self.location_id:
			default = next((loc["location_id"] for loc in locations.values() if loc["is_default"]), None)
			if not default:
				frappe.throw(
					_("The store has no default sell-from location. Pick one on the order, or set a default in Settings.")
				)
			self.location_id = default
		if self.location_id not in locations:
			frappe.throw(
				_("Location {0} is not one the shop sells from. Pick one of: {1}").format(
					self.location_id, ", ".join(loc["location_name"] for loc in locations.values())
				)
			)
		self.location_name = locations[self.location_id]["location_name"]

	def _set_row_stock(self):
		"""
		Rows show the stored stock at the order's location (Product Stock), on every draft
		save and on submit, never the client's value (read-only is only a UI hint): the
		submit's stock check (``_block_on_stock_warning``) relies on it.

		Not a ``fetch_from``: Frappe re-fetches those on every save, from Platform Product,
		which only holds the default location's stock. "Refresh Stock" saves the live figure
		at the order's location before saving the order, so the rows show it. A submitted
		order keeps the stock it was submitted against (``_apply_live_stock``).
		"""
		if self.docstatus != 0 and self.get("_action") != "submit":
			return
		stock = get_location_stock([row.product for row in self.items], self.location_id)
		for row in self.items:
			if row.product in stock:
				row.available_qty = stock[row.product]

	def _validate_item_quantities(self):
		"""Every line item must have qty > 0."""
		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(
					_("Row {0}: Quantity must be greater than 0.").format(row.idx)
				)

	def _validate_no_duplicate_products(self):
		"""The same Platform Product variant must not appear twice."""
		seen = {}
		for row in self.items:
			if not row.product:
				continue
			if row.product in seen:
				frappe.throw(
					_(
						"Row {0}: Product <b>{1}</b> is already present in Row {2}. "
						"Merge the quantities instead of adding a duplicate line."
					).format(row.idx, row.product_title or row.product, seen[row.product])
				)
			seen[row.product] = row.idx

	def _validate_discount(self):
		"""
		The discount % must be 0-100 and, for anyone but a manager, within the order's rep limit.

		Checked when a draft is saved or an order is submitted (so an amended order is checked
		again), not on later updates of a submitted order or on cancel.
		"""
		percent = flt(self.discount_percent)
		if not 0 <= percent <= 100:
			frappe.throw(_("Discount % must be between 0 and 100."))
		if self.docstatus == 2 or self.get("_action") in ("cancel", "update_after_submit"):
			return
		if percent <= 0 or frappe.session.user == "Administrator":
			return
		if {"Sales Manager", "System Manager"} & set(frappe.get_roles()):
			return

		rep = frappe.db.get_value(
			"Sales Rep", self.sales_rep, ["full_name", "max_discount_pct"], as_dict=True
		) or frappe._dict()
		limit = flt(rep.max_discount_pct)
		name = rep.full_name or self.sales_rep or frappe.session.user
		if limit <= 0:
			frappe.throw(_("{0} cannot give a discount on orders. Ask a manager.").format(name))
		if percent > limit:
			frappe.throw(_("Max discount for {0} is {1}%").format(name, limit))

	def _apply_live_stock(self):
		"""
		Re-check tracked items against the store's current stock, not the last sync.

		Every way of submitting (desk, app, API) goes through here. Skipped when the
		store connection is off or has no default location; then the stored stock is
		used, as before. If the store cannot be reached the submit is stopped: an
		order must not be sent on stock that may be out of date.
		"""
		if not live_stock_enabled():
			return
		try:
			live = fetch_live_stock([row.product for row in self.items], self.location_id)
		except ConnectorError as e:
			frappe.throw(
				_("Could not check live stock on the store, so the order was not submitted: {0}").format(e.message),
				title=_("Live stock unavailable"),
			)

		for row in self.items:
			if row.product in live:
				row.available_qty = live[row.product]
		self._compute_item_amounts()  # recalculates stock_warning on the live quantities

	def _block_on_stock_warning(self):
		"""Prevent submission when any item exceeds tracked inventory."""
		for row in self.items:
			if row.stock_warning:
				frappe.throw(
					_(
						"Row {0}: <b>{1}</b> exceeds available stock — "
						"only {2} unit(s) available."
					).format(
						row.idx,
						row.product_title or row.product,
						flt(row.available_qty),
					)
				)

	# ------------------------------------------------------------------
	# Computation helpers
	# ------------------------------------------------------------------

	def _compute_item_amounts(self):
		"""
		For every child row:
		  - rate = the Platform Product's price, unless the user may override prices
		    (PRICE_OVERRIDE_ROLES): the rate is sent to the store as the line's price.
		    A saved row whose product and rate are unchanged keeps its rate, so a rep's
		    save does not undo a manager's price (or move a draft to a newer store price).
		  - amount = qty × rate
		  - stock_warning = 1  iff  track_inventory is set on the product
		                            AND qty > available_qty

		Fetches price and track_inventory for all products in a single query to
		avoid N+1 DB hits.
		"""
		products = [row.product for row in self.items if row.product]

		product_map = {}
		if products:
			product_map = {
				r.name: r
				for r in frappe.get_all(
					"Platform Product",
					filters={"name": ["in", products]},
					fields=["name", "price", "track_inventory"],
				)
			}
		may_override_price = bool(PRICE_OVERRIDE_ROLES & set(frappe.get_roles()))
		before = self.get_doc_before_save()
		saved_rows = {r.name: r for r in before.items} if before else {}

		for row in self.items:
			product = product_map.get(row.product)
			saved = saved_rows.get(row.name)
			unchanged = saved and saved.product == row.product and flt(saved.rate) == flt(row.rate)
			if product and not may_override_price and not unchanged:
				row.rate = flt(product.price)
			if flt(row.rate) < 0:
				frappe.throw(_("Row {0}: Rate cannot be negative.").format(row.idx))

			row.amount = flt(flt(row.qty) * flt(row.rate), row.precision("amount"))

			tracks = product.track_inventory if product else 0
			row.stock_warning = (
				1 if tracks and flt(row.qty) > flt(row.available_qty) else 0
			)

	def _compute_totals(self):
		"""
		Roll up item amounts into order-level totals, rounded to currency
		precision so they match what the store calculates from the same order.
		"""
		self.total_qty = sum(flt(r.qty) for r in self.items)
		self.net_total = flt(sum(flt(r.amount) for r in self.items), self.precision("net_total"))
		self.tax_amount = flt(
			flt(self.net_total) * flt(self.tax_percent) / 100, self.precision("tax_amount")
		)
		self.discount_amount = flt(
			flt(self.net_total) * flt(self.discount_percent) / 100, self.precision("discount_amount")
		)
		self.grand_total = flt(
			flt(self.net_total) + flt(self.tax_amount) - flt(self.discount_amount),
			self.precision("grand_total"),
		)


	def _validate_amounts(self):
		"""Bounds on what is sent to the store as the order's tax and discount."""
		if not 0 <= flt(self.tax_percent) <= 100:
			frappe.throw(_("Tax % must be between 0 and 100."))
		if (self.docstatus == 0 or self.get("_action") == "submit") and flt(self.tax_percent) not in TAX_SLABS:
			frappe.throw(
				_("Tax % must be one of {0}.").format(", ".join(f"{slab}%" for slab in TAX_SLABS))
			)
		if flt(self.discount_amount) < 0:
			frappe.throw(_("Discount cannot be negative."))
		if flt(self.discount_amount) > flt(self.net_total) + flt(self.tax_amount):
			frappe.throw(_("Discount cannot be more than the order total."))


def on_doctype_update():
	# Rep Performance and list filters read orders by rep over a date range.
	frappe.db.add_index("Field Order", ["sales_rep", "order_date"])
