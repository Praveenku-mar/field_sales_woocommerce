"""Record a Field Order payment received outside Razorpay."""

import frappe
from frappe import _
from frappe.utils import escape_html, flt, now_datetime

from field_sales_woocommerce.payments.razorpay import cancel_payment_link, fetch_payment_link, get_settings, notify_payment_received
from field_sales_woocommerce.sync.orders import enqueue_mark_store_paid
from field_sales_woocommerce.utils.money import site_currency


@frappe.whitelist(methods=["POST"])
def mark_order_paid(field_order: str, payment_method: str, payment_reference: str | None = None) -> dict:
	"""Mark a submitted order fully paid by cash or bank transfer / UPI."""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the order before recording payment"))
	if doc.payment_status in ("Paid", "Partially Paid"):
		frappe.throw(_("This order already has a payment; reconcile it before recording another"))
	if flt(doc.grand_total) <= 0:
		frappe.throw(_("The order total must be more than zero"))
	if payment_method not in ("Cash", "Bank Transfer / UPI"):
		frappe.throw(_("Select Cash or Bank Transfer / UPI"))

	reference = (payment_reference or "").strip()
	if payment_method == "Bank Transfer / UPI" and not reference:
		frappe.throw(_("Enter the bank transfer or UPI reference number"))
	if payment_method == "Cash" and reference:
		frappe.throw(_("A payment reference is only used for bank transfer or UPI"))
	if len(reference) > 140:
		frappe.throw(_("The payment reference must be 140 characters or fewer"))

	# A stale Link Sent state can conceal a payment that arrived at Razorpay.
	# Check the provider before cancelling an open link or recording the full amount.
	if doc.razorpay_payment_link_id:
		link = fetch_payment_link(doc.razorpay_payment_link_id, settings=get_settings())
		if link.get("status") in ("paid", "partially_paid"):
			frappe.throw(
				_("The Razorpay link already has a payment. Refresh its status before recording another.")
			)
		if link.get("status") == "created":
			cancel_payment_link(doc.razorpay_payment_link_id, field_order=doc.name)

	doc.db_set(
		{
			"payment_status": "Paid",
			"amount_paid": doc.grand_total,
			"paid_on": now_datetime(),
			"manual_payment_method": payment_method,
			"manual_payment_reference": reference or None,
		},
		update_modified=False,
	)
	doc.add_comment(
		"Info",
		_("Manual payment received: {0} by {1}.").format(
			frappe.utils.fmt_money(doc.grand_total, currency=site_currency()),
			_(payment_method),
		)
		+ (_(" Reference: {0}.").format(escape_html(reference)) if reference else ""),
	)
	notify_payment_received(doc)
	enqueue_mark_store_paid(doc.name)
	frappe.publish_realtime(
		"field_order_payment_updated",
		{"name": doc.name, "payment_status": "Paid"},
		doctype="Field Order",
		docname=doc.name,
		after_commit=True,
	)
	return {"name": doc.name, "payment_status": doc.payment_status, "payment_method": payment_method}
