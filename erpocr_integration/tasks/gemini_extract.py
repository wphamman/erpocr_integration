# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import base64
import json
import time

import frappe
import requests
from frappe import _


def extract_invoice_data(pdf_content: bytes, filename: str, mime_type: str = "application/pdf") -> list[dict]:
	"""
	Extract invoice data from PDF or image using Gemini 2.5 Flash API.

	Supports multi-invoice PDFs — returns one result per invoice found.

	Args:
		pdf_content: Raw file bytes (PDF or image)
		filename: Original filename for logging
		mime_type: MIME type for Gemini API (e.g., "application/pdf", "image/jpeg", "image/png")

	Returns:
		list[dict]: Each dict contains:
			"header_fields": {supplier_name, invoice_number, dates, amounts, ...},
			"line_items": [{description, product_code, qty, rate, amount}, ...],
			"raw_response": str,  # Original Gemini response (shared across all)
			"source_filename": str,
			"extraction_time": float

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
		response_data = _call_gemini_api(pdf_content, prompt, schema, api_key, model, mime_type)
	except Exception as e:
		frappe.log_error(
			title="Gemini API Error",
			message=f"Gemini API call failed for {filename}\n{frappe.get_traceback()}",
		)
		raise Exception(f"Failed to call Gemini API: {e!s}") from e

	# Validate response
	is_valid, error_msg = _validate_gemini_response(response_data)
	if not is_valid:
		# Truncate response to avoid leaking full OCR/PII data into Error Log
		truncated = json.dumps(response_data, indent=2)[:500]
		frappe.log_error(
			title="Invalid Gemini Response",
			message=f"Invalid Gemini response for {filename}\n{error_msg}\n{truncated}...",
		)
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
		# Truncate response to avoid leaking full OCR/PII data into Error Log
		truncated = json.dumps(response_data, indent=2)[:500]
		frappe.log_error(
			title="Gemini Parse Error",
			message=f"Failed to parse Gemini response for {filename}\n{frappe.get_traceback()}\n{truncated}...",
		)
		raise Exception(f"Failed to parse Gemini response: {e!s}") from e

	# Extract invoices array from response
	invoices_raw = extracted_data.get("invoices", [])
	if not invoices_raw:
		raise Exception("No invoices found in Gemini response")

	raw_response = json.dumps(response_data, indent=2)
	extraction_time = time.time() - start_time

	# Transform each invoice to OCR Import format
	results = []
	for invoice_data in invoices_raw:
		result = _transform_to_ocr_import_format(invoice_data, filename)
		result["raw_response"] = raw_response
		result["extraction_time"] = extraction_time
		results.append(result)

	return results


def _build_extraction_prompt() -> str:
	"""Construct prompt for Gemini API invoice extraction."""
	return """Extract ALL invoices from this document. The document may be a PDF (possibly containing multiple invoices) or a photograph/scan of a single invoice.

For EACH invoice found, extract:

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
- Line amount / Total (total for this line = quantity x unit price)

**Important Instructions:**
- If the document contains multiple invoices (e.g., a statement or batch PDF), return each as a separate entry in the invoices array
- If the document contains only one invoice (or is a photograph of a single invoice), return an array with one entry
- Return all dates in YYYY-MM-DD format (convert from any format you see)
- Return all amounts as numeric values WITHOUT currency symbols (e.g., 1234.56 not R1,234.56)
- If a field is not found or not visible, return null for that field
- For line items, skip any header rows - only extract actual data rows
- Product code may appear in a separate column from the description - extract both if available
- If you see multiple tables within a single invoice, extract all line items from all tables
- Be precise with amounts - do not round or approximate

**Confidence Rating:**
For each invoice, rate your overall extraction confidence from 0.0 to 1.0:
- 1.0 = perfectly clear, all fields extracted with high certainty
- 0.7-0.9 = most fields clear, minor uncertainty on some values
- 0.4-0.6 = moderate quality, some fields may be incorrect or missing
- Below 0.4 = poor quality scan, significant uncertainty

Return the extracted data as structured JSON matching the provided schema."""


def _build_extraction_schema() -> dict:
	"""Build JSON schema for Gemini structured output. Supports multi-invoice PDFs and images."""
	invoice_schema = {
		"type": "object",
		"properties": {
			"supplier_name": {"type": "string", "description": "Full name of the supplier/vendor"},
			"supplier_tax_id": {
				"type": "string",
				"description": "Supplier's tax ID/VAT number/registration number (empty string if not present)",
			},
			"invoice_number": {"type": "string", "description": "Invoice number or identifier"},
			"invoice_date": {"type": "string", "description": "Invoice date in YYYY-MM-DD format"},
			"due_date": {
				"type": "string",
				"description": "Payment due date in YYYY-MM-DD format (empty string if not present)",
			},
			"subtotal": {
				"type": "number",
				"description": "Subtotal amount before tax (0 if not shown separately)",
			},
			"tax_amount": {
				"type": "number",
				"description": "Total tax/VAT amount (0 if not shown separately)",
			},
			"total_amount": {"type": "number", "description": "Final total amount of the invoice"},
			"currency": {
				"type": "string",
				"description": "Currency code of the invoice (e.g., USD, ZAR, EUR, GBP). If not explicitly shown, infer from context or symbols.",
			},
			"confidence": {
				"type": "number",
				"description": "Overall extraction confidence from 0.0 (very uncertain) to 1.0 (perfectly clear)",
			},
			"line_items": {
				"type": "array",
				"description": "Array of line items from the invoice",
				"items": {
					"type": "object",
					"properties": {
						"description": {
							"type": "string",
							"description": "Description of the item or service",
						},
						"product_code": {
							"type": "string",
							"description": "Product code, SKU, or item code (empty string if not present)",
						},
						"quantity": {"type": "number", "description": "Quantity ordered/delivered"},
						"unit_price": {"type": "number", "description": "Price per unit"},
						"amount": {
							"type": "number",
							"description": "Total for this line (quantity x unit_price)",
						},
					},
					"required": ["description", "product_code", "quantity", "unit_price", "amount"],
				},
			},
		},
		"required": [
			"supplier_name",
			"supplier_tax_id",
			"invoice_number",
			"invoice_date",
			"due_date",
			"subtotal",
			"tax_amount",
			"total_amount",
			"confidence",
			"line_items",
		],
	}

	return {
		"type": "object",
		"properties": {
			"invoices": {
				"type": "array",
				"description": "Array of invoices extracted from the document. Most documents contain one invoice, but multi-page PDFs or statements may contain multiple.",
				"items": invoice_schema,
			}
		},
		"required": ["invoices"],
	}


def _call_gemini_api(
	pdf_content: bytes,
	prompt: str,
	schema: dict,
	api_key: str,
	model: str,
	mime_type: str = "application/pdf",
) -> dict:
	"""
	Call Gemini API with file content and prompt, return parsed JSON response.
	Includes retry logic for rate limits.
	"""
	url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

	# Encode file as base64
	file_base64 = base64.b64encode(pdf_content).decode("utf-8")

	payload = {
		"contents": [
			{
				"parts": [
					{"text": prompt},
					{"inline_data": {"mime_type": mime_type, "data": file_base64}},
				]
			}
		],
		"generationConfig": {"response_mime_type": "application/json", "response_schema": schema},
	}

	headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

	# Retry with exponential backoff
	max_retries = 3
	for attempt in range(max_retries):
		try:
			response = requests.post(url, json=payload, headers=headers, timeout=60)
			response.raise_for_status()
			return response.json()

		except requests.exceptions.HTTPError as e:
			# Get error response body and sanitize (truncate to prevent sensitive data leakage)
			error_body = ""
			try:
				error_body = e.response.text
			except Exception:
				pass

			# Truncate error body to prevent sensitive invoice data from being logged
			error_body_truncated = error_body[:500] + "..." if len(error_body) > 500 else error_body

			# Rate limit (429) or server error (5xx) - retry
			if e.response and e.response.status_code in (429, 500, 503) and attempt < max_retries - 1:
				wait_time = 2**attempt  # 1s, 2s, 4s
				frappe.log_error(
					title="Gemini API Rate Limit",
					message=f"Rate limit or server error (status {e.response.status_code}), retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})\n\nResponse (truncated): {error_body_truncated}",
				)
				time.sleep(wait_time)
				continue
			else:
				# Non-retryable error or max retries reached
				# Log truncated error (avoid sensitive data in logs/stdout)
				error_summary = f"HTTP {e.response.status_code if e.response else 'Unknown'} Error"

				# Also try to log it (may fail if in nested error context)
				try:
					frappe.log_error(
						title="Gemini API Error",
						message=f"{error_summary}\n\nResponse (first 500 chars):\n{error_body_truncated}\n\nRequest URL: {url}",
					)
				except Exception:
					pass  # Ignore if logging fails

				raise

		except requests.exceptions.Timeout:
			if attempt < max_retries - 1:
				wait_time = 2**attempt
				frappe.log_error(
					title="Gemini API Timeout",
					message=f"Request timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})",
				)
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
		return False, f"Invalid JSON in response text: {e!s}"

	return True, ""


def _transform_to_ocr_import_format(gemini_data: dict, filename: str) -> dict:
	"""
	Transform Gemini response to OCR Import dict format.
	Applies same cleaning and parsing as Nanonets pipeline.
	"""
	from erpocr_integration.tasks.process_import import _clean_ocr_text, _parse_date

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
		"currency": (gemini_data.get("currency") or "").upper().strip(),  # Normalize to uppercase
		"confidence": gemini_data.get("confidence", 0.0),
	}

	# Extract and clean line items
	line_items = []
	for item in gemini_data.get("line_items", []):
		description = _clean_ocr_text(item.get("description", ""))
		product_code = _clean_ocr_text(item.get("product_code", ""))

		line_items.append(
			{
				"description": description,
				"product_code": product_code,
				"quantity": item.get("quantity", 1.0),
				"unit_price": item.get("unit_price", 0.0),
				"amount": item.get("amount", 0.0),
			}
		)

	return {
		"header_fields": header_fields,
		"line_items": line_items,
		"source_filename": filename,
	}
