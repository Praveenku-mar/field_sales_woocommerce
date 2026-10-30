# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Stock by location, and live stock: ask the store for current quantities instead of trusting the last sync.

Platform-neutral; talks to the store only through ``get_connector()``. Used by
"Refresh Stock", the Field Order submit check, the app's order screen, the
product sync and the stock webhook.

Stock lives in ``Product Stock`` (one row per product and location).
``Platform Product.available_qty`` is a copy of the *default* location's row, so
lists, number cards and presets that read it keep working. Write stock only
through ``set_location_stock`` so the two never drift apart.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from field_sales_woocommerce.connectors import get_connector


def get_sell_locations() -> list[dict]:
	"""
	Locations a rep can sell from: the Store Connection's ``Sell from here`` rows.

	The default location is always included, even if the table has no row for it
	yet (a connection set up before locations existed), and is listed first.

	:return: ``[{"location_id", "location_name", "is_default"}]``
	"""
	default_id, default_name = frappe.db.get_value(
		"Store Connection", "Store Connection", ["default_location_id", "default_location_name"]
	) or (None, None)

	rows = frappe.get_all(
		"Store Location",
		filters={"parent": "Store Connection", "parenttype": "Store Connection", "sell_from": 1},
		fields=["location_id", "location_name"],
		order_by="idx asc",
	)
	locations = [
		{
			"location_id": r.location_id,
			"location_name": r.location_name or r.location_id,
			"is_default": r.location_id == default_id,
		}
		for r in rows
	]
	if default_id and not any(loc["is_default"] for loc in locations):
		locations.append({"location_id": default_id, "location_name": default_name or default_id, "is_default": True})

	locations.sort(key=lambda loc: not loc["is_default"])  # stable: default first, table order otherwise
	return locations


def get_default_location() -> str | None:
	"""Store Connection's default location id, or ``None`` if it has none."""
	return frappe.db.get_single_value("Store Connection", "default_location_id") or None


def set_location_stock(
	product: str,
	location_id: str,
	qty: float,
	location_name: str | None = None,
	updated_at=None,
	update_product: bool = True,
) -> None:
	"""
	Save a product's stock at one location (create or update its Product Stock row).

	At the default location it also sets ``Platform Product.available_qty``,
	``stock_updated_at`` and ``location_id``. Does not commit.

	:param product: Platform Product name.
	:param location_id: Platform location id.
	:param qty: Available quantity there.
	:param location_name: Shown in lists; kept as is when omitted.
	:param updated_at: When the quantity was true (webhook time); now when omitted.
	:param update_product: ``False`` when the caller has already written the Platform Product (the product sync does).
	"""
	updated_at = updated_at or now_datetime()
	location_id = str(location_id)
	name = frappe.db.exists("Product Stock", {"product": product, "location_id": location_id})
	values = {"available_qty": qty, "stock_updated_at": updated_at}
	if location_name:
		values["location_name"] = location_name

	if name:
		frappe.db.set_value("Product Stock", name, values, update_modified=False)
	else:
		frappe.get_doc(
			{"doctype": "Product Stock", "product": product, "location_id": location_id, **values}
		).insert(ignore_permissions=True)

	if update_product and location_id == get_default_location():
		frappe.db.set_value(
			"Platform Product",
			product,
			{"available_qty": qty, "location_id": location_id, "stock_updated_at": updated_at},
			update_modified=False,
		)


def get_location_stock(products: list[str], location_id: str | None = None) -> dict[str, float]:
	"""
	Stored stock of ``products`` at a location (the default when ``None``).

	A product with no Product Stock row there has no known stock: 0, except at
	the default location (or when no location is set up at all) where
	``Platform Product.available_qty`` stands in.

	:return: ``{product: available_qty}`` for every product given.
	"""
	products = list({p for p in products if p})
	if not products:
		return {}

	default_id = get_default_location()
	location_id = location_id or default_id
	stock = dict.fromkeys(products, 0.0)

	# No location set up yet: the product's own figure is all there is.
	if not location_id or location_id == default_id:
		stock.update(
			{
				r.name: r.available_qty or 0.0
				for r in frappe.get_all(
					"Platform Product", filters={"name": ("in", products)}, fields=["name", "available_qty"]
				)
			}
		)
	if location_id:
		stock.update(
			{
				r.product: r.available_qty or 0.0
				for r in frappe.get_all(
					"Product Stock",
					filters={"product": ("in", products), "location_id": location_id},
					fields=["product", "available_qty"],
				)
			}
		)
	return stock


def fetch_live_stock(products: list[str], location_id: str | None = None) -> dict[str, float]:
	"""
	Fetch current stock at a location for the tracked products among
	``products`` and save it (see ``set_location_stock``).

	Untracked products are always available, so they are left out (no store call).
	``stock_updated_at`` is set to now, so a stock webhook older than this answer
	cannot overwrite it.

	Raises ``ConnectorError`` if the store cannot be reached, and a
	``frappe.ValidationError`` if there is no default location or ``location_id``
	is not one the shop sells from.

	:param products: Platform Product names.
	:param location_id: Platform location id; ``None`` for the default location.
	:return: ``{product: available_qty}`` for the tracked products only.
	"""
	products = list({p for p in products if p})
	if not products:
		return {}

	sell_locations = {loc["location_id"]: loc for loc in get_sell_locations()}
	location_id = location_id or get_default_location()
	if not location_id:
		frappe.throw(_("Set a Default Location on Store Connection before checking stock"))
	if location_id not in sell_locations:
		frappe.throw(_("Location {0} is not one the shop sells from").format(location_id))

	inventory_ids = {
		p.name: p.external_inventory_id
		for p in frappe.get_all(
			"Platform Product",
			filters={"name": ("in", products), "track_inventory": 1, "external_inventory_id": ("is", "set")},
			fields=["name", "external_inventory_id"],
		)
	}
	if not inventory_ids:
		return {}

	stock = get_connector().get_stock(list(set(inventory_ids.values())), location_id)

	now = now_datetime()
	live = {}
	for product, inventory_id in inventory_ids.items():
		live[product] = stock.get(inventory_id, 0)
		set_location_stock(product, location_id, live[product], sell_locations[location_id]["location_name"], now)
	return live


def live_stock_enabled() -> bool:
	"""True when the store can be asked for live stock (connection enabled with a default location)."""
	settings = frappe.db.get_singles_dict("Store Connection", cast=True)
	return bool(settings.enabled and settings.default_location_id)
