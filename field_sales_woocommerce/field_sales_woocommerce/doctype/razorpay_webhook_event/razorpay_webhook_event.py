# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RazorpayWebhookEvent(Document):
	"""One Razorpay webhook delivery: written by ``payments/razorpay.py -> webhook()``, read-only for users."""

	pass
