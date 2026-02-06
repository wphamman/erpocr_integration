# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _


def log_and_raise_error(exception=None, error_text=None, ocr_import_name=None):
	"""
	Log an error with full context and raise a user-friendly message.

	Pattern adopted from woocommerce_fusion.
	"""
	error_message = ""
	if exception:
		error_message = frappe.get_traceback()
	if error_text:
		error_message += f"\n{error_text}" if error_message else error_text
	if ocr_import_name:
		error_message += f"\nOCR Import: {ocr_import_name}"

	log = frappe.log_error("OCR Integration Error", error_message)

	# Link error log to OCR Import if we have one
	if ocr_import_name:
		try:
			frappe.db.set_value("OCR Import", ocr_import_name, {
				"status": "Error",
				"error_log": log.name,
			})
		except Exception:
			pass

	log_link = frappe.utils.get_link_to_form("Error Log", log.name)
	frappe.throw(msg=_("OCR processing failed. See Error Log {0}").format(log_link))
