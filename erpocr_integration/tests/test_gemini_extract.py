"""Tests for erpocr_integration.tasks.gemini_extract — schema, validation, transform."""

import json

import pytest

from erpocr_integration.tasks.gemini_extract import (
	_build_extraction_prompt,
	_build_extraction_schema,
	_transform_to_ocr_import_format,
	_validate_gemini_response,
)


# ---------------------------------------------------------------------------
# _build_extraction_schema
# ---------------------------------------------------------------------------

class TestBuildExtractionSchema:
	def test_returns_dict(self):
		schema = _build_extraction_schema()
		assert isinstance(schema, dict)

	def test_has_invoices_array(self):
		schema = _build_extraction_schema()
		assert "invoices" in schema["properties"]
		assert schema["properties"]["invoices"]["type"] == "array"

	def test_invoice_has_required_fields(self):
		schema = _build_extraction_schema()
		invoice_schema = schema["properties"]["invoices"]["items"]
		required = invoice_schema["required"]
		for field in ["supplier_name", "invoice_number", "invoice_date", "total_amount", "line_items"]:
			assert field in required, f"Missing required field: {field}"

	def test_line_items_nested_correctly(self):
		schema = _build_extraction_schema()
		invoice_schema = schema["properties"]["invoices"]["items"]
		line_items = invoice_schema["properties"]["line_items"]
		assert line_items["type"] == "array"
		item_props = line_items["items"]["properties"]
		assert "description" in item_props
		assert "quantity" in item_props
		assert "unit_price" in item_props
		assert "amount" in item_props

	def test_confidence_field_exists(self):
		schema = _build_extraction_schema()
		invoice_schema = schema["properties"]["invoices"]["items"]
		assert "confidence" in invoice_schema["properties"]
		assert invoice_schema["properties"]["confidence"]["type"] == "number"

	def test_currency_field_exists(self):
		schema = _build_extraction_schema()
		invoice_schema = schema["properties"]["invoices"]["items"]
		assert "currency" in invoice_schema["properties"]


# ---------------------------------------------------------------------------
# _build_extraction_prompt
# ---------------------------------------------------------------------------

class TestBuildExtractionPrompt:
	def test_returns_non_empty_string(self):
		prompt = _build_extraction_prompt()
		assert isinstance(prompt, str)
		assert len(prompt) > 100

	def test_mentions_date_format(self):
		prompt = _build_extraction_prompt()
		assert "YYYY-MM-DD" in prompt

	def test_mentions_currency_symbols(self):
		prompt = _build_extraction_prompt()
		assert "currency" in prompt.lower()

	def test_mentions_confidence(self):
		prompt = _build_extraction_prompt()
		assert "confidence" in prompt.lower()

	def test_mentions_multi_invoice(self):
		prompt = _build_extraction_prompt()
		assert "multiple invoices" in prompt.lower()


# ---------------------------------------------------------------------------
# _validate_gemini_response
# ---------------------------------------------------------------------------

class TestValidateGeminiResponse:
	def test_valid_response(self, sample_gemini_api_response):
		is_valid, error = _validate_gemini_response(sample_gemini_api_response)
		assert is_valid is True
		assert error == ""

	def test_empty_response(self):
		is_valid, error = _validate_gemini_response({})
		assert is_valid is False
		assert error  # Has some error message

	def test_none_response(self):
		is_valid, error = _validate_gemini_response(None)
		assert is_valid is False

	def test_empty_candidates(self):
		is_valid, error = _validate_gemini_response({"candidates": []})
		assert is_valid is False

	def test_missing_content(self):
		is_valid, error = _validate_gemini_response({"candidates": [{}]})
		assert is_valid is False
		assert "content" in error.lower()

	def test_empty_parts(self):
		response = {"candidates": [{"content": {"parts": []}}]}
		is_valid, error = _validate_gemini_response(response)
		assert is_valid is False

	def test_empty_text(self):
		response = {"candidates": [{"content": {"parts": [{"text": ""}]}}]}
		is_valid, error = _validate_gemini_response(response)
		assert is_valid is False

	def test_invalid_json_text(self):
		response = {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
		is_valid, error = _validate_gemini_response(response)
		assert is_valid is False
		assert "json" in error.lower()

	def test_valid_json_text(self):
		response = {"candidates": [{"content": {"parts": [{"text": '{"invoices": []}'}]}}]}
		is_valid, error = _validate_gemini_response(response)
		assert is_valid is True


# ---------------------------------------------------------------------------
# _transform_to_ocr_import_format
# ---------------------------------------------------------------------------

class TestTransformToOcrImportFormat:
	def test_basic_transform(self):
		gemini_data = {
			"supplier_name": "Test Supplier",
			"supplier_tax_id": "123",
			"invoice_number": "INV-001",
			"invoice_date": "2024-06-15",
			"due_date": "2024-07-15",
			"subtotal": 1000.0,
			"tax_amount": 150.0,
			"total_amount": 1150.0,
			"currency": "zar",
			"confidence": 0.9,
			"line_items": [{
				"description": "Widget",
				"product_code": "W-01",
				"quantity": 5,
				"unit_price": 200.0,
				"amount": 1000.0,
			}],
		}
		result = _transform_to_ocr_import_format(gemini_data, "test.pdf")

		assert result["source_filename"] == "test.pdf"
		assert result["header_fields"]["supplier_name"] == "Test Supplier"
		assert result["header_fields"]["currency"] == "ZAR"  # Normalized to uppercase
		assert result["header_fields"]["total_amount"] == 1150.0
		assert len(result["line_items"]) == 1
		assert result["line_items"][0]["description"] == "Widget"

	def test_cleans_ocr_artifacts(self):
		gemini_data = {
			"supplier_name": "Star Pops ( Pty ) Ltd",
			"supplier_tax_id": "",
			"invoice_number": "INV-\n002",
			"invoice_date": "2024-06-15",
			"due_date": "",
			"subtotal": 0,
			"tax_amount": 0,
			"total_amount": 500.0,
			"currency": "",
			"confidence": 0.8,
			"line_items": [],
		}
		result = _transform_to_ocr_import_format(gemini_data, "test.pdf")

		assert result["header_fields"]["supplier_name"] == "Star Pops (Pty) Ltd"
		assert result["header_fields"]["invoice_number"] == "INV-002"

	def test_empty_fields_handled(self):
		gemini_data = {
			"supplier_name": "",
			"supplier_tax_id": "",
			"invoice_number": "",
			"invoice_date": "",
			"due_date": "",
			"subtotal": None,
			"tax_amount": None,
			"total_amount": 0.0,
			"currency": None,
			"confidence": 0.0,
			"line_items": [],
		}
		result = _transform_to_ocr_import_format(gemini_data, "empty.pdf")

		assert result["header_fields"]["supplier_name"] == ""
		assert result["header_fields"]["invoice_date"] is None
		assert result["header_fields"]["subtotal"] == 0.0
		assert result["header_fields"]["currency"] == ""
		assert result["line_items"] == []

	def test_line_item_defaults(self):
		gemini_data = {
			"supplier_name": "S",
			"supplier_tax_id": "",
			"invoice_number": "1",
			"invoice_date": "2024-01-01",
			"due_date": "",
			"subtotal": 0,
			"tax_amount": 0,
			"total_amount": 100.0,
			"currency": "",
			"confidence": 0.5,
			"line_items": [{
				"description": "Item",
				"product_code": "",
				"quantity": 1.0,
				"unit_price": 100.0,
				"amount": 100.0,
			}],
		}
		result = _transform_to_ocr_import_format(gemini_data, "test.pdf")
		item = result["line_items"][0]
		assert item["quantity"] == 1.0
		assert item["product_code"] == ""

	def test_missing_optional_fields_use_defaults(self):
		"""Gemini may omit optional fields entirely."""
		gemini_data = {
			"supplier_name": "S",
			"invoice_number": "1",
			"invoice_date": "2024-01-01",
			"total_amount": 100.0,
			"line_items": [{"description": "X"}],
		}
		result = _transform_to_ocr_import_format(gemini_data, "test.pdf")

		assert result["header_fields"]["supplier_tax_id"] == ""
		assert result["header_fields"]["due_date"] is None
		assert result["header_fields"]["subtotal"] == 0.0
		assert result["line_items"][0]["quantity"] == 1.0
		assert result["line_items"][0]["unit_price"] == 0.0
