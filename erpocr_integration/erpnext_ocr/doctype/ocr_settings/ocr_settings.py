# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OCRSettings(Document):
	def before_save(self):
		if not self.webhook_token:
			self.webhook_token = frappe.generate_hash(length=32)
