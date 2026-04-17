# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class OCRStatement(Document):
	@frappe.whitelist()
	def mark_reviewed(self):
		"""Mark this statement as reviewed by the user."""
		if not frappe.has_permission("OCR Statement", "write", self.name):
			frappe.throw(_("You don't have permission to modify this statement."))
		if self.status != "Reconciled":
			frappe.throw(_("Can only mark Reconciled statements as Reviewed."))
		self.status = "Reviewed"
		self.save()
		frappe.msgprint(_("Statement marked as reviewed."), indicator="green")
