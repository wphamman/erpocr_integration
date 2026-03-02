"""Tests for erpocr_integration.api — pipeline logic."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from erpocr_integration.api import _populate_ocr_import, check_duplicates

# ---------------------------------------------------------------------------
# _populate_ocr_import
# ---------------------------------------------------------------------------


class TestPopulateOcrImport:
	def _make_ocr_import_mock(self):
		"""Create a mock OCR Import doc that supports attribute assignment and append."""
		doc = MagicMock()
		doc.items = []

		def mock_append(table_name, row_dict):
			item = SimpleNamespace(**row_dict)
			doc.items.append(item)

		doc.append = mock_append
		return doc

	def test_header_fields_mapped(self, sample_extracted_data, sample_settings):
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		assert doc.supplier_name_ocr == "Star Pops (Pty) Ltd"
		assert doc.invoice_number == "INV-2024-0042"
		assert doc.invoice_date == "2024-06-15"
		assert doc.due_date == "2024-07-15"
		assert doc.subtotal == 1000.00
		assert doc.tax_amount == 150.00
		assert doc.total_amount == 1150.00
		assert doc.currency == "ZAR"

	def test_line_items_created(self, sample_extracted_data, sample_settings):
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		assert len(doc.items) == 2
		assert doc.items[0].description_ocr == "Premium Lollipops Assorted 50pk"
		assert doc.items[0].qty == 10
		assert doc.items[0].rate == 85.00
		assert doc.items[0].match_status == "Unmatched"

	def test_confidence_scaled(self, sample_extracted_data, sample_settings):
		"""Gemini returns 0.0-1.0, OCR Import stores 0-100."""
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		# 0.95 * 100 = 95.0
		assert doc.confidence == 95.0

	def test_confidence_clamped(self, sample_settings):
		"""Confidence should be clamped to 0-100 range."""
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}
		data = {
			"header_fields": {"confidence": 1.5, "total_amount": 0},
			"line_items": [],
		}

		_populate_ocr_import(doc, data, sample_settings, drive_result)

		assert doc.confidence == 100.0  # Clamped to max

	def test_tax_template_with_tax(self, sample_extracted_data, sample_settings):
		"""When tax_amount > 0, use VAT template."""
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		assert doc.tax_template == "SA VAT 15%"

	def test_tax_template_without_tax(self, sample_settings):
		"""When tax_amount is 0, use non-VAT template."""
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}
		data = {
			"header_fields": {"tax_amount": 0, "total_amount": 500.00, "confidence": 0.9},
			"line_items": [],
		}

		_populate_ocr_import(doc, data, sample_settings, drive_result)

		assert doc.tax_template == "Non-VAT"

	def test_drive_info_populated(self, sample_extracted_data, sample_settings):
		doc = self._make_ocr_import_mock()
		drive_result = {
			"file_id": "drive-123",
			"shareable_link": "https://drive.google.com/file/d/drive-123",
			"folder_path": "2024/June/Star Pops",
		}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		assert doc.drive_file_id == "drive-123"
		assert doc.drive_link == "https://drive.google.com/file/d/drive-123"
		assert doc.drive_folder_path == "2024/June/Star Pops"

	def test_no_drive_info(self, sample_extracted_data, sample_settings):
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		# Drive fields should not be set when file_id is None
		assert not hasattr(doc, "drive_file_id") or doc.drive_file_id != "drive-123"

	def test_empty_line_items(self, sample_settings):
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}
		data = {
			"header_fields": {"total_amount": 100.00, "confidence": 0.8},
			"line_items": [],
		}

		_populate_ocr_import(doc, data, sample_settings, drive_result)

		assert len(doc.items) == 0

	def test_item_name_uses_product_code_when_available(self, sample_settings):
		"""item_name should be product_code if present, else description."""
		doc = self._make_ocr_import_mock()
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}
		data = {
			"header_fields": {"total_amount": 100.00, "confidence": 0.8},
			"line_items": [
				{
					"description": "Widget A",
					"product_code": "WA-01",
					"quantity": 1,
					"unit_price": 50,
					"amount": 50,
				},
				{
					"description": "Service Fee",
					"product_code": "",
					"quantity": 1,
					"unit_price": 50,
					"amount": 50,
				},
			],
		}

		_populate_ocr_import(doc, data, sample_settings, drive_result)

		assert doc.items[0].item_name == "WA-01"  # product_code takes priority
		assert doc.items[1].item_name == "Service Fee"  # Falls back to description


# ---------------------------------------------------------------------------
# check_duplicates
# ---------------------------------------------------------------------------


class TestCheckDuplicates:
	def _make_doc(self, **kwargs):
		"""Return a SimpleNamespace mimicking a cached OCR Import doc."""
		defaults = {
			"invoice_number": "INV-001",
			"supplier_name_ocr": "Acme Corp",
			"source_filename": "invoice.pdf",
		}
		defaults.update(kwargs)
		return SimpleNamespace(**defaults)

	def test_finds_duplicate_by_invoice_number(self, mock_frappe):
		doc = self._make_doc()
		mock_frappe.get_cached_doc.return_value = doc

		dup_record = {
			"name": "OCR-IMP-00002",
			"status": "Needs Review",
			"creation": "2026-03-01",
			"source_type": "Gemini Email",
			"invoice_number": "INV-001",
		}
		# First call = invoice_number match, second call = filename match
		mock_frappe.get_list.side_effect = [[dup_record], []]

		result = check_duplicates("OCR-IMP-00001")

		assert len(result) == 1
		assert result[0]["name"] == "OCR-IMP-00002"
		assert result[0]["match_reason"] == "Same invoice number"

	def test_finds_duplicate_by_filename(self, mock_frappe):
		doc = self._make_doc(invoice_number="")  # No invoice number
		mock_frappe.get_cached_doc.return_value = doc

		dup_record = {
			"name": "OCR-IMP-00003",
			"status": "Matched",
			"creation": "2026-03-01",
			"source_type": "Gemini Manual Upload",
			"invoice_number": "INV-002",
		}
		# First call skipped (no invoice_number), second call = filename match
		mock_frappe.get_list.return_value = [dup_record]

		result = check_duplicates("OCR-IMP-00001")

		assert len(result) == 1
		assert result[0]["match_reason"] == "Same filename"

	def test_deduplicates_across_both_checks(self, mock_frappe):
		"""If the same record matches both invoice_number and filename, it appears once."""
		doc = self._make_doc()
		mock_frappe.get_cached_doc.return_value = doc

		dup = {
			"name": "OCR-IMP-00002",
			"status": "Needs Review",
			"creation": "2026-03-01",
			"source_type": "Gemini Email",
			"invoice_number": "INV-001",
		}
		# Same record returned by both queries
		mock_frappe.get_list.side_effect = [[dup], [dup]]

		result = check_duplicates("OCR-IMP-00001")

		assert len(result) == 1
		# First match wins — should be "Same invoice number"
		assert result[0]["match_reason"] == "Same invoice number"

	def test_no_duplicates_found(self, mock_frappe):
		doc = self._make_doc()
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		result = check_duplicates("OCR-IMP-00001")

		assert result == []

	def test_skips_invoice_check_when_empty(self, mock_frappe):
		"""Should not query by invoice_number when it's empty."""
		doc = self._make_doc(invoice_number="", supplier_name_ocr="")
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		check_duplicates("OCR-IMP-00001")

		# Only the filename query should have been made (not the invoice one)
		assert mock_frappe.get_list.call_count == 1

	def test_skips_filename_check_when_empty(self, mock_frappe):
		doc = self._make_doc(source_filename="")
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		check_duplicates("OCR-IMP-00001")

		# Only the invoice_number query should have been made
		assert mock_frappe.get_list.call_count == 1

	def test_permission_denied(self, mock_frappe):
		mock_frappe.has_permission.return_value = False

		with pytest.raises(Exception):
			check_duplicates("OCR-IMP-00001")
