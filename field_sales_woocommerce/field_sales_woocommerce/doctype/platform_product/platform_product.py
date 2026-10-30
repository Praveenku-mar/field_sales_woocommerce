# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PlatformProduct(Document):
	def on_trash(self):
		# Product Stock links to the product, which would otherwise block the delete.
		for name in frappe.get_all("Product Stock", filters={"product": self.name}, pluck="name"):
			frappe.delete_doc("Product Stock", name, ignore_permissions=True, force=True)
