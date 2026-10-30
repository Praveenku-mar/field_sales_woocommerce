# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-agnostic connector contract.

Core code (sync jobs, doc events, API endpoints) talks to a store only through
an instance returned by ``field_sales_woocommerce.connectors.get_connector()``. Every
platform connector subclasses ``BaseConnector`` and returns data in the
normalized shapes documented below, so callers never branch on platform.

IDs are plain platform ids (e.g. Shopify's numeric id, not its ``gid://`` form)
so they are safe in document names; each connector converts them back as needed.

Normalized product dict (one per *variant*)::

	{
		"external_product_id": str,
		"external_variant_id": str,
		"external_inventory_id": str | None,
		"product_title": str,
		"variant_title": str | None,
		"sku": str | None,
		"barcode": str | None,
		"price": float,
		"compare_at_price": float | None,
		"collection": str | None,
		"image_url": str | None,
		"status": "Active" | "Draft" | "Archived",
		"track_inventory": bool,
	}

Webhooks: the store pushes events to ``field_sales_woocommerce.api.webhooks.receive``, which
verifies and parses them through the connector, so everything after that is
platform-neutral. A parsed event is::

	{
		"topic": "stock/update" | "product/update" | "product/delete"
			| "order/paid" | "order/fulfilled" | "order/cancelled" | "refund/create" | other,
		"platform_topic": str,       # the store's own topic name, for logs
		"event_id": str | None,      # unique per delivery; used to drop duplicates
		"updated_at": str | None,    # ISO timestamp of the change, to order events
		"payload": dict,             # the store's JSON body, untouched
	}

Normalized order dict (``get_order``), used for commission. Statuses are the
platform's, mapped to these lowercase words::

	{
		"external_order_id": str,
		"currency": str,
		"financial_status": "pending" | "authorized" | "partially_paid" | "paid"
			| "partially_refunded" | "refunded" | "voided" | "expired" | "",
		"fulfillment_status": "fulfilled" | "partial" | "unfulfilled" | other lowercase,
		"cancelled": bool,
		"lines": [
			{
				"line_id": str,
				"external_variant_id": str | None,   # None for custom lines (e.g. tax)
				"qty": float,                        # all units, including refunded
				"current_qty": float,                # units not refunded / removed
				"unit_price": float,                 # before discounts and tax
				"discount": float,                   # total discount allocated to the line
				"product_type": str | None,
				"tags": [str, ...],
			},
		],
		"refunds": [
			{"refund_id": str, "lines": [{"line_id": str, "qty": float, "amount": float}]},
		],
	}

Amounts are in the order's own ``currency``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync_durable

if TYPE_CHECKING:
	from frappe.model.document import Document


#: Platform-neutral webhook topics; ``parse_webhook`` maps each platform's own topics to these.
STOCK_TOPIC = "stock/update"
PRODUCT_UPDATE_TOPIC = "product/update"
PRODUCT_DELETE_TOPIC = "product/delete"
ORDER_PAID_TOPIC = "order/paid"
ORDER_FULFILLED_TOPIC = "order/fulfilled"
ORDER_CANCELLED_TOPIC = "order/cancelled"
REFUND_CREATE_TOPIC = "refund/create"

#: Topics that make the commission engine re-read the order.
ORDER_TOPICS = (ORDER_PAID_TOPIC, ORDER_FULFILLED_TOPIC, ORDER_CANCELLED_TOPIC, REFUND_CREATE_TOPIC)

NORMALIZED_PRODUCT_KEYS: tuple[str, ...] = (
	"external_product_id",
	"external_variant_id",
	"external_inventory_id",
	"product_title",
	"variant_title",
	"sku",
	"barcode",
	"price",
	"compare_at_price",
	"collection",
	"image_url",
	"status",
	"track_inventory",
)


class ConnectorError(Exception):
	"""
	Raised by connectors for any failure talking to the store platform.

	:param message: Human-readable error, safe to show to the user.
	:param code: Short machine-readable code, e.g. ``"HTTP_ERROR"``,
		``"GRAPHQL_ERROR"``, ``"USER_ERROR"``, ``"AUTH_FAILED"``, ``"NETWORK_ERROR"``.
	:param http_status: HTTP status of the failing response, if there was one.
	:param details: Platform error entries behind the message (e.g. Shopify
		``userErrors`` as ``[{"field": [...], "message": str}]``), for callers
		that need to react to a specific field.
	:param partial: What was created on the store before the failure, so a
		retry can resume instead of duplicating it (e.g. ``{"draft_id": str}``).
	"""

	def __init__(
		self,
		message: str,
		code: str | None = None,
		http_status: int | None = None,
		details: list[dict] | None = None,
		partial: dict | None = None,
	):
		super().__init__(message)
		self.message = message
		self.code = code
		self.http_status = http_status
		self.details = details or []
		self.partial = partial or {}

	def __str__(self) -> str:
		return self.message


class BaseConnector:
	"""
	Abstract base for store connectors. Subclasses override every public method.

	:param settings: The ``Store Connection`` single document.
	"""

	#: Value written to ``Sync Log.platform``; subclasses set it.
	platform: str = ""

	def __init__(self, settings: Document):
		self.settings = settings

	@staticmethod
	def normalize_store_url(url: str | None) -> str:
		"""
		Turn what was entered as Store URL into the form this platform stores.

		Called by Store Connection's ``validate()`` through the ``CONNECTORS`` registry.
		Raises ``frappe.ValidationError`` for a URL the platform cannot use.
		"""
		return (url or "").strip()

	def test_connection(self) -> dict:
		"""
		Verify credentials and reachability.

		:return: ``{"ok": bool, "shop_name": str, "shop_domain": str, "scopes": [str, ...], "currency": str,
			"timezone": str, "country": str, "message": str}``; ``scopes`` are the permissions granted to the app (empty if
			the platform has none or on failure); ``currency`` (ISO code) and ``timezone`` (IANA name) are
			the store's, empty strings on failure; ``country`` is the shop's ISO 3166-1 alpha-2 code.
		"""
		raise NotImplementedError

	def get_shop_settings(self) -> dict:
		"""
		The store's own settings the site should follow.

		:return: ``{"currency": str, "timezone": str, "country": str}`` (ISO currency code, IANA time
			zone name, ISO 3166-1 alpha-2 country code of the shop's address; empty strings if unknown).
		:raises ConnectorError: on a platform error.
		"""
		raise NotImplementedError

	def get_locations(self) -> list[dict]:
		"""
		List stock locations / warehouses on the store.

		:return: ``[{"id": str, "name": str}, ...]``
		"""
		raise NotImplementedError

	def get_products(self, cursor: str | None = None) -> dict:
		"""
		Fetch one page of products as normalized variant dicts.

		:param cursor: Opaque cursor from the previous page's ``next_cursor``; ``None`` for the first page.
		:return: ``{"items": [normalized product, ...], "next_cursor": str | None}``
		"""
		raise NotImplementedError

	def get_stock(self, inventory_ids: list, location_id: str) -> dict:
		"""
		Get available quantity per inventory item at one location.

		:param inventory_ids: ``external_inventory_id`` values from normalized products.
		:param location_id: Platform location id (``Store Connection.default_location_id``).
		:return: ``{inventory_id: qty}``; ids with no stock record at the location map to 0.
		"""
		raise NotImplementedError

	def create_customer(self, retailer: Document) -> dict:
		"""
		Create the retailer as a customer on the store, or update it if it has
		an ``external_customer_id`` already.

		:param retailer: ``Retailer`` document.
		:return: ``{"id": str, "warning": str | None}``: the platform's customer id
			(stored as ``external_customer_id``), and a note if the customer was
			synced with some data left out (stored as ``sync_error``).
		"""
		raise NotImplementedError

	def create_order(self, field_order: Document) -> dict:
		"""
		Push a submitted Field Order to the store.

		The retailer must already exist on the store (``external_customer_id``).
		``Store Connection.order_mode`` decides whether a draft or a confirmed
		order (payment pending) is created. Totals must match the Field Order:
		its tax and discount are sent explicitly, not recalculated by the store.

		Must be safe to retry: if ``field_order.external_draft_id`` is set, or
		an order for it already exists, reuse it instead of creating another.
		On failure after something was created, the ``ConnectorError`` carries
		it in ``partial`` (e.g. ``{"draft_id": str}``).

		:param field_order: ``Field Order`` document.
		:return: ``{"draft_id": str | None, "order_id": str | None, "order_name": str | None}``
		"""
		raise NotImplementedError

	def mark_order_paid(self, field_order: Document) -> dict:
		"""
		Mark the store order (``field_order.external_order_id``) paid in full, for a payment
		taken outside the store (Razorpay). An order the store already shows as paid is left alone.

		:return: ``{"financial_status": str, "already_paid": bool}``
		:raises ConnectorError: the store cannot mark it paid (e.g. cancelled there).
		"""
		raise NotImplementedError

	def get_draft_names(self, draft_ids: list) -> dict:
		"""
		The store's own numbers for drafts (e.g. ``#D5``).

		:return: ``{draft_id: name}`` for drafts that still exist.
		"""
		return {}

	def get_completed_drafts(self, draft_ids: list) -> dict:
		"""
		Find which drafts have since been completed into orders on the store.

		:param draft_ids: ``external_draft_id`` values from ``create_order``.
		:return: ``{draft_id: order_id}`` for the completed drafts only; drafts
			still open, or deleted on the store, are left out.
		"""
		return {}

	def ensure_order_metafields(self) -> bool:
		"""
		Set up the store's order fields for the Field Order No and Sales Rep, if the platform has them.
		Safe to run again.

		:return: ``True`` when they exist (or the platform needs none).
		"""
		return True

	def set_order_metafields(self, order_id: str, field_order: Document) -> None:
		"""Write the Field Order No and Sales Rep onto an existing store order (e.g. a completed draft)."""
		return None

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		"""
		Admin URL of an order on the store (or of its draft when not yet confirmed).

		:return: URL, or ``None`` if the platform has no such page.
		"""
		return None

	def verify_webhook(self, headers, raw_body: bytes) -> bool:
		"""
		Check that a webhook request really comes from the configured store.

		:param headers: Request headers (case-insensitive mapping).
		:param raw_body: The request body exactly as received; signatures cover these bytes.
		:return: ``True`` if the signature and sender are valid; ``False`` if anything is missing or wrong.
		"""
		raise NotImplementedError

	def parse_webhook(self, headers, raw_body: bytes) -> dict:
		"""
		Turn a verified webhook request into a neutral event.

		:param headers: Request headers (case-insensitive mapping).
		:param raw_body: The request body exactly as received.
		:return: ``{"topic", "platform_topic", "event_id", "updated_at", "payload"}``
			(see the module docstring); ``topic`` is one of ``STOCK_TOPIC``,
			``PRODUCT_UPDATE_TOPIC``, ``PRODUCT_DELETE_TOPIC``, or the platform topic if unmapped.
		"""
		raise NotImplementedError

	def handle_stock_event(self, payload: dict) -> dict | None:
		"""
		Read a stock-change payload.

		:param payload: ``payload`` of a ``STOCK_TOPIC`` event.
		:return: ``{"external_inventory_id": str, "location_id": str, "available": float}``
			(plain ids), or ``None`` if the payload is not a stock event.
		"""
		raise NotImplementedError

	def handle_product_event(self, payload: dict) -> list[dict] | None:
		"""
		Read the variant prices from a product-change payload.

		:param payload: ``payload`` of a ``PRODUCT_UPDATE_TOPIC`` event.
		:return: ``[{"external_variant_id": str, "price": float, "compare_at_price": float | None}]``
			(plain ids), or ``None`` if the payload carries no variants.
		:raises ConnectorError: if a variant cannot be read (code ``"BAD_PAYLOAD"``).
		"""
		raise NotImplementedError

	def get_product(self, external_product_id: str) -> list[dict]:
		"""
		Fetch every variant of one product.

		:param external_product_id: Plain platform product id.
		:return: Normalized product dicts, one per variant; empty if the product no longer exists.
		"""
		raise NotImplementedError

	def get_order(self, external_id: str) -> dict:
		"""
		Fetch the current state of one order, for commission.

		:param external_id: Plain platform order id (``Field Order.external_order_id``).
		:return: Normalized order dict (see the module docstring); empty dict if the order no longer exists.
		"""
		raise NotImplementedError

	def order_id_from_event(self, payload: dict) -> str | None:
		"""
		Plain order id behind an order or refund webhook.

		:param payload: ``payload`` of an ``ORDER_TOPICS`` event.
		:return: The order id, or ``None`` if the payload carries none.
		"""
		raise NotImplementedError

	# ------------------------------------------------------------------
	# Shared helpers
	# ------------------------------------------------------------------

	def _write_log(self, operation: str, ref: Document | None = None, **fields) -> None:
		"""
		Write a Sync Log for one platform call; survives a rollback of the caller's transaction.

		:param operation: ``Sync Log.operation`` value.
		:param ref: Document the call was made for, if any.
		:param fields: Any other Sync Log fields (``status``, ``http_status``, ...).
		"""
		log_sync_durable(
			platform=self.platform,
			operation=operation,
			reference_doctype=ref.doctype if ref else None,
			reference_name=ref.name if ref else None,
			**fields,
		)
