# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import base64
import json
import time

import frappe
import requests
from frappe import _


def extract_invoice_data(pdf_content: bytes, filename: str) -> dict:
	"""
	Extract invoice data from PDF using Gemini 2.5 Flash API.

	Args:
		pdf_content: Raw PDF file bytes
		filename: Original filename for logging

	Returns:
		dict: {
			"header_fields": {supplier_name, invoice_number, dates, amounts, ...},
			"line_items": [{description, product_code, qty, rate, amount}, ...],
			"raw_response": str,  # Original Gemini response
			"source_filename": str
		}

	Raises:
		Exception: If API call fails or returns invalid data
	"""
	start_time = time.time()

	# Get API key from OCR Settings
	settings = frappe.get_single("OCR Settings")
	api_key = settings.get_password("gemini_api_key")

	if not api_key:
		frappe.throw(_("Gemini API key not configured in OCR Settings"))

	model = settings.gemini_model or "gemini-2.5-flash"

	# Build prompt and schema
	prompt = _build_extraction_prompt()
	schema = _build_extraction_schema()

	# Call Gemini API with retry logic
	try:
		response_data = _call_gemini_api(pdf_content, prompt, schema, api_key, model)
	except Exception as e:
		frappe.log_error(f"Gemini API call failed for {filename}\n{frappe.get_traceback()}")
		raise Exception(f"Failed to call Gemini API: {str(e)}")

	# Validate response
	is_valid, error_msg = _validate_gemini_response(response_data)
	if not is_valid:
		frappe.log_error(f"Invalid Gemini response for {filename}\n{error_msg}\n{json.dumps(response_data, indent=2)}")
		raise Exception(f"Invalid Gemini response: {error_msg}")

	# Extract the JSON content from response
	try:
		candidates = response_data.get("candidates", [])
		if not candidates:
			raise Exception("No candidates in Gemini response")

		content = candidates[0].get("content", {})
		parts = content.get("parts", [])
		if not parts:
			raise Exception("No parts in Gemini response")

		text = parts[0].get("text", "")
		if not text:
			raise Exception("Empty text in Gemini response")

		# Parse JSON from text
		extracted_data = json.loads(text)

	except Exception as e:
		frappe.log_error(f"Failed to parse Gemini response for {filename}\n{frappe.get_traceback()}\n{json.dumps(response_data, indent=2)}")
		raise Exception(f"Failed to parse Gemini response: {str(e)}")

	# Transform to OCR Import format
	result = _transform_to_ocr_import_format(extracted_data, filename)
	result["raw_response"] = json.dumps(response_data, indent=2)
	result["extraction_time"] = time.time() - start_time

	return result


def _build_extraction_prompt() -> str:
	"""Construct prompt for Gemini API invoice extraction."""
	return """Extract the following information from this invoice PDF:

**Supplier Information:**
- Full supplier/vendor name exactly as printed on the invoice
- Supplier tax ID/VAT number/registration number (if present)

**Invoice Details:**
- Invoice number (the unique identifier for this invoice)
- Invoice date (format: YYYY-MM-DD)
- Due date / Payment due date (format: YYYY-MM-DD, if present)

**Amounts:**
- Subtotal (pre-tax amount, if shown separately)
- Tax amount (VAT/GST/sales tax total, if shown separately)
- Total amount (final invoice total - this is REQUIRED)

**Line Items:**
For each product or service line item in the invoice table, extract:
- Description (full text description of the item/service)
- Product code / SKU / Item code (if present - may be in a separate column from description)
- Quantity (numeric quantity ordered/delivered)
- Unit price / Rate (price per unit)
- Line amount / Total (total for this line = quantity × unit price)

**Important Instructions:**
- Return all dates in YYYY-MM-DD format (convert from any format you see)
- Return all amounts as numeric values WITHOUT currency symbols (e.g., 1234.56 not R1,234.56)
- If a field is not found or not visible, return null for that field
- For line items, skip any header rows - only extract actual data rows
- Product code may appear in a separate column from the description - extract both if available
- If you see multiple tables, extract all line items from all tables
- Be precise with amounts - do not round or approximate

Return the extracted data as structured JSON matching the provided schema."""


def _build_extraction_schema() -> dict:
	"""Build JSON schema for Gemini structured output."""
	return {
		"type": "object",
		"properties": {
			"supplier_name": {
				"type": "string",
				"description": "Full name of the supplier/vendor"
			},
			"supplier_tax_id": {
				"type": ["string", "null"],
				"description": "Supplier's tax ID/VAT number/registration number"
			},
			"invoice_number": {
				"type": "string",
				"description": "Invoice number or identifier"
			},
			"invoice_date": {
				"type": "string",
				"description": "Invoice date in YYYY-MM-DD format"
			},
			"due_date": {
				"type": ["string", "null"],
				"description": "Payment due date in YYYY-MM-DD format"
			},
			"subtotal": {
				"type": ["number", "null"],
				"description": "Subtotal amount before tax"
			},
			"tax_amount": {
				"type": ["number", "null"],
				"description": "Total tax/VAT amount"
			},
			"total_amount": {
				"type": "number",
				"description": "Final total amount of the invoice"
			},
			"line_items": {
				"type": "array",
				"description": "Array of line items from the invoice",
				"items": {
					"type": "object",
					"properties": {
						"description": {
							"type": "string",
							"description": "Description of the item or service"
						},
						"product_code": {
							"type": ["string", "null"],
							"description": "Product code, SKU, or item code if present"
						},
						"quantity": {
							"type": "number",
							"description": "Quantity ordered/delivered"
						},
						"unit_price": {
							"type": "number",
							"description": "Price per unit"
						},
						"amount": {
							"type": "number",
							"description": "Total for this line (quantity × unit_price)"
						}
					},
					"required": ["description", "quantity", "unit_price", "amount"]
				}
			}
		},
		"required": ["supplier_name", "invoice_number", "invoice_date", "total_amount", "line_items"]
	}


def _call_gemini_api(pdf_content: bytes, prompt: str, schema: dict, api_key: str, model: str) -> dict:
	"""
	Call Gemini API with PDF and prompt, return parsed JSON response.
	Includes retry logic for rate limits.
	"""
	url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

	# Encode PDF as base64
	pdf_base64 = base64.b64encode(pdf_content).decode('utf-8')

	payload = {
		"contents": [{
			"parts": [
				{"text": prompt},
				{
					"inline_data": {
						"mime_type": "application/pdf",
						"data": pdf_base64
					}
				}
			]
		}],
		"generationConfig": {
			"response_mime_type": "application/json",
			"response_schema": schema
		}
	}

	headers = {
		"Content-Type": "application/json",
		"x-goog-api-key": api_key
	}

	# Retry with exponential backoff
	max_retries = 3
	for attempt in range(max_retries):
		try:
			response = requests.post(url, json=payload, headers=headers, timeout=60)
			response.raise_for_status()
			return response.json()

		except requests.exceptions.HTTPError as e:
			# Rate limit (429) or server error (5xx) - retry
			if e.response and e.response.status_code in (429, 500, 503) and attempt < max_retries - 1:
				wait_time = 2 ** attempt  # 1s, 2s, 4s
				frappe.log_error(
					"Gemini API Rate Limit",
					f"Rate limit or server error (status {e.response.status_code}), retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
				)
				time.sleep(wait_time)
				continue
			else:
				# Non-retryable error or max retries reached
				raise

		except requests.exceptions.Timeout:
			if attempt < max_retries - 1:
				wait_time = 2 ** attempt
				frappe.log_error("Gemini API Timeout", f"Request timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
				time.sleep(wait_time)
				continue
			else:
				raise

	# Should not reach here
	raise Exception("Failed to call Gemini API after retries")


def _validate_gemini_response(response: dict) -> tuple[bool, str]:
	"""
	Validate Gemini API response structure.

	Returns:
		tuple: (is_valid, error_message)
	"""
	if not response:
		return False, "Empty response"

	if "candidates" not in response:
		return False, "Missing 'candidates' in response"

	candidates = response.get("candidates", [])
	if not candidates:
		return False, "Empty candidates array"

	if len(candidates) == 0:
		return False, "No candidates in response"

	content = candidates[0].get("content", {})
	if not content:
		return False, "Missing 'content' in first candidate"

	parts = content.get("parts", [])
	if not parts:
		return False, "Missing 'parts' in content"

	if len(parts) == 0:
		return False, "Empty parts array"

	text = parts[0].get("text", "")
	if not text:
		return False, "Empty text in first part"

	# Try to parse as JSON
	try:
		json.loads(text)
	except json.JSONDecodeError as e:
		return False, f"Invalid JSON in response text: {str(e)}"

	return True, ""


def _transform_to_ocr_import_format(gemini_data: dict, filename: str) -> dict:
	"""
	Transform Gemini response to OCR Import dict format.
	Applies same cleaning and parsing as Nanonets pipeline.
	"""
	# Import parsers from process_import (reuse existing code)
	from erpocr_integration.tasks.process_import import _clean_ocr_text, _parse_amount, _parse_date, _parse_float

	# Extract and clean header fields
	header_fields = {
		"supplier_name": _clean_ocr_text(gemini_data.get("supplier_name", "")),
		"supplier_tax_id": _clean_ocr_text(gemini_data.get("supplier_tax_id", "")),
		"invoice_number": _clean_ocr_text(gemini_data.get("invoice_number", "")),
		"invoice_date": _parse_date(gemini_data.get("invoice_date", "")),
		"due_date": _parse_date(gemini_data.get("due_date", "")),
		"subtotal": gemini_data.get("subtotal") or 0.0,
		"tax_amount": gemini_data.get("tax_amount") or 0.0,
		"total_amount": gemini_data.get("total_amount", 0.0),
	}

	# Extract and clean line items
	line_items = []
	for item in gemini_data.get("line_items", []):
		description = _clean_ocr_text(item.get("description", ""))
		product_code = _clean_ocr_text(item.get("product_code", ""))

		line_items.append({
			"description": description,
			"product_code": product_code,
			"quantity": item.get("quantity", 1.0),
			"unit_price": item.get("unit_price", 0.0),
			"amount": item.get("amount", 0.0),
		})

	return {
		"header_fields": header_fields,
		"line_items": line_items,
		"source_filename": filename,
	}
