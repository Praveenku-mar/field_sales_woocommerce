# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PaymentTransaction(Document):
	"""
	One step in an order's payment history: a Razorpay payment link, a store checkout's Razorpay
	order, a payment (captured or failed), a refund, or a manual cash / bank payment.

	Written only by server code (``payments/transactions.py -> record()``), one row per
	``transaction_key``; read-only in the desk. Reps see their orders' rows through the app APIs.
	"""

	pass
