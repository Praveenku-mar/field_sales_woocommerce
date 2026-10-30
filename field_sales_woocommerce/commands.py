# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Bench commands. Frappe loads ``commands`` from this module (``bench --site <site> <command>``).

``field-sales-demo``: demo data and reviewer logins for the App Store review / screencast.
"""

import random

import click
from frappe.commands import get_site, pass_context

DEMO_TAG = "[demo]"
MANAGER_EMAIL = "demo.manager@example.com"
REP_EMAIL = "demo.rep@example.com"
PLAN_NAME = "Demo Flat 2%"
RETAILER_COUNT = 10
ORDER_COUNT = 15
VISIT_DAYS = 30

RETAILER_NAMES = (
	"Green Leaf Grocers", "Corner Basket", "Sunrise Mart", "Daily Needs Store", "Fresh Fields",
	"City Pantry", "Harbour Provisions", "Maple Street Market", "Riverside Stores", "Hilltop Supplies",
)  # fmt: skip


@click.command("field-sales-demo")
@pass_context
def field_sales_demo(context):
	"""
	Create demo data: a manager and a rep (passwords from site_config field_sales_demo_passwords),
	10 retailers, visits over the last 30 days, 15 submitted orders and a commission plan, using
	products already synced from the connected store. Safe to run again (tops up what is missing).
	Refuses unless developer_mode is on or the site has no submitted orders other than demo ones.
	"""
	import frappe

	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()
	try:
		frappe.set_user("Administrator")
		summary = create_demo_data()
		frappe.db.commit()
		click.echo(frappe.as_json(summary))
	except DemoRefused as e:
		frappe.db.rollback()
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()


class DemoRefused(Exception):
	pass


def create_demo_data() -> dict:
	"""The command's work, callable from tests. Does not commit."""
	import frappe

	_check_allowed()
	passwords = frappe.conf.get("field_sales_demo_passwords") or {}
	if not (passwords.get("manager") and passwords.get("rep")):
		raise DemoRefused(
			'Set site_config field_sales_demo_passwords to {"manager": "...", "rep": "..."} first '
			"(bench --site <site> set-config --parse field_sales_demo_passwords '{...}')."
		)
	products = _demo_products()
	if not products:
		raise DemoRefused("No sellable products yet: connect the store and sync products first.")

	rng = random.Random(42)
	plan = _ensure_plan()
	manager = _ensure_user(MANAGER_EMAIL, "Demo", "Manager", "Sales Manager", passwords["manager"])
	rep_user = _ensure_user(REP_EMAIL, "Demo", "Rep", "Field Sales Rep", passwords["rep"])
	manager_rep = _ensure_rep(manager, "Demo Manager", None, None)
	rep = _ensure_rep(rep_user, "Demo Rep", manager_rep, plan)
	retailers = _ensure_retailers(rep, rng)
	visits = _ensure_visits(rep, retailers, rng)
	orders = _ensure_orders(rep, retailers, products, rng)
	return {
		"manager": MANAGER_EMAIL,
		"rep": REP_EMAIL,
		"commission_plan": plan,
		"retailers": len(retailers),
		"visits": visits,
		"submitted_orders": orders,
	}


def _check_allowed() -> None:
	import frappe

	if frappe.conf.developer_mode:
		return
	real_orders = frappe.db.count("Field Order", {"docstatus": 1, "remarks": ("not like", f"%{DEMO_TAG}%")})
	if real_orders:
		raise DemoRefused(
			f"This site has {real_orders} submitted orders: demo data is only added to empty sites "
			"or with developer_mode on."
		)


def _demo_products() -> list[dict]:
	import frappe

	rows = frappe.get_all(
		"Platform Product",
		filters={"status": "Active", "price": (">", 0)},
		fields=["name", "price", "track_inventory", "available_qty"],
		order_by="product_title asc",
		limit=50,
	)
	return [r for r in rows if not r.track_inventory or (r.available_qty or 0) >= 10]


def _ensure_plan() -> str:
	import frappe

	if not frappe.db.exists("Commission Plan", PLAN_NAME):
		frappe.get_doc(
			{
				"doctype": "Commission Plan",
				"plan_name": PLAN_NAME,
				"is_active": 1,
				"rule_type": "Flat %",
				"commission_on": "Paid",
				"flat_rate": 2,
				"description": f"{DEMO_TAG} 2% of each paid order",
			}
		).insert(ignore_permissions=True)
	return frappe.db.get_value("Commission Plan", {"plan_name": PLAN_NAME}, "name")


def _ensure_user(email, first, last, role, password) -> str:
	import frappe
	from frappe.utils.password import update_password

	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first,
				"last_name": last,
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	else:
		frappe.get_doc("User", email).add_roles(role)
	update_password(email, password)
	return email


def _ensure_rep(user, full_name, reports_to, plan) -> str:
	import frappe

	name = frappe.db.get_value("Sales Rep", {"user": user})
	values = {"full_name": full_name, "active": 1, "reports_to": reports_to}
	if plan:
		values.update(commission_plan=plan, commission_active=1)
	if name:
		frappe.db.set_value("Sales Rep", name, values)
		return name
	return (
		frappe.get_doc({"doctype": "Sales Rep", "user": user, **values}).insert(ignore_permissions=True).name
	)


def _ensure_retailers(rep, rng) -> list[str]:
	import frappe

	from field_sales_woocommerce.field_sales_woocommerce.doctype.retailer.retailer import country_code_of, get_default_country
	from field_sales_woocommerce.utils.phone import is_valid

	country = get_default_country()
	code = country_code_of(country) or "IN"
	names = []
	for i, retailer_name in enumerate(RETAILER_NAMES[:RETAILER_COUNT]):
		existing = frappe.db.get_value("Retailer", {"retailer_name": retailer_name})
		if existing:
			names.append(existing)
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": retailer_name,
				"owner_name": f"Demo Owner {i + 1}",
				"phone": _demo_phone(code, i, is_valid),
				"email": f"retailer{i + 1}@example.com",
				"retailer_type": rng.choice(["Kirana", "Supermarket", "Wholesaler", "Other"]),
				"address_line1": f"{10 + i} Market Road",
				"city": "Demo City",
				"country": country,
				"sales_rep": rep,
				"status": "Active",
			}
		)
		names.append(doc.insert(ignore_permissions=True).name)
	return names


def _demo_phone(country_code, index, is_valid) -> str:
	"""A valid-looking mobile for the country: the library's example with the last digits varied."""
	import phonenumbers

	example = phonenumbers.example_number_for_type(country_code, phonenumbers.PhoneNumberType.MOBILE)
	if not example:
		return "98765 432%02d" % index
	national = str(example.national_number)
	candidate = national[:-2] + "%02d" % (10 + index)
	return candidate if is_valid(candidate, country_code) else national


def _ensure_visits(rep, retailers, rng) -> int:
	"""About two visits a working day over the last 30 days (completed), and two planned for today."""
	import frappe
	from frappe.utils import add_days, add_to_date, get_datetime, getdate, nowdate

	existing = frappe.db.count("Visit", {"sales_rep": rep, "notes": ("like", f"%{DEMO_TAG}%")})
	if existing:
		return existing
	today = getdate(nowdate())
	created = 0
	for days_ago in range(VISIT_DAYS, -1, -1):
		day = add_days(today, -days_ago)
		if getdate(day).weekday() == 6:  # Sunday
			continue
		for slot in range(2):
			retailer = rng.choice(retailers)
			visit = frappe.get_doc(
				{
					"doctype": "Visit",
					"sales_rep": rep,
					"retailer": retailer,
					"visit_date": day,
					"visit_purpose": rng.choice(["Order Taking", "Collection", "Merchandising", "Follow-up"]),
					"notes": f"{DEMO_TAG} visit",
				}
			).insert(ignore_permissions=True)
			created += 1
			if days_ago:
				check_in = add_to_date(
					get_datetime(f"{day} 10:00:00"), hours=slot * 3, minutes=rng.randint(0, 50)
				)
				frappe.db.set_value(
					"Visit",
					visit.name,
					{
						"status": "Completed",
						"check_in_time": check_in,
						"check_out_time": add_to_date(check_in, minutes=rng.randint(15, 45)),
						"within_geofence": 1,
					},
					update_modified=False,
				)
	return created


def _ensure_orders(rep, retailers, products, rng) -> int:
	"""Submitted orders until there are ORDER_COUNT demo ones (each submit checks live stock and syncs)."""
	import frappe
	from frappe.utils import add_days, nowdate

	done = frappe.db.count("Field Order", {"docstatus": 1, "remarks": ("like", f"%{DEMO_TAG}%")})
	for i in range(done, ORDER_COUNT):
		items = [
			{"product": p.name, "qty": rng.randint(1, 3), "rate": p.price}
			for p in rng.sample(products, k=min(len(products), rng.randint(1, 3)))
		]
		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": rng.choice(retailers),
				"sales_rep": rep,
				"order_date": add_days(nowdate(), -rng.randint(0, VISIT_DAYS - 1)),
				"remarks": f"{DEMO_TAG} order {i + 1}",
				"items": items,
			}
		).insert(ignore_permissions=True)
		order.submit()
	return ORDER_COUNT


commands = [field_sales_demo]
