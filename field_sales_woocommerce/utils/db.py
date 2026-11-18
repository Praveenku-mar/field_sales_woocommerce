# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Transaction helpers for the places that must keep their work before the request or job ends."""

import frappe


def safe_commit() -> None:
	"""
	Commit now: background jobs that keep each item's result before the next, logs that must
	survive the caller's rollback, and the few requests that persist a token or a lock. If the
	commit itself fails, the transaction is rolled back before the error is raised, so the
	connection is never left in a half-committed state for the next statement.
	"""
	try:
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise
