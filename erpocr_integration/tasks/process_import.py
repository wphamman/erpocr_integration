# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _

from erpocr_integration.tasks.matching import match_item, match_supplier
from erpocr_integration.tasks.utils import log_and_raise_error


def process(raw_payload: str):
	"""
	Process a Nanonets webhook payload.

	This function is called asynchronously via frappe.enqueue() from the webhook endpoint.

	Steps:
	1. Parse the JSON payload
	2. Extract header fields (supplier, invoice number, dates, amounts)
	3. Extract line items from table predictions
	4. Check for duplicate imports (by nanonets_file_id)
	5. Create an OCR Import record
	6. Run supplier and item matching
	7. If fully matched, auto-create Purchase Invoice draft
	"""
	ocr_import_name = None

	# Webhook runs as Guest — elevate to Administrator for document creation
	frappe.set_user("Administrator")

	try:
		payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload

		# Extract the result — Nanonets sends either a dict or a list
		results = payload.get("result", {})
		if not results:
			frappe.log_error("OCR Integration Error", "Webhook payload has no results")
			return

		if isinstance(results, list):
			result = results[0]
		else:
			result = results

		# Safety check: only process approved documents
		approval_status = result.get("approval_status", "")
		if approval_status and approval_status != "approved":
			frappe.log_error(
				"OCR Integration Warning",
				f"Skipping non-approved document (status: {approval_status})",
			)
			return

		# Extract identifiers
		nanonets_file_id = result.get("id", "")
		nanonets_model_id = result.get("model_id", "")
		source_filename = result.get("input", "")

		# Dedup check
		if nanonets_file_id and frappe.db.exists("OCR Import", {"nanonets_file_id": nanonets_file_id}):
			frappe.log_error(
				"OCR Integration Warning",
				f"Duplicate webhook for file ID {nanonets_file_id} — skipping",
			)
			return

		# Parse predictions — use moderated_boxes if available (reviewed data), else predicted_boxes
		predictions = result.get("moderated_boxes") or result.get("prediction") or result.get("predicted_boxes") or []

		# Extract header fields and table data
		header_fields = _extract_header_fields(predictions)
		line_items = _extract_line_items(predictions)

		# Create OCR Import record
		ocr_import = frappe.get_doc({
			"doctype": "OCR Import",
			"status": "Pending",
			"source_filename": source_filename,
			"nanonets_file_id": nanonets_file_id,
			"nanonets_model_id": nanonets_model_id,
			"supplier_name_ocr": header_fields.get("supplier_name", ""),
			"invoice_number": header_fields.get("invoice_number", ""),
			"invoice_date": _parse_date(header_fields.get("invoice_date", "")),
			"due_date": _parse_date(header_fields.get("due_date", "")),
			"subtotal": _parse_amount(header_fields.get("subtotal", "")),
			"tax_amount": _parse_amount(header_fields.get("tax_amount", "")),
			"total_amount": _parse_amount(header_fields.get("total_amount", "")),
			"raw_payload": json.dumps(payload, indent=2),
			"items": [],
		})

		# Add line items
		for line in line_items:
			ocr_import.append("items", {
				"description_ocr": line.get("description", ""),
				"item_name": line.get("description", ""),
				"qty": _parse_float(line.get("quantity", "1")),
				"rate": _parse_amount(line.get("unit_price", "0")),
				"amount": _parse_amount(line.get("amount", "0")),
				"match_status": "Unmatched",
			})

		# Run supplier matching
		supplier_name_ocr = ocr_import.supplier_name_ocr
		if supplier_name_ocr:
			matched_supplier, match_status = match_supplier(supplier_name_ocr)
			if matched_supplier:
				ocr_import.supplier = matched_supplier
				ocr_import.supplier_match_status = match_status
			else:
				ocr_import.supplier_match_status = "Unmatched"
		else:
			ocr_import.supplier_match_status = "Unmatched"

		# Run item matching for each line
		for item in ocr_import.items:
			if item.description_ocr:
				matched_item, match_status = match_item(item.description_ocr)
				if matched_item:
					item.item_code = matched_item
					item.match_status = match_status
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

	except Exception:
		log_and_raise_error(exception=True, ocr_import_name=ocr_import_name)


def _extract_header_fields(predictions: list) -> dict:
	"""
	Extract header-level fields from Nanonets predictions.

	Looks for fields with type == "field" and maps common label names
	to our internal field names.
	"""
	# Map of common Nanonets label names to our field names
	# Users can have different labels in their Nanonets model — this covers common patterns
	label_map = {
		# Supplier
		"supplier_name": "supplier_name",
		"vendor_name": "supplier_name",
		"seller_name": "supplier_name",
		"company_name": "supplier_name",
		# Invoice number
		"invoice_number": "invoice_number",
		"invoice_no": "invoice_number",
		"invoice_id": "invoice_number",
		"bill_number": "invoice_number",
		# Invoice date
		"invoice_date": "invoice_date",
		"date": "invoice_date",
		"bill_date": "invoice_date",
		# Due date
		"due_date": "due_date",
		"payment_due_date": "due_date",
		# Amounts
		"subtotal": "subtotal",
		"sub_total": "subtotal",
		"net_amount": "subtotal",
		"tax_amount": "tax_amount",
		"tax": "tax_amount",
		"vat": "tax_amount",
		"vat_amount": "tax_amount",
		"total_tax": "tax_amount",
		"total_amount": "total_amount",
		"total": "total_amount",
		"grand_total": "total_amount",
		"amount_due": "total_amount",
		"invoice_amount": "total_amount",
		"total_due_amount": "total_amount",
	}

	fields = {}
	for pred in predictions:
		if pred.get("type") != "field":
			continue

		label = pred.get("label", "").lower().strip()
		value = pred.get("ocr_text", "").strip()

		if not value:
			continue

		mapped_field = label_map.get(label)
		if mapped_field and mapped_field not in fields:
			fields[mapped_field] = value

	return fields


def _extract_line_items(predictions: list) -> list[dict]:
	"""
	Extract line items from Nanonets table predictions.

	Table predictions have type == "table" with a cells[] array.
	Each cell has row, col, label, and text fields.
	"""
	items = []
	rows: dict[int, dict] = {}

	for pred in predictions:
		if pred.get("type") != "table":
			continue

		cells = pred.get("cells", [])
		if not cells:
			continue

		# Group cells by row
		for cell in cells:
			row_num = cell.get("row", 0)
			label = cell.get("label", "").lower().strip()
			text = cell.get("text", "").strip()

			if row_num == 0:
				# Skip header row
				continue

			if row_num not in rows:
				rows[row_num] = {}

			# Map common column labels to our field names
			if label in ("description", "item_description", "item", "product", "item_name", "particulars"):
				rows[row_num]["description"] = text
			elif label in ("quantity", "qty", "units"):
				rows[row_num]["quantity"] = text
			elif label in ("unit_price", "rate", "price", "unit_cost"):
				rows[row_num]["unit_price"] = text
			elif label in ("amount", "total", "line_total", "net_amount"):
				rows[row_num]["amount"] = text

	# Convert rows dict to list, skip empty rows
	for row_num in sorted(rows.keys()):
		row = rows[row_num]
		if row.get("description") or row.get("amount"):
			items.append(row)

	return items


def _parse_date(value: str) -> str | None:
	"""Parse a date string into YYYY-MM-DD format, or return None."""
	if not value:
		return None

	import re
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

	import re

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

	import re

	cleaned = re.sub(r"[^\d.\-]", "", str(value))
	try:
		result = float(cleaned)
		return result if result > 0 else 1.0
	except ValueError:
		return 1.0
