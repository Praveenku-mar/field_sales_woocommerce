# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The words of each push notification.

A push is shown on the lock screen, so it never carries a shop or owner name, a phone
number or an amount (docs/security/threat_model.md). The full bell subject is shown once
the app is opened and signed in.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate


def _platform() -> str:
	return frappe.db.get_single_value("Store Connection", "platform") or _("the store")


def render(kind: str, docname: str | None, params: dict | None) -> tuple[str, str]:
	"""
	:return: ``(title, body)`` for a push of ``kind``.
	"""
	params = params or {}
	if kind == "visit_assigned":
		return _("New visit assigned"), _("Visit on {0}. Tap to see the details.").format(
			formatdate(params.get("date"))
		)
	if kind == "visit_rescheduled":
		return _("Visit rescheduled"), _("Your visit is now on {0}.").format(formatdate(params.get("date")))
	if kind == "order_sync_failed":
		return _("Order failed to sync"), _("Order {0} could not be sent to {1}.").format(docname, _platform())
	if kind == "shop_sync_failed":
		return _("Shop failed to sync"), _("A shop could not be sent to {0}.").format(_platform())
	if kind == "payment_received":
		return _("Payment received"), _("Payment received for order {0}.").format(docname)
	if kind == "order_synced":
		return _("Order confirmed"), _("Order {0} is confirmed in {1}.").format(docname, _platform())
	if kind == "statement_approved":
		return _("Commission statement approved"), _("Your statement {0} has been approved.").format(docname)
	if kind == "statement_paid":
		return _("Commission paid"), _("Your statement {0} has been marked paid.").format(docname)
	if kind == "low_stock":
		qty = flt(params.get("qty"))
		qty_label = cint(qty) if qty == cint(qty) else qty
		return _("Low stock"), _("{0} has {1} left.").format(params.get("product") or docname, qty_label)
	if kind == "low_stock_digest":
		return _("Low stock"), _("{0} products are low on stock.").format(cint(params.get("count")))
	if kind == "test":
		return _("Field Sales"), _("Push notifications are working on this device.")
	return _("Field Sales"), _("You have a new notification.")
