"""End-to-end workflow tests for OCR Delivery Note pipeline."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.dn_api import (
	_populate_ocr_dn,
	_run_dn_matching,
	update_ocr_dn_on_cancel,
	update_ocr_dn_on_submit,
)
from erpocr_integration.erpnext_ocr.doctype.ocr_delivery_note.ocr_delivery_note import (
	OCRDeliveryNote,
	_resolve_rate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockOCRDN:
	"""Lightweight mock for workflow tests."""

	def __init__(self, **kw):
		self.name = kw.get("name", "OCR-DN-00001")
		self.status = kw.get("status", "Pending")
		self.supplier = kw.get("supplier", None)
		self.supplier_name_ocr = kw.get("supplier_name_ocr", "")
		self.supplier_match_status = kw.get("supplier_match_status", "Unmatched")
		self.company = kw.get("company", "Test Company")
		self.delivery_note_number = kw.get("delivery_note_number", "")
		self.delivery_date = kw.get("delivery_date", None)
		self.vehicle_number = kw.get("vehicle_number", "")
		self.driver_name = kw.get("driver_name", "")
		self.confidence = kw.get("confidence", 0)
		self.raw_payload = ""
		self.items = kw.get("items", [])
		self.document_type = kw.get("document_type", "")
		self.purchase_order = kw.get("purchase_order", None)
		self.purchase_order_result = kw.get("purchase_order_result", None)
		self.purchase_receipt = kw.get("purchase_receipt", None)
		self.drive_file_id = kw.get("drive_file_id", None)

	def append(self, table, item_dict):
		self.items.append(SimpleNamespace(**item_dict))

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def save(self, **kw):
		pass


def _make_settings(**overrides):
	defaults = dict(
		default_company="Test Company",
		default_warehouse="Stores - TC",
		matching_threshold=80,
		dn_default_warehouse="Goods Receipt - TC",
		dn_archive_folder_id="dn-archive-123",
	)
	defaults.update(overrides)

	class S(SimpleNamespace):
		def get(self, key, default=None):
			return getattr(self, key, default)

	return S(**defaults)


# ---------------------------------------------------------------------------
# TestDnExtractionToMatching
# ---------------------------------------------------------------------------


class TestDnExtractionToMatching:
	"""Test the populate → match pipeline for DN data."""

	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_full_pipeline(
		self,
		mock_item_fuzzy,
		mock_item,
		mock_sup_fuzzy,
		mock_sup,
		sample_dn_extracted_data,
	):
		"""Populate then match produces correct supplier/item state."""
		mock_sup.return_value = ("Acme Materials Ltd", "Auto Matched")
		mock_item.side_effect = [
			("SR-12-6", "Auto Matched"),  # for product_code match
			("CM-50", "Auto Matched"),  # for description match
		]
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		settings = _make_settings()
		doc = MockOCRDN()
		_populate_ocr_dn(doc, sample_dn_extracted_data, settings)
		_run_dn_matching(doc, settings)

		assert doc.supplier == "Acme Materials Ltd"
		assert doc.supplier_match_status == "Auto Matched"
		assert len(doc.items) == 2
		assert doc.items[0].item_code == "SR-12-6"

	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_unmatched_supplier_and_items(
		self,
		mock_item_fuzzy,
		mock_item,
		mock_sup_fuzzy,
		mock_sup,
		sample_dn_extracted_data,
	):
		"""When nothing matches, supplier and items stay unmatched."""
		mock_sup.return_value = (None, "Unmatched")
		mock_sup_fuzzy.return_value = (None, "Unmatched", 0)
		mock_item.return_value = (None, "Unmatched")
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		settings = _make_settings()
		doc = MockOCRDN()
		_populate_ocr_dn(doc, sample_dn_extracted_data, settings)
		_run_dn_matching(doc, settings)

		assert doc.supplier is None
		assert doc.supplier_match_status == "Unmatched"
		assert all(item.match_status == "Unmatched" for item in doc.items)


# ---------------------------------------------------------------------------
# TestPOCreationWorkflow
# ---------------------------------------------------------------------------


class TestPOCreationWorkflow:
	"""Test Create PO → submit → Completed workflow."""

	def test_po_submit_marks_completed(self, mock_frappe):
		"""Submitting PO marks DN as Completed."""
		mock_frappe.get_all.return_value = ["OCR-DN-00001"]

		po_doc = SimpleNamespace(doctype="Purchase Order", name="PO-00001")
		update_ocr_dn_on_submit(po_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_with(
			"OCR Delivery Note", "OCR-DN-00001", "status", "Completed"
		)

	def test_po_cancel_resets_to_pending(self, mock_frappe):
		"""Cancelling PO clears link and resets status."""
		mock_frappe.get_all.return_value = ["OCR-DN-00001"]
		mock_dn = MockOCRDN(
			name="OCR-DN-00001",
			status="Completed",
			purchase_order_result="PO-00001",
		)
		mock_frappe.get_doc.return_value = mock_dn

		po_doc = SimpleNamespace(doctype="Purchase Order", name="PO-00001")
		update_ocr_dn_on_cancel(po_doc, "on_cancel")

		assert mock_dn.purchase_order_result == ""
		assert mock_dn.document_type == ""


# ---------------------------------------------------------------------------
# TestPRCreationWorkflow
# ---------------------------------------------------------------------------


class TestPRCreationWorkflow:
	"""Test Create PR → submit → Completed workflow."""

	def test_pr_submit_marks_completed(self, mock_frappe):
		"""Submitting PR marks DN as Completed."""
		mock_frappe.get_all.return_value = ["OCR-DN-00002"]

		pr_doc = SimpleNamespace(doctype="Purchase Receipt", name="PR-00001")
		update_ocr_dn_on_submit(pr_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_with(
			"OCR Delivery Note", "OCR-DN-00002", "status", "Completed"
		)

	def test_pr_cancel_clears_and_resets(self, mock_frappe):
		"""Cancelling PR clears link and resets."""
		mock_frappe.get_all.return_value = ["OCR-DN-00002"]
		mock_dn = MockOCRDN(
			name="OCR-DN-00002",
			status="Completed",
			purchase_receipt="PR-00001",
		)
		mock_frappe.get_doc.return_value = mock_dn

		pr_doc = SimpleNamespace(doctype="Purchase Receipt", name="PR-00001")
		update_ocr_dn_on_cancel(pr_doc, "on_cancel")

		assert mock_dn.purchase_receipt == ""


# ---------------------------------------------------------------------------
# TestRateResolutionWorkflow
# ---------------------------------------------------------------------------


class TestRateResolutionWorkflow:
	"""Test rate resolution for PR creation from DN."""

	def test_rate_from_po(self, mock_frappe):
		"""When PO item is linked, PR gets PO item rate."""
		mock_frappe.db.get_value.return_value = 125.50
		rate = _resolve_rate("SR-12-6", "poi-001")
		assert rate == 125.50

	def test_rate_from_item_master_no_po(self, mock_frappe):
		"""Without PO, PR gets rate from item master."""
		mock_frappe.db.get_value.return_value = SimpleNamespace(last_purchase_rate=100.0, standard_rate=80.0)
		rate = _resolve_rate("SR-12-6")
		assert rate == 100.0

	def test_rate_zero_when_no_source(self, mock_frappe):
		"""Returns 0 when no rate source available."""
		mock_frappe.db.get_value.return_value = None
		rate = _resolve_rate(None)
		assert rate == 0


# ---------------------------------------------------------------------------
# TestNoActionWorkflow
# ---------------------------------------------------------------------------


class TestNoActionWorkflow:
	"""Test the No Action workflow for non-delivery-note scans."""

	def test_mark_no_action_flow(self, mock_frappe):
		"""Full No Action flow: Needs Review → No Action."""
		doc = OCRDeliveryNote.__new__(OCRDeliveryNote)
		doc.name = "OCR-DN-00001"
		doc.status = "Needs Review"
		doc.no_action_reason = None
		doc.save = MagicMock()
		mock_frappe.has_permission.return_value = True

		doc.mark_no_action("Random photo, not a delivery note")

		assert doc.status == "No Action"
		assert doc.no_action_reason == "Random photo, not a delivery note"

	def test_no_action_preserved_by_update_status(self, mock_frappe):
		"""No Action status is not overwritten by _update_status."""
		doc = OCRDeliveryNote.__new__(OCRDeliveryNote)
		doc.status = "No Action"
		doc.purchase_order_result = None
		doc.purchase_receipt = None
		doc.supplier = "Test"
		doc.items = []

		doc._update_status()
		assert doc.status == "No Action"


# ---------------------------------------------------------------------------
# TestDriveScanDedup
# ---------------------------------------------------------------------------


class TestDriveScanDedup:
	"""Test drive scan deduplication for DN files."""

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	@patch("erpocr_integration.api.validate_file_magic_bytes")
	def test_skips_already_processed(self, mock_validate, mock_download, mock_frappe):
		"""Files already processed (non-Error) are skipped."""
		from erpocr_integration.tasks.drive_integration import _process_dn_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-DN-00001", status="Matched", drive_retry_count=0)
		]

		settings = _make_settings()
		result = _process_dn_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "scan.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is False
		mock_download.assert_not_called()

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	@patch("erpocr_integration.api.validate_file_magic_bytes")
	def test_retries_error_records(self, mock_validate, mock_download, mock_frappe):
		"""Error records under retry cap are retried (old records deleted)."""
		from erpocr_integration.tasks.drive_integration import _process_dn_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-DN-00001", status="Error", drive_retry_count=1)
		]
		mock_download.return_value = b"%PDF-1.4 test content"
		mock_validate.return_value = True

		mock_dn = MagicMock()
		mock_dn.name = "OCR-DN-00002"
		mock_frappe.get_doc.return_value = mock_dn

		settings = _make_settings()
		result = _process_dn_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "scan.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is True
		mock_frappe.delete_doc.assert_called_once()

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	def test_gives_up_after_max_retries(self, mock_download, mock_frappe):
		"""Files exceeding MAX_DRIVE_RETRIES are not retried."""
		from erpocr_integration.tasks.drive_integration import _process_dn_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-DN-00001", status="Error", drive_retry_count=3)
		]

		settings = _make_settings()
		result = _process_dn_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "scan.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is False
		mock_download.assert_not_called()


# ---------------------------------------------------------------------------
# TestGeminiDnExtraction
# ---------------------------------------------------------------------------


class TestGeminiDnExtraction:
	"""Test Gemini extraction functions for delivery notes."""

	def test_dn_schema_has_no_financial_fields(self):
		"""DN schema should not include rate, amount, tax, currency fields."""
		from erpocr_integration.tasks.gemini_extract import _build_dn_extraction_schema

		schema = _build_dn_extraction_schema()
		props = schema["properties"]

		# Should not have financial header fields
		for field in ["subtotal", "tax_amount", "total_amount", "currency"]:
			assert field not in props, f"Financial field '{field}' should not be in DN schema"

		# Line item schema should not have financial fields
		item_props = props["line_items"]["items"]["properties"]
		for field in ["unit_price", "amount"]:
			assert field not in item_props, f"Financial field '{field}' should not be in DN item schema"

	def test_dn_schema_has_delivery_fields(self):
		"""DN schema should include DN-specific fields."""
		from erpocr_integration.tasks.gemini_extract import _build_dn_extraction_schema

		schema = _build_dn_extraction_schema()
		props = schema["properties"]

		for field in ["supplier_name", "delivery_note_number", "delivery_date", "vehicle_number"]:
			assert field in props, f"DN field '{field}' should be in schema"

	def test_transform_dn_format(self):
		"""_transform_to_dn_format produces correct structure."""
		from erpocr_integration.tasks.gemini_extract import _transform_to_dn_format

		raw = {
			"supplier_name": "Acme (Pty) Ltd",
			"delivery_note_number": "DN-001",
			"delivery_date": "2025-02-20",
			"vehicle_number": "CA 123",
			"driver_name": "John",
			"confidence": 0.9,
			"line_items": [{"description": "Rod", "product_code": "R-01", "quantity": 10, "unit": "pcs"}],
		}
		result = _transform_to_dn_format(raw, "scan.pdf")

		assert result["header_fields"]["supplier_name"] == "Acme (Pty) Ltd"
		assert result["header_fields"]["delivery_date"] == "2025-02-20"
		assert len(result["line_items"]) == 1
		assert result["source_filename"] == "scan.pdf"

	def test_transform_cleans_supplier_name(self):
		"""Supplier name cleanup: ( Pty ) → (Pty)."""
		from erpocr_integration.tasks.gemini_extract import _transform_to_dn_format

		raw = {
			"supplier_name": "Acme ( Pty ) Ltd",
			"line_items": [],
		}
		result = _transform_to_dn_format(raw, "scan.pdf")
		assert result["header_fields"]["supplier_name"] == "Acme (Pty) Ltd"

	def test_transform_cleans_product_code_newlines(self):
		"""Product codes with newlines are cleaned."""
		from erpocr_integration.tasks.gemini_extract import _transform_to_dn_format

		raw = {
			"supplier_name": "Test",
			"line_items": [{"description": "Rod", "product_code": "R-\n01", "quantity": 5, "unit": ""}],
		}
		result = _transform_to_dn_format(raw, "scan.pdf")
		assert result["line_items"][0]["product_code"] == "R-01"
