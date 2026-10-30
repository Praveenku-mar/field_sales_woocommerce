# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt
# ruff: noqa: RUF001, RUF002

"""
Commission engine: Field Order -> Commission Entry.

Platform-neutral: the store is reached only through ``get_connector()``.

Everything funnels through ``reconcile_order``. It reads the order's CURRENT state
from the store (never the webhook payload) and brings the entries in line with it, so
a replayed, late or out-of-order webhook gives the same result as a fresh one:

* entries are created once per order line (``dedupe_key``), whatever number of runs;
* ``Pending`` -> ``Earned`` when the plan's condition (paid / fulfilled) holds;
* a cancellation or refund sets the entry's status / ``reversed_amount`` while it is not on an
  approved statement; once it is, the entry is frozen and the loss is booked as a negative
  Adjustment instead, sized from the total the store says is lost, minus what earlier
  Adjustments already cover.

Entries on a Draft statement are changed in place; regenerate the statement to refresh its totals.
"""

import time
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, nowdate

from field_sales_woocommerce.commission import commission_enabled
from field_sales_woocommerce.connectors import get_connector
from field_sales_woocommerce.field_sales_woocommerce.doctype.commission_entry.commission_entry import (
	LOCKED_STATEMENT_STATUSES,
	make_dedupe_key,
)
from field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log import log_sync

ENTRY_FIELDS = [
	"name",
	"beneficiary_type",
	"sales_rep",
	"source_rep",
	"status",
	"earned_on",
	"commission_statement",
	"field_order_item",
	"platform_order",
	"product",
	"qty",
	"commission_plan",
	"currency",
	"base_amount",
	"amount",
	"reversed_amount",
]

# Financial statuses (neutral, see connectors.base) that count as paid for a "Paid" plan.
PAID_STATUSES = ("paid", "partially_refunded", "refunded")


def enqueue_create_entries(field_order: str):
	"""
	Queue ``create_entries`` once the current transaction commits.

	Deduplicated per order, so the push and the draft linker cannot queue it twice at once.
	Nothing is queued while commission is turned off.
	"""
	if not commission_enabled():
		return
	frappe.enqueue(
		"field_sales_woocommerce.commission.engine.create_entries",
		field_order=field_order,
		job_id=f"field_sales:create_entries:{field_order}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def create_entries(field_order: str):
	"""Create the commission entries of a Field Order that just got its platform order id."""
	external_order_id = frappe.db.get_value("Field Order", field_order, "external_order_id")
	if external_order_id:
		reconcile_order(external_order_id)


def reconcile_order(external_order_id: str) -> dict:
	"""
	Idempotent entry point for every order event: sync entries with the order's current store state.

	Safe under concurrent webhooks: the Field Order row is locked for the transaction, so a second
	run waits, then sees the first run's entries; the unique ``dedupe_key`` is the final guard.

	:param external_order_id: Plain platform order id.
	:return: ``{"note": ...}`` if ignored, else counts of what changed.
	"""
	if not commission_enabled():
		return {"note": "Ignored: commission is turned off"}
	external_order_id = str(external_order_id or "")
	name = frappe.db.get_value("Field Order", {"external_order_id": external_order_id, "docstatus": 1}, "name")
	if not name:
		return {"note": "Ignored: not a Field Sales order"}

	# Lock, then read the store: the run that gets the lock last also reads the latest state.
	frappe.db.get_value("Field Order", name, "name", for_update=True)
	field_order = frappe.get_doc("Field Order", name)

	start = time.monotonic()
	conn = get_connector()
	order = conn.get_order(external_order_id)
	if not order:
		return {"note": "Ignored: order not found on the store"}

	ctx = _Context(field_order, order)
	created = ctx.create_missing_entries()
	updated, adjusted = ctx.apply_state()

	result = {"field_order": name, "created": created, "updated": updated, "adjusted": adjusted}
	if created or updated or adjusted:
		log_sync(
			platform=field_order.platform or frappe.db.get_single_value("Store Connection", "platform"),
			operation="Commission",
			reference_doctype="Field Order",
			reference_name=name,
			status="Success",
			external_id=external_order_id,
			duration_ms=int((time.monotonic() - start) * 1000),
			response_payload=frappe.as_json(result),
		)
	return result


class _Context:
	"""One reconcile run: the Field Order, the store's order and the lookups both need."""

	def __init__(self, field_order, order: dict):
		self.field_order = field_order
		self.order = order
		self.external_id = str(order.get("external_order_id") or field_order.external_order_id)
		self.currency = order.get("currency") or frappe.db.get_default("currency")
		self.today = nowdate()
		self.precision = cint(frappe.get_precision("Commission Entry", "amount"))
		self._plans: dict[str, object] = {}
		self.pairs = self._map_lines()
		self.lines_by_item = {item.name: line for line, item in self.pairs}
		self.refunded_qty = self._refunded_quantities()

	# ------------------------------------------------------------------
	# Lines
	# ------------------------------------------------------------------

	def _map_lines(self) -> list[tuple[dict, object]]:
		"""
		Pair each store line that has a variant with the Field Order Item of that variant.

		Duplicates pair in row order. Custom lines (the draft's tax line has no variant) and
		lines that are not on the Field Order are left out.
		"""
		by_variant = defaultdict(list)
		for item in self.field_order.items:
			if item.external_variant_id:
				by_variant[str(item.external_variant_id)].append(item)

		used = defaultdict(int)
		pairs = []
		for line in self.order.get("lines") or []:
			variant = str(line.get("external_variant_id") or "")
			bucket = by_variant.get(variant)
			if not variant or not bucket or used[variant] >= len(bucket):
				continue
			pairs.append((line, bucket[used[variant]]))
			used[variant] += 1
		return pairs

	def _refunded_quantities(self) -> dict[str, float]:
		refunded = defaultdict(float)
		for refund in self.order.get("refunds") or []:
			for line in refund.get("lines") or []:
				refunded[str(line.get("line_id"))] += flt(line.get("qty"))
		return refunded

	def _line_base(self, line: dict) -> tuple[float, float]:
		"""(base amount, discount %) of a line: price x qty less its discounts, no tax or shipping."""
		gross = flt(line.get("qty")) * flt(line.get("unit_price"))
		discount = flt(line.get("discount"))
		pct = discount / gross * 100 if gross else 0
		return self.money(gross - discount), pct

	def _refund_fraction(self, line: dict) -> float:
		qty = flt(line.get("qty"))
		if qty <= 0:
			return 0
		return min(1, self.refunded_qty.get(str(line.get("line_id")), 0) / qty)

	def money(self, value) -> float:
		return flt(value, self.precision)

	def plan(self, name: str | None):
		if not name:
			return None
		if name not in self._plans:
			self._plans[name] = frappe.get_doc("Commission Plan", name) if frappe.db.exists("Commission Plan", name) else None
		return self._plans[name]

	# ------------------------------------------------------------------
	# Step 1: create missing entries
	# ------------------------------------------------------------------

	def create_missing_entries(self) -> int:
		"""Create the Order entries (rep and manager overrides) that do not exist yet; return how many."""
		rep = frappe.db.get_value(
			"Sales Rep",
			self.field_order.sales_rep,
			["commission_active", "commission_plan", "max_discount_pct", "reports_to"],
			as_dict=True,
		)
		plan = self.plan(rep.commission_plan) if rep else None
		if not rep or not cint(rep.commission_active) or not plan or not cint(plan.is_active) or not self.pairs:
			return 0

		existing = set(
			frappe.get_all(
				"Commission Entry",
				filters={"source_order": self.field_order.name, "entry_type": "Order"},
				pluck="dedupe_key",
			)
		)
		managers = self._override_managers(rep, plan)
		# Month-to-date base for a provisional slab rate; grows as this order's lines are added.
		running_base = self._month_to_date_base(self.field_order.sales_rep)

		bases = [self._line_base(line) for line, _item in self.pairs]
		total_base = self.money(sum(b for b, _p in bases))
		created = 0

		for index, ((line, item), (base, discount_pct)) in enumerate(zip(self.pairs, bases, strict=True)):
			if plan.rule_type != "Per Order" or index == 0:
				if plan.rule_type == "Per Order":
					entry_base = total_base
					entry_pct = self._order_discount_pct()
				else:
					entry_base, entry_pct = base, discount_pct
				values = self._rep_entry(rep, plan, item, line, entry_base, entry_pct, running_base)
				created += self._insert(values, existing)
			running_base += base

			for manager, manager_plan in managers:
				created += self._insert(self._override_entry(manager, manager_plan, item, line, base, discount_pct), existing)
		return created

	def _order_discount_pct(self) -> float:
		gross = sum(flt(line.get("qty")) * flt(line.get("unit_price")) for line, _item in self.pairs)
		discount = sum(flt(line.get("discount")) for line, _item in self.pairs)
		return discount / gross * 100 if gross else 0

	def _month_to_date_base(self, sales_rep: str) -> float:
		rows = frappe.get_all(
			"Commission Entry",
			filters={
				"sales_rep": sales_rep,
				"entry_type": "Order",
				"beneficiary_type": "Rep",
				"status": ("in", ("Pending", "Earned")),
				"posting_date": ("between", (get_first_day(self.today), get_last_day(self.today))),
			},
			fields=[{"SUM": "base_amount", "as": "total"}],
		)
		return flt(rows[0].total) if rows else 0

	def _override_managers(self, rep, plan) -> list[tuple[str, object]]:
		"""Managers up the chain (as many levels as the rep's plan says) that earn an override."""
		managers = []
		seen = {self.field_order.sales_rep}
		current = rep.reports_to
		for _level in range(cint(plan.override_levels)):
			if not current or current in seen:
				break
			seen.add(current)
			manager = frappe.db.get_value(
				"Sales Rep", current, ["commission_active", "commission_plan", "reports_to"], as_dict=True
			)
			if not manager:
				break
			manager_plan = self.plan(manager.commission_plan)
			if (
				cint(manager.commission_active)
				and manager_plan
				and cint(manager_plan.is_active)
				and flt(manager_plan.override_pct) > 0
			):
				managers.append((current, manager_plan))
			current = manager.reports_to
		return managers

	def _entry_common(self, item, line: dict) -> dict:
		return {
			"doctype": "Commission Entry",
			"entry_type": "Order",
			"status": "Pending",
			"posting_date": self.today,
			"source_order": self.field_order.name,
			"field_order_item": item.name,
			"platform_order": self.external_id,
			"product": item.product,
			"qty": flt(line.get("qty")),
			"currency": self.currency,
		}

	def _rep_entry(self, rep, plan, item, line: dict, base: float, discount_pct: float, running_base: float) -> dict:
		"""Values of the rep's own entry: rate from the plan, reduced for discount given."""
		multiplier = penalty_multiplier(discount_pct, rep.max_discount_pct, plan.penalty_factor)
		values = {
			**self._entry_common(item, line),
			"beneficiary_type": "Rep",
			"sales_rep": self.field_order.sales_rep,
			"commission_plan": plan.name,
			"base_amount": base,
			"discount_pct": flt(discount_pct, 4),
		}
		penalty_text = _penalty_text(discount_pct, rep.max_discount_pct, plan.penalty_factor)

		if plan.rule_type == "Per Order":
			amount = self.money(flt(plan.per_order_amount) * multiplier)
			values.update(
				amount=amount,
				calculation_note=f"{flt(plan.per_order_amount):,.2f} per order"
				+ (f" × (1 − {penalty_text})" if penalty_text else "")
				+ f" = {amount:,.2f}",
			)
			return values

		base_rate = resolve_rate(plan, item.product, self._collection(item.product), line, running_base + base)
		effective_rate = flt(base_rate * multiplier, 4)
		amount = self.money(base * effective_rate / 100)
		rate_text = f"{flt(base_rate, 4):g}%"
		if penalty_text:
			rate_text += f" × (1 − {penalty_text})"
		values.update(
			base_rate=base_rate,
			effective_rate=effective_rate,
			amount=amount,
			calculation_note=f"{rate_text} = {effective_rate:g}% of {base:,.2f} = {amount:,.2f}",
		)
		return values

	def _override_entry(self, manager: str, manager_plan, item, line: dict, base: float, discount_pct: float) -> dict:
		"""A manager's override on a rep's line: the manager plan's override % of the line base, no discount penalty."""
		pct = flt(manager_plan.override_pct)
		amount = self.money(base * pct / 100)
		return {
			**self._entry_common(item, line),
			"beneficiary_type": "Manager Override",
			"sales_rep": manager,
			"source_rep": self.field_order.sales_rep,
			"commission_plan": manager_plan.name,
			"base_amount": base,
			"discount_pct": flt(discount_pct, 4),
			"base_rate": pct,
			"effective_rate": pct,
			"amount": amount,
			"calculation_note": f"Override {pct:g}% of {base:,.2f} = {amount:,.2f}",
		}

	def _collection(self, product: str | None) -> str | None:
		return frappe.db.get_value("Platform Product", product, "collection") if product else None

	def _insert(self, values: dict, existing: set) -> int:
		"""Insert an entry unless its key exists (or a concurrent run just made it); 1 if created."""
		key = make_dedupe_key(
			values["field_order_item"], values["sales_rep"], values["beneficiary_type"], values["platform_order"]
		)
		if key in existing:
			return 0

		doc = frappe.get_doc(values)
		doc.flags.from_engine = True
		savepoint = "commission_entry_insert"
		frappe.db.savepoint(savepoint)
		try:
			doc.insert(ignore_permissions=True)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
			frappe.db.rollback(save_point=savepoint)
			return 0
		existing.add(key)
		return 1

	# ------------------------------------------------------------------
	# Step 2: bring every entry in line with the order's state
	# ------------------------------------------------------------------

	def apply_state(self) -> tuple[int, int]:
		"""Update entry statuses / reversals, or book Adjustments for frozen ones; return (updated, adjusted)."""
		entries = frappe.get_all(
			"Commission Entry",
			filters={"source_order": self.field_order.name, "entry_type": "Order"},
			fields=ENTRY_FIELDS,
			order_by="creation, name",
		)
		if not entries:
			return 0, 0

		statements = {e.commission_statement for e in entries if e.commission_statement}
		locked_statements = set(
			frappe.get_all(
				"Commission Statement",
				filters={"name": ("in", list(statements)), "status": ("in", LOCKED_STATEMENT_STATUSES)},
				pluck="name",
			)
			if statements
			else []
		)
		adjusted_so_far = defaultdict(float)
		for adj in frappe.get_all(
			"Commission Entry",
			filters={"source_order": self.field_order.name, "entry_type": "Adjustment", "status": ("!=", "Cancelled")},
			fields=["adjusts_entry", "amount"],
		):
			adjusted_so_far[adj.adjusts_entry] += flt(adj.amount)

		order_fraction = self._order_refund_fraction()
		updated = adjusted = 0

		for e in entries:
			fraction = self._entry_fraction(e, order_fraction)
			if e.commission_statement in locked_statements:
				adjusted += self._adjust_locked(e, fraction, adjusted_so_far[e.name])
			else:
				updated += self._update_open(e, fraction)
		return updated, adjusted

	def _order_refund_fraction(self) -> float:
		"""Share of the order's base amount that was refunded (for a Per Order entry)."""
		total = refunded = 0.0
		for line, _item in self.pairs:
			base, _pct = self._line_base(line)
			total += base
			refunded += base * self._refund_fraction(line)
		return refunded / total if total else 0

	def _entry_fraction(self, entry, order_fraction: float) -> float:
		"""Share of an entry lost to refunds: its line's refunded share, or the order's for a Per Order entry."""
		plan = self.plan(entry.commission_plan)
		if plan and plan.rule_type == "Per Order" and entry.beneficiary_type == "Rep":
			return order_fraction
		line = self.lines_by_item.get(entry.field_order_item)
		return self._refund_fraction(line) if line else 0

	def _earned_condition(self, entry) -> bool:
		plan = self.plan(entry.commission_plan)
		if plan and plan.commission_on == "Fulfilled":
			return self.order.get("fulfillment_status") == "fulfilled"
		return self.order.get("financial_status") in PAID_STATUSES

	def _update_open(self, entry, fraction: float) -> int:
		"""Set status / reversed amount of an entry that is not frozen; 1 if it changed."""
		if self.order.get("cancelled"):
			status, earned_on, reversed_amount = "Cancelled", entry.earned_on, flt(entry.reversed_amount)
		elif entry.status == "Cancelled":
			return 0
		else:
			status = entry.status
			if status == "Reversed":
				status = "Earned" if entry.earned_on else "Pending"
			earned_on = entry.earned_on
			if status == "Pending" and self._earned_condition(entry):
				status, earned_on = "Earned", self.today
			reversed_amount = self.money(flt(entry.amount) * fraction)
			if fraction >= 1:
				status = "Reversed"

		if (
			status == entry.status
			and earned_on == entry.earned_on
			and flt(reversed_amount, self.precision) == flt(entry.reversed_amount, self.precision)
		):
			return 0

		doc = frappe.get_doc("Commission Entry", entry.name)
		doc.flags.from_engine = True
		doc.update({"status": status, "earned_on": earned_on, "reversed_amount": reversed_amount})
		doc.save(ignore_permissions=True)
		return 1

	def _adjust_locked(self, entry, fraction: float, already_adjusted: float) -> int:
		"""
		Book the difference between what the store says is lost and what the frozen entry
		plus earlier Adjustments already cover, as a new negative Adjustment; 1 if one was made.
		"""
		if self.order.get("cancelled"):
			lost = flt(entry.amount)
			reason = _("Order {0} was cancelled on the store.").format(self.external_id)
		else:
			lost = self.money(flt(entry.amount) * fraction)
			reason = _("Refund on order {0}: {1}% of the line returned.").format(self.external_id, flt(fraction * 100, 2))

		# The frozen entry's own reversed_amount is already on its statement.
		wanted = -self.money(max(0, lost - flt(entry.reversed_amount)))
		difference = self.money(wanted - already_adjusted)
		if not difference:
			return 0

		doc = frappe.get_doc(
			{
				"doctype": "Commission Entry",
				"entry_type": "Adjustment",
				"status": "Earned",
				"posting_date": self.today,
				"earned_on": self.today,
				"beneficiary_type": entry.beneficiary_type,
				"sales_rep": entry.sales_rep,
				"source_rep": entry.source_rep,
				"source_order": self.field_order.name,
				"platform_order": self.external_id,
				"product": entry.product,
				"commission_plan": entry.commission_plan,
				"currency": entry.currency,
				"amount": difference,
				"adjusts_entry": entry.name,
				"adjustment_reason": reason,
			}
		)
		doc.flags.from_engine = True
		doc.insert(ignore_permissions=True)
		return 1


# ----------------------------------------------------------------------
# Rates (also used by statements.py)
# ----------------------------------------------------------------------


def resolve_rate(plan, product: str | None, collection: str | None, line: dict, month_to_date_base: float = 0) -> float:
	"""
	Base commission rate (%) of one line under ``plan``, before the discount penalty.

	Product Rate precedence: the exact product, then collection / product type, then tag, then the
	plan's default rate. Tiered Slab gives the provisional rate for the rep's month-to-date base;
	the final rate is set when the statement is built.
	"""
	if plan.rule_type == "Flat %":
		return flt(plan.flat_rate)

	if plan.rule_type == "Tiered Slab":
		return slab_rate(plan, month_to_date_base)

	if plan.rule_type == "Product Rate":
		for row in plan.rates:
			if row.match_type == "Product" and product and row.product == product:
				return flt(row.rate)

		categories = {c.strip().lower() for c in (collection, line.get("product_type")) if c}
		for row in plan.rates:
			if row.match_type == "Collection/Product Type" and (row.match_value or "").strip().lower() in categories:
				return flt(row.rate)

		tags = {str(t).strip().lower() for t in line.get("tags") or []}
		for row in plan.rates:
			if row.match_type == "Tag" and (row.match_value or "").strip().lower() in tags:
				return flt(row.rate)

		return flt(plan.default_rate)

	return 0


def slab_rate(plan, total: float) -> float:
	"""Rate (%) of the highest slab whose From Amount ``total`` has reached; 0 below the first slab."""
	rate = 0.0
	for row in sorted(plan.slabs, key=lambda r: flt(r.from_amount)):
		if flt(total) >= flt(row.from_amount):
			rate = flt(row.rate)
	return rate


def penalty_multiplier(discount_pct: float, max_discount_pct: float | None, penalty_factor: float | None) -> float:
	"""
	Share of the rate kept after the discount penalty: 1 - (discount / max discount) × factor, within 0..1.

	No max discount (0 / empty), no factor or no discount means no penalty.
	"""
	if flt(max_discount_pct) <= 0 or flt(penalty_factor) <= 0 or flt(discount_pct) <= 0:
		return 1.0
	return max(0.0, min(1.0, 1 - flt(discount_pct) / flt(max_discount_pct) * flt(penalty_factor)))


def _penalty_text(discount_pct: float, max_discount_pct, penalty_factor) -> str:
	if penalty_multiplier(discount_pct, max_discount_pct, penalty_factor) == 1.0:
		return ""
	return f"{flt(discount_pct, 2):g}/{flt(max_discount_pct):g} × {flt(penalty_factor):g}"
