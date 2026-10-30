# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Daily email to each manager summarising their reps' day (scheduler, 20:00)."""

import frappe
from frappe import _
from frappe.utils import escape_html, fmt_money, formatdate, get_url, nowdate

from field_sales_woocommerce.field_sales_woocommerce.report.rep_performance.rep_performance import get_rep_stats, get_reps_in_scope
from field_sales_woocommerce.permissions import clear_allowed_reps_cache


def send_daily_summary():
	"""
	Email every manager (a user whose Sales Rep has others reporting to it)
	a summary of today for everyone under them, at all levels.

	Managers whose team had no activity and no failures get no email.
	"""
	today = nowdate()
	for manager in _get_managers():
		try:
			_send_to_manager(manager, today)
		except Exception:
			# One bad manager record must not stop the others' summaries.
			frappe.log_error(title=f"Field Sales daily summary failed for {manager.user}")


def _get_managers() -> list:
	rep = frappe.qb.DocType("Sales Rep")
	sub = frappe.qb.DocType("Sales Rep").as_("sub")
	user = frappe.qb.DocType("User")
	return (
		frappe.qb.from_(rep)
		.join(sub)
		.on(sub.reports_to == rep.name)
		.join(user)
		.on(user.name == rep.user)
		.select(rep.name, rep.user, rep.full_name)
		.distinct()
		.where(rep.active == 1)
		.where(user.enabled == 1)
	).run(as_dict=True)


def _send_to_manager(manager, today: str):
	clear_allowed_reps_cache()
	reps = get_reps_in_scope({}, user=manager.user)
	if not reps:
		return
	rows = get_rep_stats(today, today, reps)
	failures = _get_open_failures(list(reps))

	for row in rows:
		row["open_failures"] = failures.get(row["sales_rep"], 0)

	active = [r for r in rows if r["planned_visits"] or r["orders"] or r["open_failures"]]
	if not active:
		return

	frappe.sendmail(
		recipients=[manager.user],
		subject=_("Field Sales summary for {0}").format(formatdate(today)),
		message=_render(manager, today, active),
		reference_doctype="Sales Rep",
		reference_name=manager.name,
	)


def _get_open_failures(reps: list[str]) -> dict:
	"""Failed Field Orders + Retailers per rep, whenever they failed (they are still open today)."""
	counts = {}
	for doctype, extra in (("Field Order", {"docstatus": 1}), ("Retailer", {})):
		for row in frappe.get_all(
			doctype,
			filters={"sync_status": "Failed", "sales_rep": ("in", reps), **extra},
			fields=["sales_rep", {"COUNT": "*", "as": "count"}],
			group_by="sales_rep",
		):
			counts[row.sales_rep] = counts.get(row.sales_rep, 0) + row.count
	return counts


def _render(manager, today: str, rows: list[dict]) -> str:
	currency = frappe.db.get_default("currency")
	totals = {
		key: sum(r[key] for r in rows)
		for key in ("planned_visits", "completed_visits", "orders", "order_value", "open_failures")
	}

	def tr(cells, bold=False):
		tag = "th" if bold else "td"
		return "<tr>" + "".join(
			f'<{tag} style="padding:6px 10px;border:1px solid #e2e6e9;text-align:{align}">{value}</{tag}>'
			for value, align in cells
		) + "</tr>"

	header = tr(
		[
			(_("Sales Rep"), "left"),
			(_("Visits"), "right"),
			(_("Completed"), "right"),
			(_("Orders"), "right"),
			(_("Order Value"), "right"),
			(_("Sync Failures"), "right"),
		],
		bold=True,
	)
	body = "".join(
		tr(
			[
				(escape_html(r["sales_rep_name"] or r["sales_rep"]), "left"),
				(r["planned_visits"], "right"),
				(r["completed_visits"], "right"),
				(r["orders"], "right"),
				(fmt_money(r["order_value"], currency=currency), "right"),
				(
					f'<span style="color:#e24c4c;font-weight:600">{r["open_failures"]}</span>'
					if r["open_failures"]
					else 0,
					"right",
				),
			]
		)
		for r in rows
	)
	total = tr(
		[
			(_("Total"), "left"),
			(totals["planned_visits"], "right"),
			(totals["completed_visits"], "right"),
			(totals["orders"], "right"),
			(fmt_money(totals["order_value"], currency=currency), "right"),
			(totals["open_failures"], "right"),
		],
		bold=True,
	)

	links = f'<a href="{get_url("/app/query-report/Rep%20Performance")}">{_("Rep Performance")}</a>'
	if totals["open_failures"]:
		links += f' · <a href="{get_url("/app/sync-failures")}">{_("Sync Failures")}</a>'

	return f"""
		<p>{_("Hi {0},").format(escape_html(manager.full_name or manager.user))}</p>
		<p>{_("Here is your team's day ({0}).").format(formatdate(today))}</p>
		<table style="border-collapse:collapse;font-size:13px">{header}{body}{total}</table>
		<p style="color:#8d99a6;font-size:12px">{_("Sync Failures are records still failing, whenever they failed.")}</p>
		<p>{links}</p>
	"""
