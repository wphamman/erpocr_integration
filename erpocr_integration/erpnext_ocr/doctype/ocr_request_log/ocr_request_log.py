# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OCRRequestLog(Document):
	@staticmethod
	def clear_old_logs(days=7):
		from frappe.query_builder import Interval
		from frappe.query_builder.functions import Now

		table = frappe.qb.DocType("OCR Request Log")
		frappe.db.delete(table, filters=(table.modified < (Now() - Interval(days=days))))
