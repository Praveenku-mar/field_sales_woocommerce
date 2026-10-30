# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Keep the site's currency and time zone the same as the connected store's, and record the
store's country (the default country for new Retailers).

The store is the source of truth: product prices come from it and order prices go back to it
without conversion, so the site must use the store's currency.
"""

from zoneinfo import available_timezones

import frappe
from frappe import _

from field_sales_woocommerce.connectors import get_connector


def sync_shop_settings(shop: dict | None = None) -> dict:
	"""
	Save the store's currency and time zone on Store Connection and, when Match Store Currency
	and Timezone is on, apply them to the site.

	:param shop: ``{"currency", "timezone", "country"}`` already fetched (e.g. by Test Connection); fetched
		from the store when ``None`` (raises ``ConnectorError`` on a store error).
	:return: ``{"changes": [str], "mismatch": [str]}``: what was changed on the site, and the
		differences still left between the store and the site.
	"""
	if shop is None:
		shop = get_connector().get_shop_settings()
	currency = (shop.get("currency") or "").strip().upper()
	timezone = (shop.get("timezone") or "").strip()
	country = (shop.get("country") or "").strip().upper()

	values = {}
	if currency:
		values["shop_currency"] = currency
	if timezone:
		values["shop_timezone"] = timezone
	if country:
		values["shop_country"] = country
	if values:
		frappe.db.set_single_value("Store Connection", values)

	changes = []
	if frappe.db.get_single_value("Store Connection", "match_shop_settings"):
		if currency and _apply_currency(currency):
			changes.append(_("Currency set to {0}").format(currency))
		if timezone and _apply_timezone(timezone):
			changes.append(_("Time zone set to {0}").format(timezone))
		if changes:
			frappe.clear_cache()

	return {"changes": changes, "mismatch": get_mismatch(currency, timezone)}


def _apply_currency(code: str) -> bool:
	"""Make ``code`` the site currency; ``True`` if anything changed."""
	if not frappe.db.exists("Currency", code):
		frappe.log_error(
			title=_("Store currency {0} not found").format(code),
			message=_("Create the Currency {0} so the site can use the store's currency.").format(code),
		)
		return False

	changed = False
	if not frappe.db.get_value("Currency", code, "enabled"):
		frappe.db.set_value("Currency", code, "enabled", 1)
		changed = True

	if frappe.get_meta("System Settings").has_field("currency"):
		if frappe.db.get_single_value("System Settings", "currency") != code:
			frappe.db.set_single_value("System Settings", "currency", code)
			changed = True

	# ERPNext keeps its own company-wide default.
	if frappe.db.exists("DocType", "Global Defaults"):
		if frappe.db.get_single_value("Global Defaults", "default_currency") != code:
			frappe.db.set_single_value("Global Defaults", "default_currency", code)
			changed = True

	if frappe.db.get_default("currency") != code:
		frappe.db.set_default("currency", code)
		changed = True
	return changed


def _apply_timezone(tz: str) -> bool:
	"""Make ``tz`` the system time zone; ``True`` if anything changed."""
	if tz not in available_timezones():
		frappe.log_error(
			title=_("Unknown store time zone {0}").format(tz),
			message=_("The store's time zone {0} is not a known IANA time zone; the site's was kept.").format(
				tz
			),
		)
		return False

	changed = False
	if frappe.db.get_single_value("System Settings", "time_zone") != tz:
		frappe.db.set_single_value("System Settings", "time_zone", tz)
		changed = True
	if frappe.db.get_default("time_zone") != tz:
		frappe.db.set_default("time_zone", tz)
		changed = True
	if changed:
		_clear_system_settings_cache()
	return changed


def _clear_system_settings_cache():
	frappe.cache.delete_value("time_zone")
	try:
		from frappe.core.doctype.system_settings.system_settings import clear_system_settings_cache
	except ImportError:
		return
	clear_system_settings_cache()


def get_mismatch(currency: str | None = None, timezone: str | None = None) -> list[str]:
	"""
	Differences between the store's settings and the site's, as readable lines.

	:param currency: The store's currency; defaults to the one saved on Store Connection.
	:param timezone: The store's time zone; defaults to the one saved on Store Connection.
	"""
	if currency is None or timezone is None:
		saved = (
			frappe.db.get_value(
				"Store Connection", "Store Connection", ["shop_currency", "shop_timezone"], as_dict=True
			)
			or {}
		)
		currency = saved.get("shop_currency") if currency is None else currency
		timezone = saved.get("shop_timezone") if timezone is None else timezone

	mismatch = []
	site_currency = frappe.db.get_default("currency")
	if currency and currency != site_currency:
		mismatch.append(
			_("The store uses {0} but the site uses {1}").format(currency, site_currency or _("no currency"))
		)
	site_tz = frappe.db.get_single_value("System Settings", "time_zone")
	if timezone and timezone != site_tz:
		mismatch.append(
			_("The store's time zone is {0} but the site's is {1}").format(timezone, site_tz or _("not set"))
		)
	return mismatch


def scheduled_sync(notify_user: str | None = None):
	"""
	Daily job, and queued after Store Connection is saved: re-check the store's settings.

	:param notify_user: User who saved Store Connection; told in a popup if something changed.
	"""
	settings = frappe.db.get_value(
		"Store Connection", "Store Connection", ["enabled", "match_shop_settings"], as_dict=True
	)
	if not settings or not settings.enabled or not settings.match_shop_settings:
		return

	try:
		result = sync_shop_settings()
	except Exception:
		frappe.log_error(title=_("Store currency and time zone sync failed"))
		return

	if result["changes"] and notify_user:
		lines = "".join(f"<li>{frappe.utils.escape_html(c)}</li>" for c in result["changes"])
		frappe.publish_realtime(
			"msgprint",
			{
				"title": _("Matched the store's settings"),
				"indicator": "green",
				"message": f"<ul>{lines}</ul>" + _("Reload the page to see the change."),
			},
			user=notify_user,
			after_commit=True,
		)
