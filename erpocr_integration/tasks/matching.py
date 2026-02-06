# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe


def match_supplier(ocr_text: str) -> tuple[str | None, str]:
	"""
	Attempt to match an OCR-extracted supplier name to an ERPNext Supplier.

	Matching priority:
	1. Exact match in OCR Supplier Alias table (learned from previous confirmations)
	2. Exact match against Supplier.supplier_name

	Returns:
		tuple: (supplier_name or None, match_status)
	"""
	if not ocr_text:
		return None, "Unmatched"

	ocr_text_stripped = ocr_text.strip()

	# 1. Check alias table (exact match)
	alias = frappe.db.get_value(
		"OCR Supplier Alias",
		{"ocr_text": ocr_text_stripped},
		"supplier",
	)
	if alias:
		return alias, "Auto Matched"

	# 2. Check Supplier master (exact name match)
	supplier = frappe.db.get_value(
		"Supplier",
		{"supplier_name": ocr_text_stripped},
		"name",
	)
	if supplier:
		return supplier, "Auto Matched"

	# 3. Also try matching against the Supplier document name directly
	if frappe.db.exists("Supplier", ocr_text_stripped):
		return ocr_text_stripped, "Auto Matched"

	return None, "Unmatched"


def match_item(ocr_text: str) -> tuple[str | None, str]:
	"""
	Attempt to match an OCR-extracted item description to an ERPNext Item.

	Matching priority:
	1. Exact match in OCR Item Alias table (learned from previous confirmations)
	2. Exact match against Item.item_name
	3. Exact match against Item.name (item_code)

	Returns:
		tuple: (item_code or None, match_status)
	"""
	if not ocr_text:
		return None, "Unmatched"

	ocr_text_stripped = ocr_text.strip()

	# 1. Check alias table (exact match)
	alias = frappe.db.get_value(
		"OCR Item Alias",
		{"ocr_text": ocr_text_stripped},
		"item_code",
	)
	if alias:
		return alias, "Auto Matched"

	# 2. Check Item master (exact item_name match)
	item = frappe.db.get_value(
		"Item",
		{"item_name": ocr_text_stripped},
		"name",
	)
	if item:
		return item, "Auto Matched"

	# 3. Also try matching against item_code directly
	if frappe.db.exists("Item", ocr_text_stripped):
		return ocr_text_stripped, "Auto Matched"

	return None, "Unmatched"
