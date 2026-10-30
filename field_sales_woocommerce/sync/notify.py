# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Realtime "sync finished" events for open forms and the Sync Failures page, and sync failure notifications."""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

# Roles that can open the Sync Failures page.
MONITOR_ROLES = ("Sales Manager", "System Manager")
ADMIN_ROLES = ("System Manager",)


def notify_sync_result(event: str, doctype: str, name: str, sync_status: str | None) -> None:
	"""
	Publish ``event`` with ``{"doctype", "name", "sync_status"}`` once the
	current transaction commits.

	Sent to the document's room (its open form) and to each user who can open
	the Sync Failures page, rather than to everyone: reps should not receive
	events about other reps' records.
	"""
	message = {"doctype": doctype, "name": name, "sync_status": sync_status}
	frappe.publish_realtime(event, message, doctype=doctype, docname=name, after_commit=True)
	for user in _monitor_users():
		frappe.publish_realtime(event, message, user=user, after_commit=True)


def _monitor_users() -> list[str]:
	"""Enabled users who can open the Sync Failures page."""
	return users_with_roles(MONITOR_ROLES)


def users_with_roles(roles: tuple[str, ...]) -> list[str]:
	"""Enabled users with any of ``roles``; cached for the rest of the request / job."""
	cache = getattr(frappe.local, "field_sales_role_users", None)
	if cache is None:
		cache = frappe.local.field_sales_role_users = {}
	key = tuple(sorted(roles))
	if key not in cache:
		has_role = frappe.qb.DocType("Has Role")
		user = frappe.qb.DocType("User")
		cache[key] = (
			frappe.qb.from_(has_role)
			.join(user)
			.on(user.name == has_role.parent)
			.select(has_role.parent)
			.distinct()
			.where(has_role.parenttype == "User")
			.where(has_role.role.isin(key))
			.where(user.enabled == 1)
			.where(user.name != "Guest")
		).run(pluck=True)
	return cache[key]


def clear_role_users_cache() -> None:
	frappe.local.field_sales_role_users = {}


def get_manager_user_for_rep(sales_rep: str) -> str | None:
	"""
	Find the Sales Manager user responsible for ``sales_rep`` via reports_to.

	Traverses up the reports_to hierarchy looking for an enabled user with the
	Sales Manager role. Falls back to the nearest enabled manager in the chain.
	"""
	if not sales_rep:
		return None
	current = frappe.db.get_value("Sales Rep", sales_rep, "reports_to")
	seen = {sales_rep}
	first_manager = None
	while current and current not in seen:
		seen.add(current)
		user = frappe.db.get_value("Sales Rep", current, "user") or current
		if frappe.db.get_value("User", user, "enabled"):
			if not first_manager:
				first_manager = user
			roles = set(frappe.get_roles(user))
			if "Sales Manager" in roles or "System Manager" in roles:
				return user
		current = frappe.db.get_value("Sales Rep", current, "reports_to")
	return first_manager


def get_system_admin_recipients() -> list[str]:
	"""Return user emails for Administrator and enabled users with the System Manager role."""
	admin_users = set(users_with_roles(ADMIN_ROLES))
	if frappe.db.get_value("User", "Administrator", "enabled"):
		admin_users.add("Administrator")
	recipients = []
	for user in sorted(admin_users):
		email = frappe.db.get_value("User", user, "email") or user
		if email:
			recipients.append(email)
	return recipients


def notify_order_sync_failure(doc) -> None:
	"""
	Send a notification to the Sales Manager responsible for the Sales Representative
	who created the order.
	"""
	sales_rep = doc.sales_rep or frappe.db.get_value("Sales Rep", {"user": doc.owner}, "name")
	manager_user = get_manager_user_for_rep(sales_rep)
	if not manager_user:
		return

	recipient = frappe.db.get_value("User", manager_user, "email") or manager_user
	subject = _("Field Order {0} failed to sync").format(doc.name)
	order_url = frappe.utils.get_url_to_form("Field Order", doc.name)
	retailer_label = frappe.utils.escape_html(doc.retailer_name or doc.retailer or "")
	rep_label = frappe.utils.escape_html(doc.sales_rep or "")
	error_label = frappe.utils.escape_html(doc.sync_error or "")
	attempts = doc.sync_attempts or 1
	email_content = _(
		'<p>Field Order <a href="{0}">{1}</a> for {2} (Sales Rep: {3}) failed to sync to the store (attempt {4}).</p><p><b>Error:</b> {5}</p>'
	).format(
		order_url,
		frappe.utils.escape_html(doc.name),
		retailer_label,
		rep_label,
		attempts,
		error_label,
	)
	enqueue_create_notification(
		[recipient],
		{
			"type": "Alert",
			"document_type": "Field Order",
			"document_name": doc.name,
			"from_user": doc.owner or "Administrator",
			"subject": subject,
			"email_content": email_content,
		},
		dedupe_on=["document_type", "document_name", "subject"],
	)


def notify_customer_sync_failure(doc) -> None:
	"""
	Send a notification to the System Administrator when a customer sync fails.
	"""
	recipients = get_system_admin_recipients()
	if not recipients:
		return

	customer_label = doc.retailer_name or doc.name
	subject = _("Customer {0} failed to sync").format(customer_label)
	customer_url = frappe.utils.get_url_to_form("Retailer", doc.name)
	error_label = frappe.utils.escape_html(doc.sync_error or "")
	email_content = _(
		'<p>Customer <a href="{0}">{1}</a> failed to sync to the store.</p><p><b>Error:</b> {2}</p>'
	).format(customer_url, frappe.utils.escape_html(customer_label), error_label)
	enqueue_create_notification(
		recipients,
		{
			"type": "Alert",
			"document_type": "Retailer",
			"document_name": doc.name,
			"from_user": "Administrator",
			"subject": subject,
			"email_content": email_content,
		},
		dedupe_on=["document_type", "document_name", "subject"],
	)
