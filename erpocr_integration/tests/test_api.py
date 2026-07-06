"""Tests for erpocr_integration.api — pipeline logic."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from erpocr_integration.api import _populate_ocr_import, _select_tax_template, check_duplicates

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

		assert doc.supplier_name_ocr == "Acme Trading (Pty) Ltd"
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
			"folder_path": "2024/June/Acme Trading",
		}

		_populate_ocr_import(doc, sample_extracted_data, sample_settings, drive_result)

		assert doc.drive_file_id == "drive-123"
		assert doc.drive_link == "https://drive.google.com/file/d/drive-123"
		assert doc.drive_folder_path == "2024/June/Acme Trading"

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

	def test_product_code_stored_in_own_field(self, sample_settings):
		"""v1.1+: product_code goes to its own field; item_name = description always.

		Pre-v1.1, product_code was packed into item_name as a matching shortcut.
		That coupling was dropped in favour of the dedicated field + the new
		Item Supplier matching tier. See CHANGELOG 1.1.0."""
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

		# product_code lives in its own field
		assert doc.items[0].product_code == "WA-01"
		assert doc.items[1].product_code == ""
		# item_name is the description regardless of product_code presence
		assert doc.items[0].item_name == "Widget A"
		assert doc.items[1].item_name == "Service Fee"


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
		assert result[0]["doctype"] == "OCR Import"

	def test_finds_duplicate_by_existing_pi(self, mock_frappe):
		"""When supplier is matched and a PI already exists with same bill_no, flag it."""
		doc = self._make_doc(supplier="Acme Corp ERPNext")
		mock_frappe.get_cached_doc.return_value = doc

		pi_record = {
			"name": "PINV-00042",
			"docstatus": 1,
			"creation": "2026-02-15",
			"bill_no": "INV-001",
			"supplier": "Acme Corp ERPNext",
		}
		# Two OCR Import checks return nothing, PI check returns a hit
		mock_frappe.get_list.side_effect = [[], [], [pi_record]]

		result = check_duplicates("OCR-IMP-00001")

		assert len(result) == 1
		assert result[0]["name"] == "PINV-00042"
		assert result[0]["doctype"] == "Purchase Invoice"
		assert result[0]["match_reason"] == "Existing Purchase Invoice"
		assert result[0]["status"] == "Submitted"
		assert result[0]["invoice_number"] == "INV-001"

	def test_skips_pi_check_when_supplier_unmatched(self, mock_frappe):
		"""PI check requires a matched supplier to avoid cross-supplier false matches."""
		doc = self._make_doc()  # no `supplier` attribute
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		check_duplicates("OCR-IMP-00001")

		# Only the two OCR Import checks should have run
		assert mock_frappe.get_list.call_count == 2
		called_doctypes = [call.args[0] for call in mock_frappe.get_list.call_args_list]
		assert "Purchase Invoice" not in called_doctypes

	def test_pi_check_scoped_to_company(self, mock_frappe):
		"""PI query must filter by company to avoid cross-company false positives."""
		doc = self._make_doc(supplier="Acme Corp ERPNext", company="Co A")
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		check_duplicates("OCR-IMP-00001")

		# Find the Purchase Invoice call and inspect its filters
		pi_calls = [c for c in mock_frappe.get_list.call_args_list if c.args[0] == "Purchase Invoice"]
		assert len(pi_calls) == 1
		filters = pi_calls[0].kwargs["filters"]
		assert filters.get("company") == "Co A"
		assert filters.get("bill_no") == "INV-001"
		assert filters.get("supplier") == "Acme Corp ERPNext"

	def test_pi_check_excludes_own_linked_pi(self, mock_frappe):
		"""The PI that was created from this OCR Import must not be flagged."""
		doc = self._make_doc(supplier="Acme Corp ERPNext", purchase_invoice="PINV-00042")
		mock_frappe.get_cached_doc.return_value = doc
		mock_frappe.get_list.return_value = []

		check_duplicates("OCR-IMP-00001")

		pi_calls = [c for c in mock_frappe.get_list.call_args_list if c.args[0] == "Purchase Invoice"]
		assert len(pi_calls) == 1
		filters = pi_calls[0].kwargs["filters"]
		assert filters.get("name") == ["!=", "PINV-00042"]

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


# ---------------------------------------------------------------------------
# _select_tax_template — customs/import Actual-VAT detection (live-review V1)
# ---------------------------------------------------------------------------


class TestSelectTaxTemplate:
	def _settings(self, **overrides):
		base = dict(
			default_tax_template="1 - Standard VAT",
			non_vat_tax_template="Non-VAT",
			import_tax_template="9 - Import with Std VAT",
		)
		base.update(overrides)
		return SimpleNamespace(**base)

	def _mock_default_template(self, mock_frappe, rates=(15.0,)):
		rows = [SimpleNamespace(rate=r, add_deduct_tax="Add") for r in rates]
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(taxes=rows)

	def test_no_tax_selects_non_vat(self, mock_frappe):
		assert _select_tax_template(self._settings(), 1000.0, 0.0) == "Non-VAT"

	def test_percentage_looking_tax_selects_default(self, mock_frappe):
		"""Tax ≈ 15% of subtotal → normal percentage template."""
		self._mock_default_template(mock_frappe)
		assert _select_tax_template(self._settings(), 1000.0, 150.0) == "1 - Standard VAT"

	def test_ocr_noise_stays_on_default(self, mock_frappe):
		"""Small deviation (mis-OCR'd digit) must not flip to the import template."""
		self._mock_default_template(mock_frappe)
		assert _select_tax_template(self._settings(), 1000.0, 160.0) == "1 - Standard VAT"

	def test_cargo_compass_ji279503_selects_import_template(self, mock_frappe):
		"""The prod-confirmed case: subtotal R57,614.30, customs VAT R64,038.90
		(111% of subtotal vs expected 15%) → Actual import template."""
		self._mock_default_template(mock_frappe)
		assert _select_tax_template(self._settings(), 57614.30, 64038.90) == "9 - Import with Std VAT"

	def test_small_subtotal_large_vat_selects_import_template(self, mock_frappe):
		"""OCR-IMP-01597 shape: R1,440 services carrying R10,912.95 customs VAT."""
		self._mock_default_template(mock_frappe)
		assert _select_tax_template(self._settings(), 1440.0, 10912.95) == "9 - Import with Std VAT"

	def test_no_import_template_configured_falls_back(self, mock_frappe):
		"""Backward compatible: sites without the setting keep old behavior."""
		self._mock_default_template(mock_frappe)
		settings = self._settings(import_tax_template=None)
		assert _select_tax_template(settings, 57614.30, 64038.90) == "1 - Standard VAT"

	def test_zero_subtotal_falls_back_to_default(self, mock_frappe):
		self._mock_default_template(mock_frappe)
		assert _select_tax_template(self._settings(), 0.0, 5000.0) == "1 - Standard VAT"

	def test_template_lookup_failure_falls_back(self, mock_frappe):
		"""Any lookup/shape problem → default template (pre-existing behavior)."""
		mock_frappe.get_cached_doc.side_effect = Exception("boom")
		assert _select_tax_template(self._settings(), 1000.0, 9999.0) == "1 - Standard VAT"

	def test_zero_rate_default_template_falls_back(self, mock_frappe):
		"""A default template with no percentage rows can't anchor the ratio test."""
		self._mock_default_template(mock_frappe, rates=(0.0,))
		assert _select_tax_template(self._settings(), 1000.0, 9999.0) == "1 - Standard VAT"

	def test_deduct_rows_subtract_from_anchor(self, mock_frappe):
		"""A default template with VAT 15% Add + withholding 15% Deduct nets
		0% — the anchor must not become 30% and misroute ordinary invoices."""
		rows = [
			SimpleNamespace(rate=15.0, add_deduct_tax="Add"),
			SimpleNamespace(rate=15.0, add_deduct_tax="Deduct"),
		]
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(taxes=rows)
		# Net anchor 0 → can't classify → default template (never import)
		assert _select_tax_template(self._settings(), 1000.0, 150.0) == "1 - Standard VAT"
