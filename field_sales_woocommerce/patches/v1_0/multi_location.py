# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime


def execute():
	"""
	Move a single-location setup to multiple locations. Safe to run again.

	- The Store Connection default location becomes a ``Sell from here`` Store Location row.
	- Every tracked Platform Product gets a Product Stock row at the default location from its
	  current ``available_qty`` (rows that exist are left alone).
	- Field Orders without a location get the default one.
	"""
	default_id, default_name = frappe.db.get_value(
		"Store Connection", "Store Connection", ["default_location_id", "default_location_name"]
	) or (None, None)
	if not default_id:
		return

	rows = frappe.get_all(
		"Store Location",
		filters={"parent": "Store Connection", "parenttype": "Store Connection", "location_id": default_id},
		fields=["name", "sell_from"],
	)
	if not rows:
		settings = frappe.get_doc("Store Connection")
		settings.append(
			"locations", {"location_id": default_id, "location_name": default_name or default_id, "sell_from": 1}
		)
		settings.flags.ignore_validate = True
		settings.save(ignore_permissions=True)
	elif not rows[0].sell_from:
		frappe.db.set_value("Store Location", rows[0].name, "sell_from", 1, update_modified=False)

	existing = set(frappe.get_all("Product Stock", filters={"location_id": default_id}, pluck="product"))
	now = now_datetime()
	for product in frappe.get_all(
		"Platform Product",
		filters={"track_inventory": 1},
		fields=["name", "available_qty", "stock_updated_at"],
	):
		if product.name in existing:
			continue
		frappe.get_doc(
			{
				"doctype": "Product Stock",
				"product": product.name,
				"location_id": default_id,
				"location_name": default_name or default_id,
				"available_qty": product.available_qty or 0,
				"stock_updated_at": product.stock_updated_at or now,
			}
		).insert(ignore_permissions=True)

	frappe.db.sql(
		"""
		UPDATE `tabField Order`
		SET location_id = %s, location_name = %s
		WHERE IFNULL(location_id, '') = ''
		""",
		(default_id, default_name or default_id),
	)
