# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe

from field_sales_woocommerce.connectors import ConnectorError, get_connector


def execute():
	"""
	Fill Field Order ``store_draft_name`` (e.g. #D5) for drafts pushed before it was stored.

	Best effort: with the store disconnected or unreachable nothing is changed, and the order
	page shows the draft id alone. Safe to run again.
	"""
	rows = frappe.get_all(
		"Field Order",
		filters={"external_draft_id": ("is", "set"), "store_draft_name": ("is", "not set")},
		fields=["name", "external_draft_id"],
	)
	if not rows:
		return
	try:
		names = get_connector().get_draft_names([r.external_draft_id for r in rows])
	except (ConnectorError, frappe.ValidationError):
		return
	for row in rows:
		if names.get(row.external_draft_id):
			frappe.db.set_value(
				"Field Order", row.name, "store_draft_name", names[row.external_draft_id], update_modified=False
			)
