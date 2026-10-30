# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Unit tests for the WooCommerce connector's platform-independent logic
(normalization, URL handling, webhook verification, topic mapping, event
handlers). No network: the HTTP layer is never called."""

import hashlib
import hmac
import json

import frappe
from frappe.tests import IntegrationTestCase

from field_sales_woocommerce.connector import (
	STOCK_LOCATION_ID,
	WooCommerceConnector,
	_variation_title,
	_map_topic,
	_normalize_order,
)


def make_connector() -> WooCommerceConnector:
	"""A connector built from a scratch settings doc; nothing is fetched."""
	settings = frappe.new_doc("Store Connection")
	settings.platform = "WooCommerce"
	settings.store_url = "http://store.example"
	settings.woo_consumer_key = "ck_test"
	settings.woo_consumer_secret = "cs_secret"
	return WooCommerceConnector(settings)


class TestNormalizeStoreUrl(IntegrationTestCase):
	def test_adds_scheme_and_strips_slash(self):
		self.assertEqual(
			WooCommerceConnector.normalize_store_url("store.example/"),
			"https://store.example",
		)
		self.assertEqual(
			WooCommerceConnector.normalize_store_url("http://store.example/"),
			"http://store.example",
		)

	def test_empty_url_throws(self):
		self.assertRaises(frappe.ValidationError, WooCommerceConnector.normalize_store_url, "  ")


class TestNormalizeProduct(IntegrationTestCase):
	def test_simple_product(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 12, "name": "Table", "status": "publish", "sku": "TBL", "regular_price": "25000", "stock_quantity": 97, "manage_stock": True}
		)
		self.assertEqual(item["product_title"], "Table")
		self.assertEqual(item["external_variant_id"], "12")
		self.assertEqual(item["price"], 25000)
		self.assertIsNone(item["compare_at_price"])
		self.assertTrue(item["track_inventory"])
		self.assertEqual(item["status"], "Active")
		# stock seen on the payload is cached for get_stock
		self.assertEqual(conn._stock_cache["12"], 97)

	def test_sale_price_becomes_compare_at(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 12, "name": "Table", "status": "publish", "regular_price": "26000", "sale_price": "25000"}
		)
		self.assertEqual(item["price"], 25000)
		self.assertEqual(item["compare_at_price"], 26000)

	def test_variation_of_variable_product(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 29, "name": "Field Sales T-Shirt", "status": "publish"},
			{"id": 31, "status": "publish", "regular_price": "550", "attributes": [{"name": "Size", "option": "M"}], "manage_stock": True, "stock_quantity": 4},
		)
		self.assertEqual(item["external_product_id"], "29")
		self.assertEqual(item["external_variant_id"], "31")
		self.assertEqual(item["variant_title"], "M")

	def test_trashed_product_is_skipped(self):
		self.assertIsNone(make_connector()._normalize_product({"id": 9, "name": "X", "status": "trash"}))


class TestGetStock(IntegrationTestCase):
	def test_unknown_ids_are_zero_without_network(self):
		conn = make_connector()
		self.assertEqual(conn.get_stock(["1", "2"], STOCK_LOCATION_ID), {"1": 0, "2": 0})

	def test_cached_ids_answer_from_cache(self):
		conn = make_connector()
		conn._stock_cache["12"] = 97
		self.assertEqual(conn.get_stock(["12"], STOCK_LOCATION_ID), {"12": 97})


class TestWebhookVerification(IntegrationTestCase):
	def test_valid_signature_hex(self):
		conn = make_connector()
		body = json.dumps({"id": 12}).encode()
		signature = hmac.new(b"cs_secret", body, hashlib.sha256).hexdigest()
		self.assertTrue(conn.verify_webhook({"X-WC-Webhook-Signature": signature}, body))

	def test_tampered_body_or_wrong_secret_fails(self):
		conn = make_connector()
		body = json.dumps({"id": 12}).encode()
		signature = hmac.new(b"other", body, hashlib.sha256).hexdigest()
		self.assertFalse(conn.verify_webhook({"X-WC-Webhook-Signature": signature}, body))
		self.assertFalse(conn.verify_webhook({"X-WC-Webhook-Signature": signature}, body + b"x"))

	def test_missing_signature_fails(self):
		self.assertFalse(make_connector().verify_webhook({}, b"{}"))


class TestParseWebhook(IntegrationTestCase):
	def _parse(self, topic: str, payload: dict) -> dict:
		conn = make_connector()
		body = json.dumps(payload).encode()
		headers = {
			"X-WC-Webhook-Signature": hmac.new(b"cs_secret", body, hashlib.sha256).hexdigest(),
			"X-WC-Webhook-Topic": topic,
			"X-WC-Webhook-ID": "7",
		}
		return conn.parse_webhook(headers, body)

	def test_topics_map(self):
		cases = [
			("product.updated", {"id": 1}, "product/update"),
			("product.deleted", {"id": 1}, "product/delete"),
			("order.updated", {"id": 5, "status": "processing"}, "order/paid"),
			("order.updated", {"id": 5, "status": "completed"}, "order/fulfilled"),
			("order.updated", {"id": 5, "status": "cancelled"}, "order/cancelled"),
			("coupon.updated", {"id": 5}, "coupon.updated"),
		]
		for topic, payload, expected in cases:
			with self.subTest(topic=topic, status=payload.get("status")):
				event = self._parse(topic, payload)
				self.assertEqual(event["topic"], expected)
				self.assertEqual(event["platform_topic"], topic)
				self.assertEqual(event["event_id"], "7")

	def test_stock_event(self):
		stock = make_connector().handle_stock_event({"id": 29, "variation_id": 31, "stock_quantity": 3})
		self.assertEqual(stock, {"external_inventory_id": "31", "location_id": STOCK_LOCATION_ID, "available": 3})
		self.assertIsNone(make_connector().handle_stock_event({"id": 29}), None)

	def test_product_event_lists_variations(self):
		variants = make_connector().handle_product_event({"id": 29, "variations": [30, 31]})
		self.assertEqual([v["external_variant_id"] for v in variants], ["30", "31"])
		self.assertIsNone(make_connector().handle_product_event({"id": 12}))

	def test_order_id_from_event(self):
		conn = make_connector()
		self.assertEqual(conn.order_id_from_event({"id": 33}), "33")
		self.assertIsNone(conn.order_id_from_event({}))


class TestNormalizeOrder(IntegrationTestCase):
	def test_shapes_and_statuses(self):
		order = _normalize_order(
			{
				"id": 25,
				"currency": "INR",
				"status": "processing",
				"date_paid": "2026-10-26T10:25:48",
				"line_items": [
					{"id": 20, "product_id": 12, "variation_id": 0, "quantity": 2, "price": "25000.00", "subtotal": "50000", "total": "50000"}
				],
				"refunds": [],
			}
		)
		self.assertEqual(order["external_order_id"], "25")
		self.assertEqual(order["financial_status"], "paid")
		self.assertEqual(order["fulfillment_status"], "unfulfilled")
		self.assertFalse(order["cancelled"])
		line = order["lines"][0]
		self.assertEqual(line["qty"], 2)
		self.assertEqual(line["unit_price"], 25000)
		self.assertEqual(line["discount"], 0)

	def test_cancelled_and_fulfilled(self):
		order = _normalize_order({"id": 1, "status": "cancelled", "line_items": []})
		self.assertTrue(order["cancelled"])
		order = _normalize_order({"id": 1, "status": "completed", "line_items": []})
		self.assertEqual(order["fulfillment_status"], "fulfilled")


class TestHelpers(IntegrationTestCase):
	def test_variation_title(self):
		self.assertEqual(_variation_title({"attributes": [{"option": "S"}, {"option": "Red"}]}), "S / Red")
		self.assertIsNone(_variation_title(None))
		self.assertIsNone(_variation_title({"attributes": []}))
