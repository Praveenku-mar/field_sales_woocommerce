# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The columns the hot lookups filter by are indexed (store webhooks, refund webhooks, customer import,
live stock). The same file runs in every Field Sales edition.
"""

import frappe
from frappe.tests import IntegrationTestCase

# (table, leading columns of an index that must exist)
EXPECTED = (
	("Product Stock", ("product", "location_id")),
	("Field Order", ("external_order_id",)),
	("Retailer", ("external_customer_id",)),
	("Payment Transaction", ("razorpay_refund_id",)),
)


class TestIndexes(IntegrationTestCase):
	def index_columns(self, doctype: str) -> list[tuple[str, ...]]:
		rows = frappe.db.sql(
			"""
			select index_name, column_name from information_schema.statistics
			where table_schema = database() and table_name = %s
			order by index_name, seq_in_index
			""",
			f"tab{doctype}",
		)
		indexes: dict[str, list[str]] = {}
		for index_name, column in rows:
			indexes.setdefault(index_name, []).append(column)
		return [tuple(columns) for columns in indexes.values()]

	def test_lookup_columns_are_indexed(self):
		for doctype, columns in EXPECTED:
			with self.subTest(doctype=doctype, columns=columns):
				self.assertTrue(
					any(index[: len(columns)] == columns for index in self.index_columns(doctype)),
					f"No index on {doctype} starting with {columns}",
				)
