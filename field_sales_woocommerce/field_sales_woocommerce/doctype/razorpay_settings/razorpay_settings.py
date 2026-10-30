# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from field_sales_woocommerce.payments.razorpay import webhook_url


class RazorpaySettings(Document):
	def onload(self):
		# Shown so it can be pasted into the Razorpay Dashboard: the address the viewer is using.
		self.webhook_url = webhook_url()

	def validate(self):
		self.key_id = (self.key_id or "").strip()
		self.callback_url = (self.callback_url or "").strip()
		self.webhook_url = webhook_url()
