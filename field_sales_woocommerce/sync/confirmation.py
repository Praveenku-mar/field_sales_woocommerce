# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt


import contextvars
import threading

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import fmt_money, format_datetime, now_datetime, today

from field_sales_woocommerce.payments.razorpay import link_for_confirmation
from field_sales_woocommerce.utils.money import site_currency

SENDING_STATUS = "Sending…"
INVOICE_SERIES = "INV-.YYYY.-.#####"
PRINT_FORMAT = "Field Sales Invoice"

# Hard limit for turning the invoice into a PDF. wkhtmltopdf can wait forever on the print CSS
# when the site's host_name is wrong, and pdfkit has no timeout of its own.
PDF_TIMEOUT = 60
# The queue kills the job after this, whatever happens (must be well above PDF_TIMEOUT).
JOB_TIMEOUT = 180
PDF_TIMEOUT_STATUS = "Error: PDF generation timed out (check the site's host_name setting)"


class PDFTimeout(Exception):
	pass


def enqueue_order_confirmation(field_order: str):
	"""
	Queue ``send_order_confirmation`` once the current transaction commits.

	Deduplicated per order, so the push and the draft linker cannot queue it twice at once.
	"""
	frappe.enqueue(
		"field_sales_woocommerce.sync.confirmation.send_order_confirmation",
		field_order=field_order,
		job_id=f"field_sales:order_confirmation:{field_order}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def send_order_confirmation(field_order: str, resend: bool = False) -> str:

	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	if doc.docstatus != 1 or not doc.external_order_id:
		return doc.confirmation_status or ""
	if doc.confirmation_sent_on and not resend:
		return doc.confirmation_status or ""

	if not frappe.db.get_single_value("Store Connection", "send_order_confirmation"):
		return _set_status(doc, "Skipped: order confirmation emails are turned off")

	# Before the savepoint: a link created on Razorpay must stay stored even if the email fails.
	pay_link = None
	if frappe.db.get_value("Retailer", doc.retailer, "email"):
		pay_link = link_for_confirmation(doc)
		if not pay_link and doc.store_checkout_url and doc.payment_status not in ("Paid", "Partially Paid"):
			# The store's own checkout: a payment link every install may use.
			pay_link = {"url": doc.store_checkout_url, "expires_on": None}

	# Undone if sending fails, so a failed attempt does not use up an invoice number.
	frappe.db.savepoint("order_confirmation")
	try:
		if not doc.invoice_no:
			doc.db_set(
				{"invoice_no": make_autoname(INVOICE_SERIES), "invoice_date": today()},
				update_modified=False,
			)

		retailer = frappe.db.get_value("Retailer", doc.retailer, ["email", "retailer_name"], as_dict=True)
		if not retailer or not retailer.email:
			status = "Skipped: customer has no email"
			doc.add_comment("Comment", _("Order confirmation not sent: the customer has no email address."))
			return _set_status(doc, status)

		_send(doc, retailer, pay_link)
		status = f"Sent to {retailer.email}"
		if doc.flags.payment_link_error:
			status += " without payment link"
		return _set_status(doc, status, sent=True)
	except PDFTimeout:
		frappe.db.rollback(save_point="order_confirmation")
		frappe.log_error(title=f"Order confirmation PDF timed out: {field_order}")
		return _set_status(doc, PDF_TIMEOUT_STATUS)
	except Exception as e:
		frappe.db.rollback(save_point="order_confirmation")
		frappe.log_error(title=f"Order confirmation failed: {field_order}")
		return _set_status(doc, f"Error: {str(e)[:500]}")


@frappe.whitelist(methods=["POST"])
def resend_order_confirmation(name: str) -> dict:

	doc = frappe.get_doc("Field Order", name)
	doc.check_permission("read")
	if doc.docstatus != 1 or not doc.external_order_id:
		frappe.throw(_("The confirmation can only be sent once the store has confirmed the order"))

	doc.db_set("confirmation_status", SENDING_STATUS, update_modified=False)
	frappe.db.commit()  # the job and the polling page must see "Sending…"

	# A job id of its own: the automatic job's id may still be on the queue (or finished) and
	# deduplicate would drop this one. Two quick clicks share this id, so only one job runs.
	frappe.enqueue(
		"field_sales_woocommerce.sync.confirmation.send_order_confirmation",
		field_order=name,
		resend=True,
		job_id=f"field_sales:order_confirmation_resend:{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		timeout=JOB_TIMEOUT,
	)
	return {"status": SENDING_STATUS, "queued": True}


def _build_pdf(doc) -> bytes:
	"""
	The invoice as PDF bytes, within ``PDF_TIMEOUT`` seconds or ``PDFTimeout`` is raised.

	The conversion runs in a helper thread (with this request's frappe context) that we wait on
	with a timeout. A thread works in any worker and needs no signals. On timeout the wkhtmltopdf
	processes started meanwhile are killed, which also unblocks the helper thread, so none is left behind.
	"""
	from frappe.utils import pdf

	html = frappe.get_print("Field Order", doc.name, print_format=PRINT_FORMAT, doc=doc)
	box = {}

	def convert():
		try:
			box["pdf"] = pdf.get_pdf(html)
		except BaseException as e:
			box["error"] = e

	before = _wkhtmltopdf_pids()
	thread = threading.Thread(target=contextvars.copy_context().run, args=(convert,), daemon=True)
	thread.start()
	thread.join(PDF_TIMEOUT)
	if thread.is_alive():
		_kill_wkhtmltopdf(before)
		thread.join(2)
		raise PDFTimeout

	if "error" in box:
		raise box["error"]
	return box["pdf"]


def _wkhtmltopdf_pids() -> set:
	import psutil

	try:
		return {p.pid for p in psutil.Process().children(recursive=True) if "wkhtmltopdf" in p.name()}
	except psutil.Error:
		return set()


def _kill_wkhtmltopdf(keep: set):
	"""Kill the wkhtmltopdf children of this process that were not running before (`keep`)."""
	import psutil

	try:
		children = psutil.Process().children(recursive=True)
	except psutil.Error:
		return
	for child in children:
		try:
			if child.pid not in keep and "wkhtmltopdf" in child.name():
				child.kill()
		except psutil.Error:
			pass


def _send(doc, retailer, pay_link: dict | None = None):
	"""Queue the email to the customer with the invoice PDF attached (and a Pay Now button when there is a link)."""
	settings = frappe.db.get_value(
		"Store Connection", "Store Connection", ["company_name", "shop_name"], as_dict=True
	)
	rep_name = frappe.db.get_value("Sales Rep", doc.sales_rep, "full_name") or doc.sales_rep
	currency = site_currency()
	order_no = doc.store_order_name or doc.name

	# Built before anything is queued, so a timeout leaves nothing behind.
	pdf_bytes = _build_pdf(doc)

	frappe.sendmail(
		recipients=[retailer.email],
		subject=_("Order confirmed: {0} — Invoice {1}").format(order_no, doc.invoice_no),
		template="order_confirmation",
		args={
			"customer": retailer.retailer_name or doc.retailer_name or doc.retailer,
			"order_no": order_no,
			"invoice_no": doc.invoice_no,
			"items": [
				{
					"title": row.product_title or row.product,
					"variant": row.variant_title,
					"qty": row.qty,
					"amount": fmt_money(row.amount, currency=currency),
				}
				for row in doc.items
			],
			"net_total": fmt_money(doc.net_total, currency=currency),
			"discount": fmt_money(doc.discount_amount, currency=currency) if doc.discount_amount else None,
			"tax": fmt_money(doc.tax_amount, currency=currency) if doc.tax_amount else None,
			"grand_total": fmt_money(doc.grand_total, currency=currency),
			"sales_rep": rep_name,
			"company": settings.company_name or settings.shop_name,
			"payment_url": pay_link and pay_link.get("url"),
			"payment_expires": pay_link
			and pay_link.get("expires_on")
			and format_datetime(pay_link["expires_on"], "dd MMM yyyy, hh:mm a"),
		},
		attachments=[{"fname": f"{doc.invoice_no}.pdf", "fcontent": pdf_bytes}],
		reference_doctype="Field Order",
		reference_name=doc.name,
		now=True
	)


def _set_status(doc, status: str, sent: bool = False) -> str:
	"""Record the last result on the order (and when it was sent); the order's modified time stays."""
	values = {"confirmation_status": status}
	if sent:
		values["confirmation_sent_on"] = now_datetime()
	doc.db_set(values, update_modified=False)
	return status
