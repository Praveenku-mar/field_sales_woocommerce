# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from field_sales_woocommerce.permissions import clear_allowed_reps_cache


class SalesRep(Document):
	def validate(self):
		if self.max_discount_pct is not None and not 0 <= flt(self.max_discount_pct) <= 100:
			frappe.throw(_("Max Discount % must be between 0 and 100."))

	def on_update(self):
		# The hierarchy may have changed; later checks in this request must see it.
		clear_allowed_reps_cache()

	def on_trash(self):
		clear_allowed_reps_cache()
