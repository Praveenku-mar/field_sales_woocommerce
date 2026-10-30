# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def commission_enabled() -> bool:
	"""
	Whether this company pays commission (Store Connection → Pay Commission to Sales Reps).

	Read from tabSingles so a site that has never saved the field (before the patch ran) counts as on.
	"""
	rows = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'Store Connection' and field = 'enable_commission'"
	)
	return not rows or str(rows[0][0]) not in ("0", "", "None")


def require_commission_enabled() -> None:
	"""Refuse new commission work while commission is turned off."""
	if not commission_enabled():
		frappe.throw(
			_("Commission is turned off for this company. Turn it on in Settings → Commission."),
			title=_("Commission is off"),
		)
