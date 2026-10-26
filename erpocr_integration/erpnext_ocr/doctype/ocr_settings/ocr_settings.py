# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class OCRSettings(Document):
	def validate(self):
		self._validate_billing_alert_recipients()

	def _validate_billing_alert_recipients(self):
		from frappe.utils import validate_email_address

		from erpocr_integration.tasks.billing_alert import parse_email_list

		bad = [
			a for a in parse_email_list(self.get("billing_alert_recipients")) if not validate_email_address(a)
		]
		if bad:
			frappe.throw(
				_(
					"These are not valid email addresses: {0}. Check the list under 'Also Email These People'."
				).format(", ".join(bad))
			)
