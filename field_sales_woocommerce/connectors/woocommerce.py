# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
WooCommerce connector.

Talks to the store through the WooCommerce REST API v3 (``/wp-json/wc/v3``).
Authentication is one-legged OAuth 1.0a signed with the Consumer Key/Secret,
which works over both HTTPS and plain HTTP (Basic auth is refused by
WooCommerce unless the site is HTTPS).

WooCommerce has one stock pool, not locations: ``get_locations`` reports the
single pseudo-location ``"woo"`` so the app's location machinery (default
location, Product Stock rows) works unchanged. ``order_mode`` maps to order
status: ``Draft`` creates the order as ``pending`` (nothing else happens on
the store until it is edited there), ``Confirmed`` as ``processing``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING
import frappe
import requests
from frappe import _
from requests_oauthlib import OAuth1

from field_sales_woocommerce.connectors.base import (
	ORDER_CANCELLED_TOPIC,
	ORDER_FULFILLED_TOPIC,
	ORDER_PAID_TOPIC,
	PRODUCT_DELETE_TOPIC,
	PRODUCT_UPDATE_TOPIC,
	STOCK_TOPIC,
	BaseConnector,
	ConnectorError,
)

if TYPE_CHECKING:
	from frappe.model.document import Document

TIMEOUT = 30
PAGE_SIZE = 100
#: The single stock pool, as a location the app can select.
STOCK_LOCATION_ID = "woo"
STOCK_LOCATION_NAME = "WooCommerce Stock"


class WooCommerceConnector(BaseConnector):
	platform = "WooCommerce"

	def __init__(self, settings: Document):
		super().__init__(settings)
		self.base_url = self.normalize_store_url(settings.store_url).rstrip("/")
		api_version = (settings.woo_api_version or "wc/v3").strip("/")
		self.api_url = f"{self.base_url}/wp-json/{api_version}"
		# The loaded single holds the masked dummy for a saved secret; get_password
		# returns the stored one.
		self.consumer_secret = settings.get_password("woo_consumer_secret", raise_exception=False) or ""
		if not settings.woo_consumer_key or not self.consumer_secret:
			frappe.throw(_("WooCommerce Consumer Key and Secret are required on Store Connection"))
		# OAuth1 signing works over http and https; Basic auth does not.
		self.auth = OAuth1(
			settings.woo_consumer_key,
			self.consumer_secret,
			signature_method="HMAC-SHA1",
		)
		# Stock seen on product payloads, for get_stock: the sync asks for stock
		# right after get_products/get_product of the same ids.
		self._stock_cache: dict[str, float] = {}

	# ------------------------------------------------------------------
	# HTTP layer
	# ------------------------------------------------------------------

	def _request(
		self,
		method: str,
		path: str,
		operation: str,
		params: dict | None = None,
		json_body: dict | None = None,
		ref: Document | None = None,
	) -> dict:
		url = f"{self.api_url}{path}"
		start = time.monotonic()
		try:
			response = requests.request(
				method, url, auth=self.auth, params=params, json=json_body, timeout=TIMEOUT
			)
		except requests.exceptions.RequestException as e:
			self._write_log(
				operation,
				ref,
				status="Failed",
				duration_ms=_elapsed_ms(start),
				error_code="NETWORK_ERROR",
				error_message=str(e)[:500],
				request_payload=_safe_payload(params or json_body),
			)
			raise ConnectorError(
				_("Could not reach the WooCommerce store at {0}").format(self.base_url),
				code="NETWORK_ERROR",
			) from e

		if response.status_code in (401, 403):
			self._log_failed(operation, ref, start, response, params or json_body)
			raise ConnectorError(
				_("WooCommerce rejected the Consumer Key/Secret (HTTP {0}). Check them on"
				" WooCommerce > Settings > Advanced > REST API.").format(response.status_code),
				code="AUTH_FAILED",
				http_status=response.status_code,
			)

		if response.status_code >= 400:
			self._log_failed(operation, ref, start, response, params or json_body)
			detail = _error_message(response)
			raise ConnectorError(
				_("WooCommerce {0} {1} failed (HTTP {2}): {3}").format(
					method, path, response.status_code, detail
				),
				code="HTTP_ERROR",
				http_status=response.status_code,
			)

		self._write_log(
			operation,
			ref,
			status="Success",
			duration_ms=_elapsed_ms(start),
			http_status=response.status_code,
			request_payload=_safe_payload(params or json_body),
			response_payload=_truncate(response.text),
		)
		if not response.content:
			return {}
		return response.json()

	def _log_failed(self, operation, ref, start, response, payload) -> None:
		self._write_log(
			operation,
			ref,
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=response.status_code,
			error_code=_error_code(response),
			error_message=_truncate(_error_message(response)),
			request_payload=_safe_payload(payload),
			response_payload=_truncate(response.text),
		)

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	@staticmethod
	def normalize_store_url(url: str | None) -> str:
		url = (url or "").strip()
		if not url:
			frappe.throw(_("Store URL is required on Store Connection"))
		if not url.startswith(("http://", "https://")):
			url = f"https://{url}"
		return url.rstrip("/")

	def test_connection(self) -> dict:
		try:
			data = self._request("GET", "/system_status", operation="Test Connection")
		except ConnectorError as e:
			return {"ok": False, "shop_name": "", "message": e.message}

		env = data.get("environment") or {}
		store_settings = data.get("settings") or {}
		name = (data.get("site") or {}).get("name") or env.get("home_url") or self.base_url.split("//")[-1]
		return {
			"ok": True,
			"shop_name": name,
			"scopes": ["read", "write"],
			"currency": store_settings.get("currency") or "",
			"timezone": env.get("default_timezone") or "",
			"country": store_settings.get("country") or "",
			"message": _("Connected to {0} (WooCommerce {1})").format(name, env.get("version") or "?"),
		}

	def get_shop_settings(self) -> dict:
		data = self._request("GET", "/system_status", operation="Other")
		return {
			"currency": (data.get("settings") or {}).get("currency") or "",
			"timezone": (data.get("environment") or {}).get("default_timezone") or "",
			"country": (data.get("settings") or {}).get("country") or "",
		}

	def get_locations(self) -> list[dict]:
		"""WooCommerce has one stock pool; report it as the single location."""
		return [{"id": STOCK_LOCATION_ID, "name": _(STOCK_LOCATION_NAME)}]

	def get_products(self, cursor: str | None = None) -> dict:
		page = int(cursor or 1)
		data = self._request(
			"GET",
			"/products",
			operation="Product Sync",
			params={"per_page": PAGE_SIZE, "page": page, "status": "any", "orderby": "id", "order": "asc"},
		)

		items: list[dict] = []
		for product in data:
			if product.get("type") == "variable":
				items.extend(self._variation_items(product))
			else:
				item = self._normalize_product(product)
				if item:
					items.append(item)

		next_cursor = str(page + 1) if len(data) == PAGE_SIZE else None
		return {"items": items, "next_cursor": next_cursor}

	def get_product(self, external_product_id: str) -> list[dict]:
		try:
			product = self._request("GET", f"/products/{external_product_id}", operation="Product Sync")
		except ConnectorError as e:
			if e.http_status == 404:
				return []
			raise
		if product.get("type") == "variable":
			return self._variation_items(product)
		item = self._normalize_product(product)
		return [item] if item else []

	def get_stock(self, inventory_ids: list, location_id: str) -> dict:
		"""
		Available stock per inventory id (the product or variation id).

		Ids seen on this instance's product payloads come from the cache; anything
		else — the live stock check at submit asks with a fresh connector — is
		looked up on the store: one call for the simple products, one per variable
		product for its variations.
		"""
		wanted = [str(i) for i in inventory_ids]
		result = {i: self._stock_cache.get(i, 0) for i in wanted}
		missing = [i for i in wanted if i not in self._stock_cache]
		if not missing:
			return result

		rows = frappe.get_all(
			"Platform Product",
			filters={"external_variant_id": ("in", missing)},
			fields=["external_variant_id", "external_product_id"],
			limit_page_length=0,
		)
		by_product: dict[str, set[str]] = {}
		for row in rows:
			by_product.setdefault(str(row.external_product_id), set()).add(str(row.external_variant_id))
		if not by_product:
			return result

		products = self._request(
			"GET",
			"/products",
			operation="Stock Check",
			params={"per_page": PAGE_SIZE, "include": ",".join(by_product)},
		)
		for product in products:
			ids = by_product.get(str(product["id"]), set())
			if product.get("type") == "variable":
				variations = self._request(
					"GET",
					f"/products/{product['id']}/variations",
					operation="Stock Check",
					params={"per_page": PAGE_SIZE},
				)
				for variation in variations:
					if str(variation["id"]) in ids:
						self._stock_cache[str(variation["id"])] = frappe.utils.flt(
							variation.get("stock_quantity")
						)
			elif str(product["id"]) in ids:
				self._stock_cache[str(product["id"])] = frappe.utils.flt(product.get("stock_quantity"))

		for i in missing:
			result[i] = self._stock_cache.get(i, 0)
		return result

	def create_customer(self, retailer: Document) -> dict:
		if retailer.external_customer_id:
			return self._update_customer(retailer)

		body = _customer_body(retailer)
		try:
			data = self._request("POST", "/customers", operation="Create Customer", json_body=body, ref=retailer)
		except ConnectorError as e:
			taken = _email_taken_code(e) and self._find_customer_by_email(body["email"], retailer)
			if not taken:
				raise
			data = taken
		return {"id": str(data["id"]), "warning": _email_warning(retailer)}

	def create_order(self, field_order: Document) -> dict:
		# Retry-safe: an order already on the store is reused, never duplicated.
		if field_order.external_order_id:
			try:
				self._request("GET", f"/orders/{field_order.external_order_id}", operation="Create Order", ref=field_order)
				return {"draft_id": None, "order_id": str(field_order.external_order_id), "order_name": None}
			except ConnectorError as e:
				if e.http_status != 404:
					raise

		status = "processing" if self.settings.order_mode == "Confirmed" else "pending"
		external_customer_id = frappe.db.get_value("Retailer", field_order.retailer, "external_customer_id") or 0
		rep_name = frappe.db.get_value("Sales Rep", field_order.sales_rep, "full_name")
		body = {
			"status": status,
			"customer_id": int(external_customer_id) if str(external_customer_id).isdigit() else 0,
			"billing": _retailer_address(field_order),
			"shipping": _retailer_address(field_order),
			"line_items": [_line_item(row) for row in field_order.items if row.product],
			"meta_data": [
				{"key": "field_order_no", "value": field_order.name},
				{"key": "sales_rep", "value": rep_name or field_order.sales_rep or ""},
			],
		}
		if frappe.utils.flt(field_order.tax_percent):
			body["tax_lines"] = _tax_line(field_order)
		if frappe.utils.flt(field_order.discount_amount):
			body["fee_lines"] = [
				{
					"name": _("Discount"),
					"total": f"-{frappe.utils.flt(field_order.discount_amount):.2f}",
					"is_taxable": False,
				}
			]
		body = {k: v for k, v in body.items() if v is not None}

		data = self._request("POST", "/orders", operation="Confirm Order" if status == "processing" else "Create Order", json_body=body, ref=field_order)
		return {"draft_id": None, "order_id": str(data["id"]), "order_name": f"#{data['number']}"}

	def mark_order_paid(self, field_order: Document) -> dict:
		data = self._request(
			"PUT",
			f"/orders/{field_order.external_order_id}",
			operation="Mark Order Paid",
			json_body={"set_paid": True},
			ref=field_order,
		)
		return {"financial_status": "paid" if data.get("date_paid") else "pending", "already_paid": False}

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		if not order_id:
			return None
		return f"{self.base_url}/wp-admin/post.php?post={order_id}&action=edit"

	def get_order(self, external_id: str) -> dict:
		try:
			order = self._request("GET", f"/orders/{external_id}", operation="Commission")
		except ConnectorError as e:
			if e.http_status == 404:
				return {}
			raise
		return _normalize_order(order)

	def set_order_metafields(self, order_id: str, field_order: Document) -> None:
		self._request(
			"PUT",
			f"/orders/{order_id}",
			operation="Other",
			json_body={
				"meta_data": [
					{"key": "field_order_no", "value": field_order.name},
					{
						"key": "sales_rep",
						"value": frappe.db.get_value("Sales Rep", field_order.sales_rep, "full_name")
						or field_order.sales_rep
						or "",
					},
				]
			},
			ref=field_order,
		)

	# ------------------------------------------------------------------
	# Webhooks
	# ------------------------------------------------------------------

	def verify_webhook(self, headers, raw_body: bytes) -> bool:
		signature = (headers.get("X-WC-Webhook-Signature") or "").encode()
		if not signature:
			return False
		# WooCommerce sends the HMAC-SHA256 of the body as a hex digest.
		expected = hmac.new(self.consumer_secret.encode(), raw_body, hashlib.sha256).hexdigest().encode()
		return hmac.compare_digest(expected, signature)

	def parse_webhook(self, headers, raw_body: bytes) -> dict:
		import json as _json

		topic = headers.get("X-WC-Webhook-Topic") or ""
		payload = _json.loads(raw_body.decode() or "{}")
		return {
			"topic": _map_topic(topic, payload),
			"platform_topic": topic,
			"event_id": headers.get("X-WC-Webhook-ID") or headers.get("X-WC-Delivery-ID"),
			"updated_at": payload.get("date_modified") or payload.get("date_created"),
			"payload": payload,
		}

	def handle_stock_event(self, payload: dict) -> dict | None:
		"""A product (or variation) payload carries its own stock_quantity."""
		if "stock_quantity" not in payload:
			return None
		return {
			"external_inventory_id": str(payload.get("variation_id") or payload.get("id") or ""),
			"location_id": STOCK_LOCATION_ID,
			"available": frappe.utils.flt(payload.get("stock_quantity")),
		}

	def handle_product_event(self, payload: dict) -> list[dict] | None:
		variants = payload.get("variations")
		if not variants:
			return None
		# Variation ids only: prices are fetched per id by the sync (get_product).
		return [{"external_variant_id": str(v), "price": None, "compare_at_price": None} for v in variants]

	def order_id_from_event(self, payload: dict) -> str | None:
		return str(payload["id"]) if payload.get("id") else None

	# ------------------------------------------------------------------
	# Internals
	# ------------------------------------------------------------------

	def _variation_items(self, product: dict) -> list[dict]:
		data = self._request(
			"GET",
			f"/products/{product['id']}/variations",
			operation="Product Sync",
			params={"per_page": PAGE_SIZE},
		)
		items = []
		for variation in data:
			item = self._normalize_product(product, variation)
			if item:
				items.append(item)
		return items

	def _normalize_product(self, product: dict, variation: dict | None = None) -> dict | None:
		row = variation or product
		if row.get("status") == "trash":
			return None
		inventory_id = str(row["id"])
		stock = row.get("stock_quantity")
		if stock is not None:
			self._stock_cache[inventory_id] = frappe.utils.flt(stock)

		sale = frappe.utils.flt(row.get("sale_price") or 0)
		regular = frappe.utils.flt(row.get("regular_price") or 0)
		price = sale or regular
		return {
			"external_product_id": str(product["id"]),
			"external_variant_id": inventory_id,
			"external_inventory_id": inventory_id,
			"product_title": product.get("name") or "",
			"variant_title": _variation_title(variation),
			"sku": row.get("sku") or None,
			"barcode": None,
			"price": price,
			"compare_at_price": (regular or None) if sale and regular else None,
			"collection": None,
			"image_url": _first_image(product, variation),
			"status": "Active" if row.get("status") in (None, "publish") else "Draft",
			"track_inventory": bool(row.get("manage_stock")),
		}

	def _update_customer(self, retailer: Document) -> dict:
		body = _customer_body(retailer)
		self._request(
			"PUT", f"/customers/{retailer.external_customer_id}", operation="Update Customer", json_body=body, ref=retailer
		)
		return {"id": str(retailer.external_customer_id), "warning": None}

	def _find_customer_by_email(self, email: str, retailer: Document) -> dict | None:
		found = self._request(
			"GET", "/customers", operation="Create Customer", params={"email": email}, ref=retailer
		)
		return found[0] if found else None


# ----------------------------------------------------------------------
# Payload helpers (module level so they read as data, not methods)
# ----------------------------------------------------------------------


def _customer_body(retailer: Document) -> dict:
	# WooCommerce requires an email; a placeholder keeps the push working when
	# the retailer has none (the retailer shows a sync warning about it).
	return {
		"email": (retailer.email or "").strip().lower() or _placeholder_email(retailer),
		"first_name": _split_name(retailer)[0],
		"last_name": _split_name(retailer)[1],
		"billing": _retailer_address(retailer),
		"shipping": _retailer_address(retailer),
	}


def _email_warning(retailer: Document) -> str | None:
	if not (retailer.email or "").strip():
		return _("No email on the customer; created with a placeholder email on the store")
	return None


def _placeholder_email(retailer: Document) -> str:
	return f"{frappe.scrub(retailer.name)}@customers.fieldsales.local"


def _split_name(retailer: Document) -> tuple[str, str]:
	full = (retailer.retailer_name or retailer.name or "").strip()
	parts = full.split(" ", 1)
	return (parts[0], parts[1] if len(parts) > 1 else "")


_ADDRESS_FIELDS = [
	"retailer_name",
	"phone",
	"address_line1",
	"address_line2",
	"city",
	"state",
	"pincode",
	"country",
]


def _retailer_address(doc: Document) -> dict:
	"""Billing/shipping address block, always read off the Retailer behind the doc."""
	retailer = (
		doc
		if doc.doctype == "Retailer"
		else frappe.db.get_value("Retailer", doc.retailer, _ADDRESS_FIELDS, as_dict=True)
	) or {}
	first, last = _split_name(retailer)
	return {
		"first_name": first,
		"last_name": last,
		"address_1": retailer.get("address_line1") or "",
		"address_2": retailer.get("address_line2") or "",
		"city": retailer.get("city") or "",
		"state": retailer.get("state") or "",
		"postcode": retailer.get("pincode") or "",
		"country": retailer.get("country") or "",
		"phone": retailer.get("phone") or "",
	}


def _line_item(row) -> dict:
	"""One Field Order Item as a Woo line item; price is sent so totals match the order."""
	item = {
		"quantity": frappe.utils.flt(row.qty),
		"price": f"{frappe.utils.flt(row.rate):.2f}",
	}
	variant_id = getattr(row, "external_variant_id", None)
	if variant_id:
		item["variation_id"] = int(variant_id)
		product_id = frappe.db.get_value("Platform Product", row.product, "external_product_id")
		if product_id:
			item["product_id"] = int(product_id)
	elif row.product:
		product_id = frappe.db.get_value("Platform Product", row.product, "external_product_id")
		if product_id:
			item["product_id"] = int(product_id)
		if row.sku:
			item["sku"] = row.sku
	return item


def _tax_line(field_order: Document) -> list[dict]:
	return [
		{
			"rate_id": None,
			"label": _("Tax"),
			"compound": False,
			"tax_total": f"{frappe.utils.flt(field_order.tax_amount):.2f}",
			"shipping_tax_total": "0.00",
		}
	]


def _variation_title(variation: dict | None) -> str | None:
	if not variation:
		return None
	parts = [
		f"{a.get('option')}" for a in variation.get("attributes") or [] if a.get("option")
	]
	return " / ".join(parts) or None


def _first_image(product: dict, variation: dict | None) -> str | None:
	image = ((variation or {}).get("image")) or ((product.get("images") or [{}])[0])
	return image.get("src") if image else None


def _normalize_order(order: dict) -> dict:
	status = order.get("status", "")
	refunded_qty: dict[str, float] = {}
	refunds = []
	for refund in order.get("refunds") or []:
		lines = []
		# Refund lines are fetched lazily only when the commission engine needs them;
		# the list gives the totals.
		refunds.append({"refund_id": str(refund.get("id")), "lines": lines})

	lines = []
	for item in order.get("line_items") or []:
		line_id = str(item.get("id"))
		qty = frappe.utils.flt(item.get("quantity"))
		lines.append(
			{
				"line_id": line_id,
				"external_variant_id": str(item.get("variation_id") or item.get("product_id") or "") or None,
				"qty": qty,
				"current_qty": qty - refunded_qty.get(line_id, 0),
				"unit_price": frappe.utils.flt(item.get("price")),
				"discount": frappe.utils.flt(item.get("subtotal")) - frappe.utils.flt(item.get("total")),
				"product_type": None,
				"tags": [],
			}
		)

	financial = "paid" if order.get("date_paid") else "pending"
	fulfillment = "fulfilled" if status == "completed" else "unfulfilled"
	return {
		"external_order_id": str(order.get("id")),
		"currency": order.get("currency") or "",
		"financial_status": financial,
		"fulfillment_status": fulfillment,
		"cancelled": status == "cancelled",
		"lines": lines,
		"refunds": refunds,
	}


def _map_topic(topic: str, payload: dict) -> str:
	mapping = {
		"product.updated": PRODUCT_UPDATE_TOPIC,
		"product.deleted": PRODUCT_DELETE_TOPIC,
		"product.restored": PRODUCT_UPDATE_TOPIC,
		"order.updated": ORDER_PAID_TOPIC,
		"order.restored": ORDER_PAID_TOPIC,
		"order.deleted": ORDER_CANCELLED_TOPIC,
	}
	if topic == "order.updated" and payload.get("status") == "completed":
		return ORDER_FULFILLED_TOPIC
	if topic == "order.updated" and payload.get("status") == "cancelled":
		return ORDER_CANCELLED_TOPIC
	return mapping.get(topic, topic or "other")


def _error_message(response) -> str:
	try:
		data = response.json()
	except ValueError:
		return (response.text or "")[:200]
	if isinstance(data, dict):
		if data.get("message"):
			return str(data["message"])[:200]
		for item in data.get("data", {}).get("params", {}).values() if isinstance(data.get("data"), dict) else []:
			if item:
				return str(item)[:200]
	return (response.text or "")[:200]


def _error_code(response) -> str:
	try:
		return (response.json().get("code") or "HTTP_ERROR")[:60]
	except ValueError:
		return "HTTP_ERROR"


def _email_taken_code(error: ConnectorError) -> bool:
	"""WooCommerce rejects a duplicate email with HTTP 400 and an email error."""
	return error.http_status == 400 and "email" in error.message.lower()


def _safe_payload(payload) -> str | None:
	if not payload:
		return None
	if not isinstance(payload, dict):
		return _truncate(str(payload))
	safe = {k: v for k, v in payload.items() if "secret" not in k.lower() and "key" not in k.lower()}
	return _truncate(frappe.as_json(safe))


def _truncate(text: str) -> str:
	return (text or "")[:5000]


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)
