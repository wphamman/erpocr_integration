"""Gemini-based document classification.

Classifies uploaded documents as 'invoice' or 'statement' before routing
to the appropriate processing pipeline. Defaults to 'invoice' on any
error to avoid breaking the existing pipeline.
"""

import base64
import json

import frappe
import requests

_CLASSIFICATION_PROMPT = """Classify this document as one of:
- "invoice": A single invoice or a PDF containing multiple invoices. Has line items with quantities, unit prices, and amounts. May have a single supplier and invoice number.
- "statement": A supplier/vendor account statement. Lists multiple invoice references, payments, credits, and a running balance over a period. Typically has opening balance, closing balance, and an aging summary.

Look at the overall structure, not just the title. A document with columns like "Date | Reference | Debit | Credit | Balance" is a statement. A document with "Qty | Description | Unit Price | Amount" is an invoice.

Return ONLY the document type."""

_CLASSIFICATION_SCHEMA = {
	"type": "object",
	"properties": {
		"document_type": {
			"type": "string",
			"description": "Either 'invoice' or 'statement'",
		},
		"confidence": {
			"type": "number",
			"description": "Confidence score 0.0 to 1.0",
		},
	},
	"required": ["document_type", "confidence"],
}


def classify_document(
	file_content: bytes,
	filename: str,
	mime_type: str = "application/pdf",
) -> tuple[str, float]:
	"""Classify a document as 'invoice' or 'statement' using Gemini.

	Returns (doc_type, confidence). Defaults to ('invoice', 0.0) on any error.
	"""
	try:
		settings = frappe.get_single("OCR Settings")
		api_key = settings.get_password("gemini_api_key")
		if not api_key:
			return "invoice", 0.0

		model = settings.gemini_model or "gemini-2.5-flash"
		result = _call_classification_api(file_content, api_key, model, mime_type)

		doc_type = result.get("document_type", "").lower().strip()
		confidence = float(result.get("confidence", 0.0))
		if doc_type == "statement":
			return "statement", confidence
		return "invoice", confidence

	except Exception:
		frappe.log_error(
			title="Document Classification Error",
			message=f"Classification failed for {filename}, defaulting to invoice\n{frappe.get_traceback()}",
		)
		return "invoice", 0.0


def _call_classification_api(
	file_content: bytes,
	api_key: str,
	model: str,
	mime_type: str,
) -> dict:
	"""Call Gemini API for document classification. Returns parsed response."""
	url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

	file_base64 = base64.b64encode(file_content).decode("utf-8")

	payload = {
		"contents": [
			{
				"parts": [
					{"text": _CLASSIFICATION_PROMPT},
					{"inline_data": {"mime_type": mime_type, "data": file_base64}},
				]
			}
		],
		"generationConfig": {
			"response_mime_type": "application/json",
			"response_schema": _CLASSIFICATION_SCHEMA,
		},
	}

	headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

	response = requests.post(url, json=payload, headers=headers, timeout=30)
	response.raise_for_status()

	data = response.json()
	candidates = data.get("candidates", [])
	text = candidates[0]["content"]["parts"][0]["text"]
	return json.loads(text)
