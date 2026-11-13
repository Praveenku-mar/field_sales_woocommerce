# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Absolute URLs that leave the site (invite emails, webhook URLs) built from a trusted origin.

Host and X-Forwarded-Host are sent by the client. A link built from them blindly lets anyone
who can trigger an email (an invite, a reset) point it at their own host and collect the
token in it. The request origin is used only when its host is one this site is known by:

- the host of ``host_name`` (site config),
- the site name (``frappe.local.site``),
- the host of ``shopify_app_url`` (the public tunnel / app URL, when set),
- any host in the ``field_sales_public_hosts`` site config list (e.g. a custom domain that is
  not ``host_name`` because ``host_name`` must stay internal for PDF rendering).

Otherwise the URL is built from site config only (``get_url`` without header override).
"""

import re
from urllib.parse import urlsplit

import frappe
from frappe.utils import get_url

# A bare host with an optional port, as it appears in Host / X-Forwarded-Host.
_HOST_RE = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?")


def _hostname(value: str | None) -> str:
	"""``https://Example.com:8000/x`` or ``example.com:8000`` -> ``example.com``."""
	value = (value or "").strip().lower()
	if not value:
		return ""
	if "://" not in value:
		value = f"//{value}"
	try:
		return (urlsplit(value).hostname or "").strip(".")
	except ValueError:
		return ""


def allowed_hosts() -> set[str]:
	"""Host names (no port) this site may be reached at, from site config only."""
	conf = frappe.local.conf
	hosts = {
		_hostname(conf.get("host_name") or conf.get("hostname")),
		_hostname(getattr(frappe.local, "site", None)),
		_hostname(conf.get("shopify_app_url")),
	}
	extra = conf.get("field_sales_public_hosts") or []
	if isinstance(extra, str):
		extra = [extra]
	hosts.update(_hostname(h) for h in extra if isinstance(h, str))
	hosts.discard("")
	return hosts


def _str(value) -> str:
	return value if isinstance(value, str) else ""


def _trusted(host: str, allowed: set[str]) -> bool:
	return bool(host) and bool(_HOST_RE.fullmatch(host)) and _hostname(host) in allowed


def public_url(path: str) -> str:
	"""
	Absolute URL of ``path`` as the current request reached the site, if that origin is trusted.

	X-Forwarded-Host (a tunnel / proxy that keeps the internal Host) is preferred over Host, but
	either is used only when its host is in ``allowed_hosts``. Without a trusted request origin:
	``get_url`` from site config, never from request headers.
	"""
	request = getattr(frappe.local, "request", None)
	if request:
		allowed = allowed_hosts()
		headers = request.headers or {}
		forwarded_host = _str(headers.get("X-Forwarded-Host")).split(",")[0].strip()
		if _trusted(forwarded_host, allowed):
			proto = _str(headers.get("X-Forwarded-Proto")).split(",")[0].strip().lower()
			return f"{proto if proto in ('http', 'https') else 'https'}://{forwarded_host}{path}"

		host = _str(getattr(request, "host", None)).strip()
		scheme = _str(getattr(request, "scheme", None)).lower()
		if _trusted(host, allowed) and scheme in ("http", "https"):
			return f"{scheme}://{host}{path}"
	return get_url(path, allow_header_override=False)
