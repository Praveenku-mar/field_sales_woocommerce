# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
What the background jobs (hooks.scheduler_events) will do on a site, before and after they are
turned on. Read-only: it counts and lists, it changes nothing.

	bench --site <site> execute <app>.utils.scheduler_check.report

The jobs never ran on WooCommerce and Odoo sites before they were added to hooks.py, so their
first run works through everything that piled up: re-pushing failed orders, deleting old sync
logs, anonymising inactive retailers. Review that here first.
"""

import importlib

import frappe
from frappe.utils import add_days, add_to_date, cint, now_datetime, nowdate
from frappe.utils.scheduler import is_scheduler_disabled

APP = __name__.split(".", 1)[0]


def report() -> dict:
	"""Print a summary for this site and return it as a dict."""
	orders = importlib.import_module(f"{APP}.sync.orders")
	hooks = importlib.import_module(f"{APP}.hooks")

	out = frappe._dict()
	out.scheduler_enabled = not is_scheduler_disabled(verbose=False)
	out.store_connection_enabled = bool(cint(frappe.db.get_single_value("Store Connection", "enabled")))

	# Jobs registered by bench migrate, against hooks.py.
	wanted = set(_hook_methods(hooks))
	registered = {
		r.method: r
		for r in frappe.get_all(
			"Scheduled Job Type",
			filters={"method": ("like", f"{APP}.%")},
			fields=["method", "stopped", "last_execution"],
		)
	}
	out.jobs_in_hooks = len(wanted)
	out.jobs_not_registered = sorted(wanted - set(registered))  # run bench migrate
	out.jobs_stopped = sorted(m for m, r in registered.items() if r.stopped and m in wanted)
	out.jobs_never_ran = sorted(m for m, r in registered.items() if not r.last_execution and m in wanted)

	# retry_failed_orders: pushed to the store within 15 minutes.
	failed = frappe.get_all(
		"Field Order",
		filters={"docstatus": 1, "sync_status": "Failed", "sync_attempts": ("<", orders.MAX_AUTO_ATTEMPTS)},
		fields=["name", "creation", "sync_attempts", "retailer"],
		order_by="creation asc",
	)
	stale_before = add_to_date(now_datetime(), minutes=-orders.STALE_PENDING_MINUTES)
	stuck = frappe.get_all(
		"Field Order",
		filters={"docstatus": 1, "sync_status": "Pending", "modified": ("<", stale_before)},
		or_filters=[["last_sync_attempt", "is", "not set"], ["last_sync_attempt", "<", stale_before]],
		fields=["name", "creation", "retailer"],
		order_by="creation asc",
	)
	out.orders_to_repush = len(failed) + len(stuck)
	out.orders_to_repush_list = [
		f"{r.name} ({str(r.creation)[:10]}, {r.retailer}, {'Failed x' + str(r.sync_attempts) if 'sync_attempts' in r else 'Pending'})"
		for r in (failed + stuck)[:25]
	]

	if hasattr(orders, "link_completed_drafts") and any(m.endswith("link_completed_drafts") for m in wanted):
		out.drafts_to_link = frappe.db.count(
			"Field Order",
			{"docstatus": 1, "sync_status": "Synced", "external_draft_id": ("is", "set"), "external_order_id": ("is", "not set")},
		)

	# clear_old_logs: deleted on the first night.
	out.success_logs_to_delete = frappe.db.count(
		"Sync Log", {"status": "Success", "creation": ("<", add_days(nowdate(), -30))}
	)
	failed_days = cint(frappe.db.get_single_value("Store Connection", "failed_log_retention_days"))
	out.failed_logs_to_delete = (
		frappe.db.count(
			"Sync Log",
			{"status": ("in", ["Failed", "Retrying"]), "creation": ("<", add_days(nowdate(), -failed_days))},
		)
		if failed_days > 0
		else 0
	)

	# anonymise_inactive_retailers: irreversible.
	anon_days = cint(frappe.db.get_single_value("Store Connection", "retailer_inactive_anonymise_days"))
	out.anonymise_after_days = anon_days
	out.retailers_to_anonymise = (
		frappe.db.count(
			"Retailer",
			{"status": "Inactive", "modified": ("<", add_days(now_datetime(), -anon_days)), "retailer_name": ("not like", "Redacted %")},
		)
		if anon_days > 0
		else 0
	)

	# send_daily_summary: managers start getting an email at 20:00.
	summary = importlib.import_module(f"{APP}.daily_summary")
	out.daily_summary_managers = len(summary._get_managers())
	out.outgoing_email_account = bool(
		frappe.db.exists("Email Account", {"enable_outgoing": 1, "default_outgoing": 1})
		or frappe.db.exists("Email Account", {"enable_outgoing": 1})
	)

	# generate_monthly_statements: Draft statements on the 1st.
	commission = importlib.import_module(f"{APP}.commission")
	out.commission_enabled = bool(commission.commission_enabled())
	out.commission_active_reps = frappe.db.count("Sales Rep", {"commission_active": 1})

	# Razorpay jobs.
	out.razorpay_enabled = bool(cint(frappe.db.get_single_value("Razorpay Settings", "enabled")))
	out.payment_links_to_recheck = frappe.db.count(
		"Field Order",
		{"docstatus": ("!=", 0), "payment_status": "Link Sent", "creation": (">=", add_days(now_datetime(), -7))},
	)

	_print(out)
	return out


def _hook_methods(hooks) -> list[str]:
	methods = []
	for key, value in (getattr(hooks, "scheduler_events", None) or {}).items():
		for group in value.values() if key == "cron" else [value]:
			methods.extend(group)
	return methods


def _print(out: frappe._dict):
	warn = "  !!"
	lines = [
		f"Background jobs on {frappe.local.site} ({APP})",
		"",
		f"Scheduler enabled: {out.scheduler_enabled}" + ("" if out.scheduler_enabled else f"{warn} bench --site {frappe.local.site} scheduler enable"),
		f"Store Connection enabled: {out.store_connection_enabled}" + ("" if out.store_connection_enabled else "  (sync jobs do nothing until it is)"),
		f"Jobs in hooks.py: {out.jobs_in_hooks}; not registered: {out.jobs_not_registered or 'none'}"
		+ (f"{warn} run bench migrate" if out.jobs_not_registered else ""),
		f"Stopped jobs: {out.jobs_stopped or 'none'}",
		f"Jobs that never ran yet: {len(out.jobs_never_ran)}",
		"",
		f"Orders re-pushed to the store within 15 min: {out.orders_to_repush}" + (warn if out.orders_to_repush else ""),
		*[f"    {line}" for line in out.orders_to_repush_list],
	]
	if out.orders_to_repush > len(out.orders_to_repush_list):
		lines.append(f"    ... and {out.orders_to_repush - len(out.orders_to_repush_list)} more")
	if "drafts_to_link" in out:
		lines.append(f"Completed drafts to link: {out.drafts_to_link}")
	lines += [
		f"Success sync logs deleted tonight (> 30 days): {out.success_logs_to_delete}",
		f"Failed sync logs deleted tonight: {out.failed_logs_to_delete}",
		f"Anonymise inactive retailers after: {out.anonymise_after_days or 'off'} days; anonymised tonight: {out.retailers_to_anonymise}"
		+ (f"{warn} irreversible" if out.retailers_to_anonymise else ""),
		f"Daily summary (20:00) to {out.daily_summary_managers} manager(s); outgoing email account: {out.outgoing_email_account}"
		+ (f"{warn} no outgoing email" if out.daily_summary_managers and not out.outgoing_email_account else ""),
		f"Commission on: {out.commission_enabled}; reps with commission: {out.commission_active_reps} (Draft statements on the 1st)",
		f"Razorpay on: {out.razorpay_enabled}; links re-checked hourly: {out.payment_links_to_recheck}",
	]
	print("\n".join(lines))
