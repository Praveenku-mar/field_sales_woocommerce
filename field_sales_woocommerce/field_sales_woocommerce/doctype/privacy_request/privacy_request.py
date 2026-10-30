# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PrivacyRequest(Document):
	"""
	One store privacy request (data request, customer redact, shop redact) and what was done
	about it. Created by ``field_sales/shopify/privacy.py``; the data export is a private File
	attached to it.
	"""

	pass
