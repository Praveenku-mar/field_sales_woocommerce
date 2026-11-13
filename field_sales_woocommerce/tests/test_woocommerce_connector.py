# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Unit tests for the WooCommerce connector's platform-independent logic
(normalization, URL handling, webhook verification, topic mapping, event
handlers). No network: the HTTP layer is never called."""

import base64
import hashlib
import hmac
import json

from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from field_sales_woocommerce.connectors.woocommerce import (
	STOCK_LOCATION_ID,
	WEBHOOK_TOPICS,
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


def wc_signature(body: bytes, secret: bytes = b"cs_secret") -> str:
	"""As WooCommerce signs a delivery: base64 of the raw HMAC-SHA256 of the body."""
	return base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()


class TestWebhookVerification(IntegrationTestCase):
	def test_signature_from_woocommerce_verifies(self):
		# Made by WooCommerce's own formula, in PHP: base64_encode(hash_hmac("sha256", $body, $secret, true)).
		body = b'{"id": 12}'
		signature = "N4S3fTig5LmIFDpC4GDdPnpD4/MF9V77h/1vtg+ManA="
		self.assertEqual(wc_signature(body), signature)
		self.assertTrue(make_connector().verify_webhook({"X-WC-Webhook-Signature": signature}, body))

	def test_hex_digest_is_not_a_woocommerce_signature(self):
		body = json.dumps({"id": 12}).encode()
		signature = hmac.new(b"cs_secret", body, hashlib.sha256).hexdigest()
		self.assertFalse(make_connector().verify_webhook({"X-WC-Webhook-Signature": signature}, body))

	def test_tampered_body_or_wrong_secret_fails(self):
		conn = make_connector()
		body = json.dumps({"id": 12}).encode()
		self.assertFalse(conn.verify_webhook({"X-WC-Webhook-Signature": wc_signature(body, b"other")}, body))
		self.assertFalse(conn.verify_webhook({"X-WC-Webhook-Signature": wc_signature(body)}, body + b"x"))

	def test_missing_signature_fails(self):
		self.assertFalse(make_connector().verify_webhook({}, b"{}"))

	def test_ping_is_the_unsigned_webhook_id_post(self):
		conn = make_connector()
		self.assertTrue(conn.is_ping({}, b"webhook_id=14"))
		self.assertFalse(conn.is_ping({"X-WC-Webhook-Signature": "x"}, b"webhook_id=14"))
		self.assertFalse(conn.is_ping({}, b'{"id": 14}'))
		self.assertFalse(conn.is_ping({}, b"webhook_id=14&x=1"))


class TestParseWebhook(IntegrationTestCase):
	def _parse(self, topic: str, payload: dict, delivery_id: str | None = "d-1") -> dict:
		conn = make_connector()
		body = json.dumps(payload).encode()
		headers = {
			"X-WC-Webhook-Signature": wc_signature(body),
			"X-WC-Webhook-Topic": topic,
			"X-WC-Webhook-ID": "7",
		}
		if delivery_id:
			headers["X-WC-Webhook-Delivery-ID"] = delivery_id
		return conn.parse_webhook(headers, body)

	def test_event_id_is_the_delivery_not_the_webhook(self):
		# X-WC-Webhook-ID is the same on every delivery of a webhook: as the dedup key it
		# dropped every delivery after the first for 48 hours.
		self.assertEqual(self._parse("product.updated", {"id": 1}, "d-1")["event_id"], "d-1")
		self.assertEqual(self._parse("product.updated", {"id": 1}, "d-2")["event_id"], "d-2")
		self.assertIsNone(self._parse("product.updated", {"id": 1}, None)["event_id"])

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
				self.assertEqual(event["event_id"], "d-1")

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


class TestStoreTimezone(IntegrationTestCase):
	"""The store's zone comes from WordPress settings, not PHP's default (always UTC)."""

	def index(self, payload, status=200, html=False):
		response = MagicMock(ok=status < 400, status_code=status)
		response.headers = {"Content-Type": "text/html" if html else "application/json"}
		response.text = "<html></html>" if html else json.dumps(payload)
		response.json.return_value = payload
		return response

	def zone(self, *responses, fallback="UTC"):
		with patch("field_sales_woocommerce.connectors.woocommerce.requests.get", side_effect=list(responses)) as get:
			result = make_connector()._store_timezone(fallback)
		return result, get

	def test_named_zone_wins(self):
		result, get = self.zone(self.index({"timezone_string": "Asia/Kolkata", "gmt_offset": 5.5}))
		self.assertEqual(result, "Asia/Kolkata")
		self.assertEqual(get.call_args.args[0], "http://store.example/wp-json/")
		self.assertNotIn("auth", get.call_args.kwargs)  # public index, no credentials sent

	def test_whole_hour_offset_maps_to_an_etc_zone(self):
		self.assertEqual(self.zone(self.index({"timezone_string": "", "gmt_offset": 4}))[0], "Etc/GMT-4")
		self.assertEqual(self.zone(self.index({"timezone_string": "", "gmt_offset": "-3"}))[0], "Etc/GMT+3")
		self.assertEqual(self.zone(self.index({"timezone_string": "", "gmt_offset": 0}))[0], "UTC")

	def test_fractional_offset_or_failure_keeps_the_fallback(self):
		self.assertEqual(self.zone(self.index({"timezone_string": "", "gmt_offset": 5.5}))[0], "UTC")
		self.assertEqual(
			self.zone(requests.exceptions.ConnectionError(), requests.exceptions.ConnectionError())[0], "UTC"
		)

	def test_html_index_falls_back_to_rest_route(self):
		result, get = self.zone(self.index({}, html=True), self.index({"timezone_string": "Europe/Paris"}))
		self.assertEqual(result, "Europe/Paris")
		self.assertEqual(get.call_args.kwargs["params"], {"rest_route": "/"})


class TestHelpers(IntegrationTestCase):
	def test_variation_title(self):
		self.assertEqual(_variation_title({"attributes": [{"option": "S"}, {"option": "Red"}]}), "S / Red")
		self.assertIsNone(_variation_title(None))
		self.assertIsNone(_variation_title({"attributes": []}))


class TestEnsureWebhooks(IntegrationTestCase):
	URL = "https://sales.example/api/method/field_sales_woocommerce.api.webhooks.receive"

	def run_with(self, existing: list[dict]):
		conn = make_connector()
		calls = []

		def fake_request(method, path, operation, params=None, json_body=None, ref=None):
			calls.append((method, path, json_body))
			if method == "GET":
				return existing if (params or {}).get("page", 1) == 1 else []
			return {"id": 99}

		with patch.object(conn, "_request", side_effect=fake_request):
			result = conn.ensure_webhooks(self.URL)
		return result, calls

	def test_creates_every_topic_signed_with_the_consumer_secret(self):
		result, calls = self.run_with([])
		self.assertEqual(result, {topic: "created" for topic in WEBHOOK_TOPICS})
		posts = [c for c in calls if c[0] == "POST"]
		self.assertEqual([c[2]["topic"] for c in posts], list(WEBHOOK_TOPICS))
		for _method, path, body in posts:
			self.assertEqual(path, "/webhooks")
			self.assertEqual(body["delivery_url"], self.URL)
			self.assertEqual(body["secret"], "cs_secret")
			self.assertEqual(body["status"], "active")

	def test_reuses_this_sites_webhooks_and_leaves_others_alone(self):
		existing = [
			{"id": 1, "topic": "product.updated", "delivery_url": self.URL + "/", "status": "disabled"},
			{"id": 2, "topic": "order.updated", "delivery_url": self.URL.upper(), "status": "active"},
			{"id": 3, "topic": "product.deleted", "delivery_url": "https://other.example/hook", "status": "active"},
		]
		result, calls = self.run_with(existing)
		self.assertEqual(result["product.updated"], "reactivated")
		self.assertEqual(result["order.updated"], "updated")
		self.assertEqual(result["product.deleted"], "created")
		puts = {c[1]: c[2] for c in calls if c[0] == "PUT"}
		self.assertEqual(set(puts), {"/webhooks/1", "/webhooks/2"})
		for body in puts.values():
			self.assertEqual(body, {"status": "active", "secret": "cs_secret"})
		self.assertNotIn("/webhooks/3", {c[1] for c in calls})

	def test_lists_every_page(self):
		conn = make_connector()
		pages = {1: [{"id": i, "topic": "x", "delivery_url": "u"} for i in range(100)], 2: [{"id": 100}]}
		with patch.object(conn, "_request", side_effect=lambda m, p, operation, params=None, **k: pages.get(params["page"], [])):
			self.assertEqual(len(conn._list_webhooks()), 101)


class TestWebhookEndpoint(IntegrationTestCase):
	"""api.webhooks.receive and the registration Test Connection runs."""

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()

	def receive(self, body: bytes, headers: dict):
		from field_sales_woocommerce.api import webhooks

		conn = make_connector()
		conn.settings.enabled = 1
		request = MagicMock()
		request.get_data.return_value = body
		request.headers = headers
		with (
			patch.object(webhooks, "get_connector", return_value=conn),
			patch.object(frappe, "request", request, create=True),
			patch.object(webhooks, "_log_invalid_signature") as logged,
			patch.object(webhooks.frappe, "enqueue") as enqueue,
		):
			result = webhooks.receive()
		return frappe.local.response.http_status_code, result, logged, enqueue

	def test_ping_is_answered_without_logging(self):
		status, result, logged, enqueue = self.receive(b"webhook_id=14", {})
		self.assertEqual((status, result["message"]), (200, "ping"))
		logged.assert_not_called()
		enqueue.assert_not_called()

	def test_signed_deliveries_are_each_queued(self):
		body = json.dumps({"id": 29}).encode()
		for delivery in (f"d-{frappe.generate_hash(length=8)}", f"d-{frappe.generate_hash(length=8)}"):
			headers = {
				"X-WC-Webhook-Signature": wc_signature(body),
				"X-WC-Webhook-Topic": "product.updated",
				"X-WC-Webhook-ID": "7",
				"X-WC-Webhook-Delivery-ID": delivery,
			}
			status, result, _logged, enqueue = self.receive(body, headers)
			self.assertEqual((status, result["message"]), (200, "ok"))
			enqueue.assert_called_once()

	def test_register_webhooks_reports_failure_without_raising(self):
		from field_sales_woocommerce import api
		from field_sales_woocommerce.connectors import ConnectorError

		conn = make_connector()
		with patch.object(conn, "ensure_webhooks", side_effect=ConnectorError("Forbidden", code="AUTH")):
			result = api._register_webhooks(conn)
		self.assertFalse(result["ok"])
		self.assertEqual(result["message"], "Forbidden")
		self.assertTrue(result["delivery_url"].endswith(api.WEBHOOK_PATH))
		with patch.object(conn, "ensure_webhooks", return_value={"product.updated": "created"}):
			result = api._register_webhooks(conn)
		self.assertEqual(result["topics"], {"product.updated": "created"})
