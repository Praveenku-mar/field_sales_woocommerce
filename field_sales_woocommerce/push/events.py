# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Notifications that only exist for the mobile app's sake: order confirmed, statement approved / paid, low stock."""

import frappe
from frappe import _
from frappe.utils import escape_html, flt

from field_sales_woocommerce.push.notify import notify_users

# Matches "Low / Out of Stock" in the Products page (frontend/src/pages/Products.vue).
LOW_STOCK_QTY = 5
# At least this many products going low in one product sync are sent as one digest.
LOW_STOCK_DIGEST_MIN = 3
LOW_STOCK_ROLES = ("Sales Manager",)


def _rep_user(sales_rep: str | None) -> str | None:
	if not sales_rep:
		return None
	user = frappe.db.get_value("Sales Rep", sales_rep, "user")
	return user if user and frappe.db.get_value("User", user, "enabled") else None


def notify_order_synced(field_order: str) -> None:
	"""Tell the rep their order is now a real order on the store (confirmed, or a completed draft)."""
	sales_rep = frappe.db.get_value("Field Order", field_order, "sales_rep")
	user = _rep_user(sales_rep)
	if not user:
		return
	subject = _("Order {0} is confirmed in the store").format(f"<b>{escape_html(field_order)}</b>")
	notify_users(
		[user],
		"order_synced",
		"Field Order",
		field_order,
		subject,
		dedupe_on=["document_type", "document_name", "subject"],
	)


def notify_statement(doc, kind: str) -> None:
	"""``kind`` is ``statement_approved`` or ``statement_paid``; sent to the statement's rep."""
	user = _rep_user(doc.sales_rep)
	if not user:
		return
	if kind == "statement_paid":
		subject = _("Commission statement {0} has been marked paid").format(f"<b>{escape_html(doc.name)}</b>")
	else:
		subject = _("Commission statement {0} has been approved").format(f"<b>{escape_html(doc.name)}</b>")
	notify_users([user], kind, "Commission Statement", doc.name, subject, from_user=frappe.session.user)


def crossed_low_stock(before, after) -> bool:
	"""Whether stock just fell from above the low-stock level to it or below."""
	if before is None:
		return False
	return flt(before) > LOW_STOCK_QTY and flt(after) <= LOW_STOCK_QTY


def notify_low_stock(products: list[tuple[str, float]], digest: bool = False) -> None:
	"""
	Tell Sales Managers about products that just went low. Each product is reported at
	most once a day. With ``digest`` (a product sync), three or more go out as one notification.

	:param products: ``[(Platform Product name, new quantity)]``
	"""
	from field_sales_woocommerce.sync.notify import users_with_roles

	fresh = []
	for name, qty in products:
		key = f"field_sales:low_stock_notified:{name}"
		if frappe.cache.get_value(key):
			continue
		frappe.cache.set_value(key, 1, expires_in_sec=24 * 60 * 60)
		fresh.append((name, qty))
	if not fresh:
		return
	users = [u for u in users_with_roles(LOW_STOCK_ROLES) if u != "Administrator"]
	if not users:
		return

	if digest and len(fresh) >= LOW_STOCK_DIGEST_MIN:
		subject = _("{0} products are low on stock").format(len(fresh))
		notify_users(users, "low_stock_digest", "Platform Product", None, subject, params={"count": len(fresh)})
		return

	for name, qty in fresh:
		row = frappe.db.get_value("Platform Product", name, ["product_title", "variant_title"], as_dict=True) or {}
		title = " – ".join(t for t in (row.get("product_title"), row.get("variant_title")) if t) or name
		subject = _("Low stock: {0} has {1} left").format(f"<b>{escape_html(title)}</b>", flt(qty))
		notify_users(
			users, "low_stock", "Platform Product", name, subject, params={"product": title, "qty": flt(qty)}
		)
