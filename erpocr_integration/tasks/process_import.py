# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import json
import re

import frappe
from frappe import _

from erpocr_integration.tasks.matching import (
	match_item, match_item_fuzzy,
	match_supplier, match_supplier_fuzzy,
	match_service_item,
)


def _clean_ocr_text(value: str) -> str:
	"""Normalize OCR-extracted text by removing common artifacts."""
	if not value:
		return ""
	# Replace newlines/carriage returns with empty string
	value = value.replace("\n", "").replace("\r", "")
	# Collapse multiple spaces into one
	value = re.sub(r"\s+", " ", value)
	# Remove spaces after opening and before closing brackets/parens
	# e.g. "( Pty )" → "(Pty)", "Star Pops ( Pty ) Ltd" → "Star Pops (Pty) Ltd"
	value = re.sub(r"\(\s+", "(", value)
	value = re.sub(r"\s+\)", ")", value)
	value = re.sub(r"\[\s+", "[", value)
	value = re.sub(r"\s+\]", "]", value)
	return value.strip()


def process_extracted_data(extracted_data: dict, source_type: str, uploaded_by: str = None):
	"""
	Universal entry point for OCR data from any source (Gemini, future sources).

	Args:
		extracted_data: dict with keys:
			- header_fields: {supplier_name, invoice_number, dates, amounts, ...}
			- line_items: [{description, product_code, qty, rate, amount}, ...]
			- raw_response: Original API response (for audit trail)
			- source_filename: Original filename
			- extraction_time: Time taken by extraction (seconds)
		source_type: "Gemini Manual Upload" or "Gemini Email"
		uploaded_by: User who initiated (optional)

	Returns:
		str: OCR Import record name
	"""
	ocr_import_name = None

	# Ensure we have admin permissions for document creation
	frappe.set_user("Administrator")

	try:
		header_fields = extracted_data.get("header_fields", {})
		line_items = extracted_data.get("line_items", [])
		raw_response = extracted_data.get("raw_response", "")
		source_filename = extracted_data.get("source_filename", "")
		extraction_time = extracted_data.get("extraction_time", 0.0)

		# Auto-set tax template based on whether tax was detected
		settings = frappe.get_cached_doc("OCR Settings")
		tax_amount = float(header_fields.get("tax_amount") or 0)
		tax_template = settings.default_tax_template if tax_amount > 0 else settings.non_vat_tax_template

		# Create OCR Import record
		ocr_import = frappe.get_doc({
			"doctype": "OCR Import",
			"status": "Pending",
			"source_filename": source_filename,
			"source_type": source_type,
			"uploaded_by": uploaded_by or frappe.session.user,
			"extraction_time": extraction_time,
			"supplier_name_ocr": header_fields.get("supplier_name", ""),
			"invoice_number": header_fields.get("invoice_number", ""),
			"invoice_date": header_fields.get("invoice_date"),
			"due_date": header_fields.get("due_date"),
			"subtotal": header_fields.get("subtotal", 0.0),
			"tax_amount": header_fields.get("tax_amount", 0.0),
			"total_amount": header_fields.get("total_amount", 0.0),
			"tax_template": tax_template,
			"raw_payload": raw_response,
			"items": [],
		})

		# Add line items
		for line in line_items:
			description = line.get("description", "")
			product_code = line.get("product_code", "")
			ocr_import.append("items", {
				"description_ocr": description,
				"item_name": product_code or description,
				"qty": line.get("quantity", 1.0),
				"rate": line.get("unit_price", 0.0),
				"amount": line.get("amount", 0.0),
				"match_status": "Unmatched",
			})

		# Run supplier matching
		fuzzy_threshold = settings.matching_threshold or 80
		supplier_name_ocr = ocr_import.supplier_name_ocr
		if supplier_name_ocr:
			matched_supplier, match_status = match_supplier(supplier_name_ocr)
			if matched_supplier:
				ocr_import.supplier = matched_supplier
				ocr_import.supplier_match_status = match_status
				# Update supplier tax_id if we have it and the supplier doesn't
				supplier_tax_id = header_fields.get("supplier_tax_id", "")
				if supplier_tax_id:
					existing_tax_id = frappe.db.get_value("Supplier", matched_supplier, "tax_id")
					if not existing_tax_id:
						frappe.db.set_value("Supplier", matched_supplier, "tax_id", supplier_tax_id)
			else:
				# Fuzzy fallback for supplier
				fuzzy_supplier, fuzzy_status, _score = match_supplier_fuzzy(
					supplier_name_ocr, fuzzy_threshold
				)
				if fuzzy_supplier:
					ocr_import.supplier = fuzzy_supplier
					ocr_import.supplier_match_status = fuzzy_status  # "Suggested"
				else:
					ocr_import.supplier_match_status = "Unmatched"
		else:
			ocr_import.supplier_match_status = "Unmatched"

		# Run item matching for each line
		for item in ocr_import.items:
			matched_item, match_status = None, "Unmatched"
			# Try product_code first (direct item_code match), then description
			if item.item_name and item.item_name != item.description_ocr:
				matched_item, match_status = match_item(item.item_name)
			if not matched_item and item.description_ocr:
				matched_item, match_status = match_item(item.description_ocr)

			# If no item match, try service matching (pattern → item + name + GL + CC)
			if not matched_item and item.description_ocr:
				service_match = match_service_item(item.description_ocr, supplier=ocr_import.supplier)
				if service_match:
					matched_item = service_match["item_code"]
					match_status = service_match["match_status"]
					item.expense_account = service_match.get("expense_account")
					item.cost_center = service_match.get("cost_center")
					if service_match.get("item_name"):
						item.item_name = service_match["item_name"]

			# Fuzzy fallback for item
			if not matched_item and item.description_ocr:
				fuzzy_item, fuzzy_status, _score = match_item_fuzzy(item.description_ocr, fuzzy_threshold)
				if fuzzy_item:
					matched_item = fuzzy_item
					match_status = fuzzy_status  # "Suggested"

			if matched_item:
				item.item_code = matched_item
				item.match_status = match_status

				# Even when item matched via alias/fuzzy, check service mapping for accounting fields
				if not item.expense_account and item.description_ocr:
					service_match = match_service_item(item.description_ocr, supplier=ocr_import.supplier)
					if service_match:
						item.expense_account = service_match.get("expense_account")
						item.cost_center = service_match.get("cost_center")
						if service_match.get("item_name"):
							item.item_name = service_match["item_name"]
			else:
				item.match_status = "Unmatched"

		# Insert the record — before_save will set the correct status
		ocr_import.insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep — explicit commit in enqueued job
		ocr_import_name = ocr_import.name

		# If fully matched, auto-create PI draft
		if ocr_import.status == "Matched":
			try:
				ocr_import.create_purchase_invoice()
				frappe.db.commit()  # nosemgrep
			except Exception:
				# PI creation failed — log but don't fail the import
				frappe.log_error(
					"OCR Integration Error",
					f"Auto PI creation failed for {ocr_import.name}\n{frappe.get_traceback()}",
				)

		return ocr_import_name

	except Exception:
		frappe.log_error("OCR Integration Error", frappe.get_traceback())
		raise


def _parse_date(value: str) -> str | None:
	"""Parse a date string into YYYY-MM-DD format, or return None."""
	if not value:
		return None

	from datetime import datetime

	# Normalize whitespace — OCR often adds extra spaces (e.g., "February 9 , 2026")
	value = re.sub(r"\s+", " ", value.strip())
	# Remove spaces before punctuation (e.g., "9 , 2026" → "9, 2026")
	value = re.sub(r"\s+,", ",", value)
	formats = [
		"%Y-%m-%d",      # 2024-01-15
		"%d/%m/%Y",      # 15/01/2024
		"%m/%d/%Y",      # 01/15/2024
		"%d-%m-%Y",      # 15-01-2024
		"%d %B %Y",      # 15 January 2024
		"%d %b %Y",      # 15 Jan 2024
		"%B %d, %Y",     # January 15, 2024
		"%b %d, %Y",     # Jan 15, 2024
	]

	for fmt in formats:
		try:
			dt = datetime.strptime(value, fmt)
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			continue

	# Try to extract a date-like pattern
	match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
	if match:
		return match.group(0)

	return None


def _parse_amount(value: str) -> float:
	"""Parse a currency amount string to float."""
	if not value:
		return 0.0

	# Remove currency symbols, spaces, and thousands separators
	cleaned = re.sub(r"[^\d.,\-]", "", str(value))

	if not cleaned:
		return 0.0

	# Handle comma as decimal separator (e.g., 1.234,56 → 1234.56)
	if "," in cleaned and "." in cleaned:
		if cleaned.rindex(",") > cleaned.rindex("."):
			# Comma is decimal separator (European format)
			cleaned = cleaned.replace(".", "").replace(",", ".")
		else:
			# Comma is thousands separator
			cleaned = cleaned.replace(",", "")
	elif "," in cleaned:
		# Could be either — if exactly 2 digits after comma, treat as decimal
		parts = cleaned.split(",")
		if len(parts) == 2 and len(parts[1]) == 2:
			cleaned = cleaned.replace(",", ".")
		else:
			cleaned = cleaned.replace(",", "")

	try:
		return float(cleaned)
	except ValueError:
		return 0.0


def _parse_float(value: str) -> float:
	"""Parse a numeric string to float."""
	if not value:
		return 1.0

	cleaned = re.sub(r"[^\d.\-]", "", str(value))
	try:
		result = float(cleaned)
		return result if result > 0 else 1.0
	except ValueError:
		return 1.0
