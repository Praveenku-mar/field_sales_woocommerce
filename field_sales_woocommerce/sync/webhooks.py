# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral webhook processing: store events -> Platform Product.

``field_sales_woocommerce.api.webhooks.receive`` verifies and parses a delivery, then
queues ``process_event`` with the neutral event (see ``connectors.base``).
This module talks to the store only through ``get_connector()``.
"""

import time

import frappe
from frappe.utils import convert_utc_to_system_timezone, flt, get_datetime, now_datetime

from field_sales_woocommerce.connectors import get_connector
from field_sales_woocommerce.commission.engine import reconcile_order
from field_sales_woocommerce.connectors.base import (
	ORDER_TOPICS,
	PRODUCT_DELETE_TOPIC,
	PRODUCT_UPDATE_TOPIC,
	STOCK_TOPIC,
)
from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync, log_sync_durable
from field_sales_woocommerce.sync.products import _archive, sync_one_product
from field_sales_woocommerce.sync.stock import get_sell_locations, set_location_stock

REALTIME_EVENT = "stock_updated"
PRICE_REALTIME_EVENT = "product_price_updated"


def process_event(platform: str, webhook: dict) -> dict:
	"""
	Apply one webhook event. Runs as a background job.

	A failure writes a Failed Sync Log (kept even though the job rolls back)
	and is re-raised so the job shows as failed.

	:param platform: Platform the event came from (``Store Connection.platform``).
	:param webhook: Neutral event from ``BaseConnector.parse_webhook``. Not named ``event``:
		``frappe.enqueue`` reserves that keyword and would swallow it.
	:return: Small summary of what was done (also the Sync Log's response).
	"""
	event = webhook
	start = time.monotonic()
	try:
		conn = get_connector(require_enabled=False)
		topic = event.get("topic")
		if topic == STOCK_TOPIC:
			result = _process_stock(conn, platform, event)
		elif topic == PRODUCT_UPDATE_TOPIC:
			result = _process_product_update(conn, platform, event)
		elif topic == PRODUCT_DELETE_TOPIC:
			result = _process_product_delete(platform, event)
		elif topic in ORDER_TOPICS:
			result = _process_order_event(conn, event)
		else:
			result = {"note": "Ignored: unhandled topic"}
	except Exception as e:
		log_sync_durable(
			**_log_fields(platform, event, start),
			status="Failed",
			request_payload=frappe.as_json(event.get("payload")),
			error_code=getattr(e, "code", None) or type(e).__name__,
			error_message=getattr(e, "message", None) or str(e),
		)
		raise

	log_sync(
		**_log_fields(platform, event, start),
		status="Success",
		response_payload=frappe.as_json(result),
	)
	return result


def _process_stock(conn, platform: str, event: dict) -> dict:
	"""Set the quantity of the matching tracked Platform Products at the event's location, unless it is stale or not sold from."""
	stock = conn.handle_stock_event(event.get("payload") or {})
	if not stock:
		return {"note": "Ignored: not a stock event"}

	location = next((loc for loc in get_sell_locations() if loc["location_id"] == stock["location_id"]), None)
	if not location:
		return {"note": "Ignored: location not sold from"}

	products = frappe.get_all(
		"Platform Product",
		filters={"platform": platform, "external_inventory_id": stock["external_inventory_id"], "track_inventory": 1},
		fields=["name", "stock_updated_at"],
	)
	if not products:
		return {"note": "Ignored: no matching product"}

	# Events are ordered per (product, location): by the Product Stock row's time, or, at the
	# default location before it has a row, the Platform Product's.
	rows = {
		r.product: r.stock_updated_at
		for r in frappe.get_all(
			"Product Stock",
			filters={"product": ("in", [p.name for p in products]), "location_id": location["location_id"]},
			fields=["product", "stock_updated_at"],
		)
	}

	event_time = _to_system_datetime(event.get("updated_at"))
	updated = []
	for product in products:
		last_updated = rows.get(product.name) or (product.stock_updated_at if location["is_default"] else None)
		if event_time and last_updated and event_time < get_datetime(last_updated):
			continue
		set_location_stock(
			product.name,
			location["location_id"],
			stock["available"],
			location["location_name"],
			event_time or now_datetime(),
		)
		if location["is_default"]:
			frappe.db.set_value("Platform Product", product.name, "last_synced", now_datetime(), update_modified=False)
		updated.append(product.name)

	if not updated:
		return {"note": "Ignored: older than stored stock"}

	frappe.publish_realtime(
		REALTIME_EVENT,
		{"products": updated, "available_qty": stock["available"], "location_id": location["location_id"]},
		doctype="Platform Product",
	)
	return {"updated": len(updated), "available_qty": stock["available"], "location_id": location["location_id"]}


def _process_product_update(conn, platform: str, event: dict) -> dict:
	"""
	Apply the payload's variant prices, re-fetch the product's variants and upsert them,
	then announce price changes to open order screens.

	The re-fetch is the store's current state, so a stale (out-of-order) delivery is
	corrected in the same job, and the announced price is the one finally stored.
	"""
	payload = event.get("payload") or {}
	external_product_id = _plain_id(payload.get("id"))
	if not external_product_id:
		return {"note": "Ignored: no product id"}

	before = _apply_prices(platform, conn.handle_product_event(payload) or [])
	result = sync_one_product(conn, external_product_id)
	result["price_changed"] = _publish_price_changes(before)
	return result


def _apply_prices(platform: str, prices: list[dict]) -> dict[str, tuple[float, float]]:
	"""
	Write changed ``price`` / ``compare_at_price`` (and ``last_synced``) to the matching
	Platform Products; unchanged ones are not written.

	:return: ``{Platform Product name: (price, compare_at_price)}`` as stored before, for every match.
	"""
	by_variant = {p["external_variant_id"]: p for p in prices}
	if not by_variant:
		return {}

	products = frappe.get_all(
		"Platform Product",
		filters={"platform": platform, "external_variant_id": ("in", list(by_variant))},
		fields=["name", "external_variant_id", "price", "compare_at_price"],
	)
	before = {}
	for product in products:
		before[product.name] = (flt(product.price), flt(product.compare_at_price))
		new = by_variant[product.external_variant_id]
		if (flt(new["price"]), flt(new["compare_at_price"])) == before[product.name]:
			continue
		frappe.db.set_value(
			"Platform Product",
			product.name,
			{"price": flt(new["price"]), "compare_at_price": flt(new["compare_at_price"]), "last_synced": now_datetime()},
			update_modified=False,
		)
	return before


def _publish_price_changes(before: dict[str, tuple[float, float]]) -> int:
	"""Publish ``PRICE_REALTIME_EVENT`` (after commit) for each product whose stored price now differs from ``before``."""
	if not before:
		return 0
	changed = 0
	for product in frappe.get_all(
		"Platform Product", filters={"name": ("in", list(before))}, fields=["name", "price", "compare_at_price"]
	):
		if (flt(product.price), flt(product.compare_at_price)) == before[product.name]:
			continue
		frappe.publish_realtime(
			PRICE_REALTIME_EVENT,
			{"product": product.name, "price": flt(product.price), "compare_at_price": flt(product.compare_at_price)},
			doctype="Platform Product",
			after_commit=True,
		)
		changed += 1
	return changed


def _process_product_delete(platform: str, event: dict) -> dict:
	"""Archive every variant of the deleted product."""
	external_product_id = _plain_id((event.get("payload") or {}).get("id"))
	if not external_product_id:
		return {"note": "Ignored: no product id"}

	names = frappe.get_all(
		"Platform Product",
		filters={"platform": platform, "external_product_id": external_product_id},
		pluck="name",
	)
	return {"archived": _archive(names)}


def _process_order_event(conn, event: dict) -> dict:
	"""Paid / fulfilled / cancelled / refund: re-read the order and reconcile its commission entries."""
	external_order_id = conn.order_id_from_event(event.get("payload") or {})
	if not external_order_id:
		return {"note": "Ignored: no order id"}

	return reconcile_order(external_order_id)


def _log_fields(platform: str, event: dict, start: float) -> dict:
	"""Sync Log fields shared by the success and failure entries."""
	return {
		"platform": platform,
		"operation": "Webhook",
		"endpoint": event.get("platform_topic") or event.get("topic"),
		"external_id": event.get("event_id"),
		"duration_ms": int((time.monotonic() - start) * 1000),
	}


def _plain_id(value) -> str | None:
	"""Webhook ids may arrive as numbers or GIDs; store them as plain strings."""
	return str(value).rsplit("/", 1)[-1] if value else None


def _to_system_datetime(value: str | None):
	"""ISO timestamp (usually UTC, e.g. ``...Z``) -> naive datetime in the system timezone; ``None`` if unparseable."""
	if not value:
		return None
	try:
		parsed = get_datetime(value)
	except Exception:
		return None
	if parsed.tzinfo is None:
		return parsed
	return convert_utc_to_system_timezone(parsed).replace(tzinfo=None)
