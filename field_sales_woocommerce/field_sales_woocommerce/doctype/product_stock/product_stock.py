# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProductStock(Document):
	"""Stock of one Platform Product at one store location; the name ``{product}-{location_id}`` keeps it one row per pair."""

	pass


def on_doctype_update():
	# Live stock, stock webhooks and the order screens read rows by product (often several)
	# at one location; without this every read scanned the whole table.
	frappe.db.add_index("Product Stock", ["product", "location_id"])
