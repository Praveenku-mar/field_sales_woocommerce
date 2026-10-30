import frappe


def site_currency() -> str:
	"""The site's default currency (System Settings), which follows the store's."""
	return frappe.db.get_default("currency") or "INR"
