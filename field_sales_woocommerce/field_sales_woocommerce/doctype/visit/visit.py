# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.model.document import Document
from frappe.utils import flt, formatdate, get_datetime, get_fullname, getdate

from field_sales_woocommerce.utils.geo import DEFAULT_GEOFENCE_RADIUS_M, has_coords, haversine_m

# Written only by field_sales.api.check_in / check_out, never taken from the client.
CHECK_IN_FIELDS = ("check_in_time", "check_out_time", "latitude", "longitude", "location_accuracy_m")
CHECK_IN_STATUSES = ("In Progress", "Completed")
RETAILER_POSITION_FIELDS = ("retailer_latitude", "retailer_longitude")


class Visit(Document):
	def validate(self):
		self._guard_check_in_fields()
		self._validate_checkout_after_checkin()
		self._validate_completed_requires_checkin()
		self._compute_time_on_site()
		self._compute_distance_and_geofence()
		self._set_order_outcome()

	def after_insert(self):
		self._notify_rep()

	def on_update(self):
		self._update_retailer_last_visit()
		before = self.get_doc_before_save()
		if not before:
			return
		if before.sales_rep != self.sales_rep:
			self._notify_rep()
		elif before.visit_date and self.visit_date and getdate(before.visit_date) != getdate(self.visit_date):
			self._notify_rep(rescheduled=True)

	def _notify_rep(self, rescheduled: bool = False):
		"""
		Send the assigned rep a system notification for this visit. Nothing goes
		out when the rep has no user or is the one making the change; dedupe
		keeps re-saves quiet. Checked here because Frappe delivers "Alert"
		logs even to the sender.
		"""
		user = frappe.db.get_value("Sales Rep", self.sales_rep, "user") if self.sales_rep else None
		if not user or user == frappe.session.user:
			return

		if rescheduled:
			subject = _("Visit rescheduled: {0}, {1} to {2} — rescheduled by {3}").format(
				f"<b>{frappe.utils.escape_html(self.retailer_name or self.retailer)}</b>",
				frappe.utils.escape_html(self.city or ""),
				formatdate(self.visit_date),
				frappe.utils.escape_html(get_fullname(frappe.session.user)),
			)
		else:
			subject = _("New visit: {0}, {1} on {2} — assigned by {3}").format(
				f"<b>{frappe.utils.escape_html(self.retailer_name or self.retailer)}</b>",
				frappe.utils.escape_html(self.city or ""),
				formatdate(self.visit_date),
				frappe.utils.escape_html(get_fullname(frappe.session.user)),
			)
		enqueue_create_notification(
			[user],
			{
				"type": "Alert",
				"document_type": "Visit",
				"document_name": self.name,
				"from_user": frappe.session.user,
				"subject": subject,
				"email_content": subject,
			},
			dedupe_on=["document_type", "document_name", "subject"],
		)

	def _guard_check_in_fields(self):
		"""
		Check-in time, location and the In Progress / Completed statuses are set
		by the check-in API from server time. Block them being edited through
		the form or REST, except by a System Manager (corrections, data import).
		"""
		if self.flags.via_check_in_api or "System Manager" in frappe.get_roles():
			return

		before = self.get_doc_before_save()
		# The retailer's position at check-in is what the geofence was measured against: keep it
		# (also stops fetch_from moving it later when the Retailer's location changes).
		if before and before.check_in_time:
			for fieldname in RETAILER_POSITION_FIELDS:
				self.set(fieldname, before.get(fieldname))
		for fieldname in CHECK_IN_FIELDS:
			if self._normalized(fieldname, self) != self._normalized(fieldname, before):
				frappe.throw(
					_("{0} is set by Check In / Check Out and cannot be edited.").format(
						_(self.meta.get_label(fieldname))
					)
				)

		old_status = before.status if before else None
		if old_status == self.status:
			return
		# Cancelling a visit that is under way is allowed; nothing else touches these statuses.
		if old_status == "In Progress" and self.status == "Cancelled":
			return
		if old_status in CHECK_IN_STATUSES or self.status in CHECK_IN_STATUSES:
			frappe.throw(_("Use the Check In / Check Out buttons to change the visit status."))

	def _normalized(self, fieldname, doc):
		"""Field value in a comparable form: the client sends strings, the DB returns typed values."""
		value = doc.get(fieldname) if doc else None
		if fieldname in ("check_in_time", "check_out_time"):
			return get_datetime(value) if value else None
		return flt(value)

	def _validate_checkout_after_checkin(self):
		"""Check-out time, when present, must be strictly after check-in time."""
		if self.check_in_time and self.check_out_time:
			if get_datetime(self.check_out_time) <= get_datetime(self.check_in_time):
				frappe.throw(
					_("Check-out time ({0}) must be after check-in time ({1}).").format(
						self.check_out_time, self.check_in_time
					)
				)

	def _validate_completed_requires_checkin(self):
		"""A visit cannot be marked Completed without a check-in time."""
		if self.status == "Completed" and not self.check_in_time:
			frappe.throw(_("Status cannot be set to 'Completed' without a Check-in Time."))

	def _compute_time_on_site(self):
		"""
		Derive time_on_site_min from check-in / check-out timestamps.
		A started minute counts as a full one, so a short visit never shows 0.
		"""
		if self.check_in_time and self.check_out_time:
			delta = get_datetime(self.check_out_time) - get_datetime(self.check_in_time)
			self.time_on_site_min = math.ceil(delta.total_seconds() / 60)
		else:
			self.time_on_site_min = 0

	def _compute_distance_and_geofence(self):
		"""
		Straight-line distance (metres) between the check-in point and the
		retailer's registered coordinates, and whether that is inside the
		retailer's geofence. Skips if either location is missing.
		"""
		if not has_coords(self.latitude, self.longitude):
			return
		if not has_coords(self.retailer_latitude, self.retailer_longitude):
			return

		dist_m = haversine_m(
			flt(self.latitude),
			flt(self.longitude),
			flt(self.retailer_latitude),
			flt(self.retailer_longitude),
		)
		self.distance_from_retailer_m = round(dist_m, 2)
		self.within_geofence = 1 if dist_m <= get_geofence_radius(self.retailer) else 0

	def _set_order_outcome(self):
		"""
		order_taken follows the visit's Field Orders (draft or submitted).
		Without one, a completed visit needs the reason no order was taken.
		"""
		if not self.is_new():
			self.order_taken = 1 if has_orders(self.name) else 0

		if self.order_taken:
			self.no_order_reason = self.no_order_remarks = None
			return
		if self.status != "Completed":
			return
		if not self.no_order_reason:
			frappe.throw(
				_("No order was taken on this visit. Select the {0} before completing it.").format(
					_(self.meta.get_label("no_order_reason"))
				),
				title=_("Reason Required"),
			)
		if self.no_order_reason == "Other" and not (self.no_order_remarks or "").strip():
			frappe.throw(_("Describe why no order was taken."), title=_("Reason Required"))

	def _update_retailer_last_visit(self):
		"""
		When a visit is saved as Completed, stamp the linked Retailer's
		last_visit field with the check-in timestamp.
		Uses db.set_value to avoid triggering the Retailer's own hooks.
		"""
		if self.status == "Completed" and self.check_in_time and self.retailer:
			frappe.db.set_value(
				"Retailer",
				self.retailer,
				"last_visit",
				self.check_in_time,
				update_modified=False,
			)


def has_orders(visit: str, exclude: str | None = None) -> bool:
	"""Whether the visit has a draft or submitted Field Order (cancelled ones do not count)."""
	filters = {"visit": visit, "docstatus": ("<", 2)}
	if exclude:
		filters["name"] = ("!=", exclude)
	return bool(frappe.db.exists("Field Order", filters))


def update_order_taken(visit: str, exclude: str | None = None) -> None:
	"""Refresh order_taken after one of the visit's orders is saved, cancelled or deleted."""
	if not visit or not frappe.db.exists("Visit", visit):
		return
	taken = int(has_orders(visit, exclude))
	values = {"order_taken": taken}
	if taken:
		values.update(no_order_reason=None, no_order_remarks=None)
	frappe.db.set_value("Visit", visit, values, update_modified=False)


def get_geofence_radius(retailer: str) -> int:
	return frappe.db.get_value("Retailer", retailer, "geofence_radius_m") or DEFAULT_GEOFENCE_RADIUS_M


def on_doctype_update():
	# Rep Performance and list filters read visits by rep over a date range.
	frappe.db.add_index("Visit", ["sales_rep", "visit_date"])
