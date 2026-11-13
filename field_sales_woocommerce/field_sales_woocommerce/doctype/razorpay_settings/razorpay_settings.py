# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from field_sales_woocommerce.payments.razorpay import webhook_url


class RazorpaySettings(Document):
	def onload(self):
		# Shown so it can be pasted into the Razorpay Dashboard: the address the viewer is using.
		self.webhook_url = webhook_url()

	def validate(self):
		self.key_id = (self.key_id or "").strip()
		self.callback_url = (self.callback_url or "").strip()
		self._validate_callback_url()
		self.webhook_url = webhook_url()

	def _validate_callback_url(self):
		"""Where Razorpay sends the customer after paying: https, or http in developer mode."""
		if not self.callback_url:
			return
		scheme, _sep, rest = self.callback_url.partition("://")
		allowed = ("https", "http") if frappe.conf.developer_mode else ("https",)
		if scheme.lower() not in allowed or not rest or any(c.isspace() for c in self.callback_url):
			frappe.throw(_("Callback URL must be a full https:// address."))
