"""Tests for dn_api.py — DN processing, matching, doc events, retry, PO matching."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.dn_api import (
	_populate_ocr_dn,
	_run_dn_matching,
	dn_gemini_process,
	get_open_purchase_orders_for_dn,
	match_dn_po_items,
	retry_dn_extraction,
	update_ocr_dn_on_cancel,
	update_ocr_dn_on_submit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockOCRDN:
	"""Lightweight mock for OCR Delivery Note document."""

	def __init__(self, **kwargs):
		self.name = kwargs.get("name", "OCR-DN-00001")
		self.status = kwargs.get("status", "Pending")
		self.supplier = kwargs.get("supplier", None)
		self.supplier_name_ocr = kwargs.get("supplier_name_ocr", "")
		self.supplier_match_status = kwargs.get("supplier_match_status", "Unmatched")
		self.company = kwargs.get("company", "Test Company")
		self.delivery_note_number = kwargs.get("delivery_note_number", "")
		self.delivery_date = kwargs.get("delivery_date", None)
		self.vehicle_number = kwargs.get("vehicle_number", "")
		self.driver_name = kwargs.get("driver_name", "")
		self.confidence = kwargs.get("confidence", 0)
		self.raw_payload = kwargs.get("raw_payload", "")
		self.items = kwargs.get("items", [])
		self.drive_file_id = kwargs.get("drive_file_id", None)
		self.purchase_order = kwargs.get("purchase_order", None)
		self.purchase_order_result = kwargs.get("purchase_order_result", None)
		self.purchase_receipt = kwargs.get("purchase_receipt", None)
		self.document_type = kwargs.get("document_type", "")
		self.source_type = kwargs.get("source_type", "Gemini Drive Scan")

	def append(self, table, item_dict):
		self.items.append(SimpleNamespace(**item_dict))

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def save(self, **kw):
		pass

	def db_set(self, key, value):
		setattr(self, key, value)

	def reload(self):
		pass


class MockPOItem:
	"""Mock PO item for matching tests."""

	def __init__(self, **kw):
		self.name = kw.get("name", "poi-001")
		self.item_code = kw.get("item_code", "SR-12-6")
		self.item_name = kw.get("item_name", "Steel Rod")
		self.qty = kw.get("qty", 100)
		self.received_qty = kw.get("received_qty", 0)
		self.rate = kw.get("rate", 50.0)


# ---------------------------------------------------------------------------
# TestPopulateOcrDn
# ---------------------------------------------------------------------------


class TestPopulateOcrDn:
	def test_populates_header_fields(self, sample_dn_extracted_data, sample_settings):
		"""Header fields are populated correctly."""
		doc = MockOCRDN()
		_populate_ocr_dn(doc, sample_dn_extracted_data, sample_settings)

		assert doc.supplier_name_ocr == "Acme Materials (Pty) Ltd"
		assert doc.delivery_note_number == "DN-2025-0042"
		assert doc.delivery_date == "2025-02-20"
		assert doc.vehicle_number == "CA 123-456"
		assert doc.driver_name == "John"
		assert doc.confidence == 92.0  # 0.92 * 100

	def test_populates_line_items(self, sample_dn_extracted_data, sample_settings):
		"""Line items are populated with correct fields."""
		doc = MockOCRDN()
		_populate_ocr_dn(doc, sample_dn_extracted_data, sample_settings)

		assert len(doc.items) == 2
		assert doc.items[0].description_ocr == "Steel Rod 12mm x 6m"
		assert doc.items[0].item_name == "SR-12-6"  # product_code used as item_name
		assert doc.items[0].qty == 50
		assert doc.items[0].uom == "pcs"
		assert doc.items[0].match_status == "Unmatched"

	def test_item_name_fallback_to_description(self, sample_settings):
		"""When no product_code, item_name falls back to description."""
		data = {
			"header_fields": {"supplier_name": "Test", "confidence": 0.8},
			"line_items": [{"description": "Some Material", "product_code": "", "quantity": 5, "unit": "kg"}],
			"raw_response": "{}",
		}
		doc = MockOCRDN()
		_populate_ocr_dn(doc, data, sample_settings)

		assert doc.items[0].item_name == "Some Material"

	def test_confidence_clamped(self, sample_settings):
		"""Confidence is clamped to 0-100 range."""
		data = {
			"header_fields": {"supplier_name": "Test", "confidence": 1.5},
			"line_items": [],
			"raw_response": "{}",
		}
		doc = MockOCRDN()
		_populate_ocr_dn(doc, data, sample_settings)
		assert doc.confidence == 100.0

	def test_empty_line_items(self, sample_settings):
		"""No items when line_items is empty."""
		data = {
			"header_fields": {"supplier_name": "Test"},
			"line_items": [],
			"raw_response": "{}",
		}
		doc = MockOCRDN()
		_populate_ocr_dn(doc, data, sample_settings)
		assert len(doc.items) == 0


# ---------------------------------------------------------------------------
# TestRunDnMatching
# ---------------------------------------------------------------------------


class TestRunDnMatching:
	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_supplier_auto_match(self, mock_item_fuzzy, mock_item, mock_sup_fuzzy, mock_sup, sample_settings):
		"""Supplier matched via alias/exact."""
		mock_sup.return_value = ("Acme Ltd", "Auto Matched")
		mock_item.return_value = (None, "Unmatched")
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		doc = MockOCRDN(supplier_name_ocr="Acme Materials")
		doc.items = [
			SimpleNamespace(
				description_ocr="Steel Rod",
				item_name="Steel Rod",
				item_code=None,
				match_status="Unmatched",
			)
		]
		_run_dn_matching(doc, sample_settings)

		assert doc.supplier == "Acme Ltd"
		assert doc.supplier_match_status == "Auto Matched"

	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_supplier_fuzzy_match(
		self, mock_item_fuzzy, mock_item, mock_sup_fuzzy, mock_sup, sample_settings
	):
		"""Supplier matched via fuzzy fallback."""
		mock_sup.return_value = (None, "Unmatched")
		mock_sup_fuzzy.return_value = ("Acme Ltd", "Suggested", 85)
		mock_item.return_value = (None, "Unmatched")
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		doc = MockOCRDN(supplier_name_ocr="Acme Materials")
		doc.items = []
		_run_dn_matching(doc, sample_settings)

		assert doc.supplier == "Acme Ltd"
		assert doc.supplier_match_status == "Suggested"

	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_item_matching(self, mock_item_fuzzy, mock_item, mock_sup_fuzzy, mock_sup, sample_settings):
		"""Items matched via alias/exact."""
		mock_sup.return_value = ("Test Supplier", "Auto Matched")
		mock_item.return_value = ("SR-12-6", "Auto Matched")
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		doc = MockOCRDN(supplier_name_ocr="Test Supplier")
		doc.items = [
			SimpleNamespace(
				description_ocr="Steel Rod",
				item_name="SR-12-6",
				item_code=None,
				match_status="Unmatched",
			)
		]
		_run_dn_matching(doc, sample_settings)

		assert doc.items[0].item_code == "SR-12-6"
		assert doc.items[0].match_status == "Auto Matched"

	@patch("erpocr_integration.tasks.matching.match_supplier")
	@patch("erpocr_integration.tasks.matching.match_supplier_fuzzy")
	@patch("erpocr_integration.tasks.matching.match_item")
	@patch("erpocr_integration.tasks.matching.match_item_fuzzy")
	def test_no_match_stays_unmatched(
		self, mock_item_fuzzy, mock_item, mock_sup_fuzzy, mock_sup, sample_settings
	):
		"""Items with no match stay Unmatched."""
		mock_sup.return_value = (None, "Unmatched")
		mock_sup_fuzzy.return_value = (None, "Unmatched", 0)
		mock_item.return_value = (None, "Unmatched")
		mock_item_fuzzy.return_value = (None, "Unmatched", 0)

		doc = MockOCRDN(supplier_name_ocr="Unknown")
		doc.items = [
			SimpleNamespace(
				description_ocr="Mystery Item",
				item_name="Mystery Item",
				item_code=None,
				match_status="Unmatched",
			)
		]
		_run_dn_matching(doc, sample_settings)

		assert doc.supplier is None
		assert doc.supplier_match_status == "Unmatched"
		assert doc.items[0].item_code is None
		assert doc.items[0].match_status == "Unmatched"


# ---------------------------------------------------------------------------
# TestDocEvents
# ---------------------------------------------------------------------------


class TestDocEvents:
	def test_on_submit_po_marks_completed(self, mock_frappe):
		"""PO submit marks linked OCR DN as Completed."""
		mock_frappe.get_all.return_value = ["OCR-DN-00001"]

		po_doc = SimpleNamespace(doctype="Purchase Order", name="PO-00001")
		update_ocr_dn_on_submit(po_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_once_with(
			"OCR Delivery Note", "OCR-DN-00001", "status", "Completed"
		)

	def test_on_submit_pr_marks_completed(self, mock_frappe):
		"""PR submit marks linked OCR DN as Completed."""
		mock_frappe.get_all.return_value = ["OCR-DN-00002"]

		pr_doc = SimpleNamespace(doctype="Purchase Receipt", name="PR-00001")
		update_ocr_dn_on_submit(pr_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_once_with(
			"OCR Delivery Note", "OCR-DN-00002", "status", "Completed"
		)

	def test_on_cancel_po_clears_link(self, mock_frappe):
		"""PO cancel clears link and resets status."""
		mock_frappe.get_all.return_value = ["OCR-DN-00001"]
		mock_dn = MockOCRDN(name="OCR-DN-00001", status="Completed")
		mock_frappe.get_doc.return_value = mock_dn

		po_doc = SimpleNamespace(doctype="Purchase Order", name="PO-00001")
		update_ocr_dn_on_cancel(po_doc, "on_cancel")

		assert mock_dn.purchase_order_result == ""
		assert mock_dn.document_type == ""
		assert mock_dn.status == "Pending"

	def test_on_cancel_pr_clears_link(self, mock_frappe):
		"""PR cancel clears link and resets status."""
		mock_frappe.get_all.return_value = ["OCR-DN-00001"]
		mock_dn = MockOCRDN(name="OCR-DN-00001", status="Completed")
		mock_frappe.get_doc.return_value = mock_dn

		pr_doc = SimpleNamespace(doctype="Purchase Receipt", name="PR-00001")
		update_ocr_dn_on_cancel(pr_doc, "on_cancel")

		assert mock_dn.purchase_receipt == ""
		assert mock_dn.document_type == ""

	def test_unrelated_doctype_ignored(self, mock_frappe):
		"""Unrelated doctypes are ignored."""
		doc = SimpleNamespace(doctype="Sales Invoice", name="SI-00001")
		update_ocr_dn_on_submit(doc, "on_submit")
		mock_frappe.get_all.assert_not_called()


# ---------------------------------------------------------------------------
# TestMatchDnPoItems
# ---------------------------------------------------------------------------


class TestMatchDnPoItems:
	def test_matches_by_item_code(self, mock_frappe):
		"""Items matched by item_code to PO items."""
		ocr_dn = MockOCRDN(
			supplier="Acme Ltd",
			company="Test Company",
			items=[
				SimpleNamespace(
					idx=1,
					description_ocr="Steel Rod",
					item_code="SR-12-6",
					item_name="Steel Rod",
					qty=50,
				)
			],
		)
		po_doc = MagicMock()
		po_doc.supplier = "Acme Ltd"
		po_doc.company = "Test Company"
		po_doc.items = [MockPOItem(name="poi-001", item_code="SR-12-6", qty=100, received_qty=20)]

		mock_frappe.get_doc.side_effect = [ocr_dn, po_doc]

		result = match_dn_po_items("OCR-DN-00001", "PO-00001")

		assert len(result["matches"]) == 1
		match = result["matches"][0]["match"]
		assert match is not None
		assert match["purchase_order_item"] == "poi-001"
		assert match["po_qty"] == 100
		assert match["po_remaining_qty"] == 80  # 100 - 20 received

	def test_unmatched_items(self, mock_frappe):
		"""Items without matching PO items are returned unmatched."""
		ocr_dn = MockOCRDN(
			supplier="Acme Ltd",
			company="Test Company",
			items=[
				SimpleNamespace(
					idx=1,
					description_ocr="Unknown Item",
					item_code="UNKNOWN",
					item_name="Unknown",
					qty=10,
				)
			],
		)
		po_doc = MagicMock()
		po_doc.supplier = "Acme Ltd"
		po_doc.company = "Test Company"
		po_doc.items = [MockPOItem(name="poi-001", item_code="SR-12-6")]

		mock_frappe.get_doc.side_effect = [ocr_dn, po_doc]

		result = match_dn_po_items("OCR-DN-00001", "PO-00001")

		assert result["matches"][0]["match"] is None
		assert len(result["unmatched_po"]) == 1

	def test_supplier_mismatch_throws(self, mock_frappe):
		"""Throws when supplier doesn't match."""
		ocr_dn = MockOCRDN(supplier="Acme Ltd", company="Test Company")
		po_doc = MagicMock()
		po_doc.supplier = "Other Supplier"
		po_doc.company = "Test Company"

		mock_frappe.get_doc.side_effect = [ocr_dn, po_doc]

		with pytest.raises(Exception):
			match_dn_po_items("OCR-DN-00001", "PO-00001")

	def test_company_mismatch_throws(self, mock_frappe):
		"""Throws when company doesn't match."""
		ocr_dn = MockOCRDN(supplier="Acme Ltd", company="Company A")
		po_doc = MagicMock()
		po_doc.supplier = "Acme Ltd"
		po_doc.company = "Company B"

		mock_frappe.get_doc.side_effect = [ocr_dn, po_doc]

		with pytest.raises(Exception):
			match_dn_po_items("OCR-DN-00001", "PO-00001")

	def test_remaining_qty_calculation(self, mock_frappe):
		"""Remaining qty = PO qty - received_qty."""
		ocr_dn = MockOCRDN(
			supplier="Acme Ltd",
			company="Test Company",
			items=[
				SimpleNamespace(
					idx=1,
					description_ocr="Steel Rod",
					item_code="SR-12-6",
					item_name="Steel Rod",
					qty=50,
				)
			],
		)
		po_doc = MagicMock()
		po_doc.supplier = "Acme Ltd"
		po_doc.company = "Test Company"
		po_doc.items = [MockPOItem(name="poi-001", item_code="SR-12-6", qty=200, received_qty=150)]

		mock_frappe.get_doc.side_effect = [ocr_dn, po_doc]

		result = match_dn_po_items("OCR-DN-00001", "PO-00001")
		assert result["matches"][0]["match"]["po_remaining_qty"] == 50


# ---------------------------------------------------------------------------
# TestRetryDnExtraction
# ---------------------------------------------------------------------------


class TestRetryDnExtraction:
	def test_blocks_non_error(self, mock_frappe):
		"""Cannot retry non-Error records."""
		mock_dn = MockOCRDN(status="Matched")
		mock_frappe.get_doc.return_value = mock_dn
		with pytest.raises(Exception):
			retry_dn_extraction("OCR-DN-00001")

	def test_blocks_no_file(self, mock_frappe):
		"""Throws when no file is available for retry."""
		mock_dn = MockOCRDN(status="Error")
		mock_frappe.get_doc.return_value = mock_dn
		mock_frappe.get_all.return_value = []

		with pytest.raises(Exception):
			retry_dn_extraction("OCR-DN-00001")

	@patch("erpocr_integration.tasks.drive_integration.download_file_from_drive")
	def test_retries_from_drive(self, mock_download, mock_frappe):
		"""Retries extraction using Drive file."""
		mock_dn = MockOCRDN(status="Error", drive_file_id="drive-123")
		mock_frappe.get_doc.return_value = mock_dn
		mock_download.return_value = b"%PDF-1.4 test"
		mock_frappe.get_all.return_value = [SimpleNamespace(file_name="scan.pdf")]

		result = retry_dn_extraction("OCR-DN-00001")

		mock_frappe.enqueue.assert_called_once()
		assert "message" in result

	def test_retries_from_attachment(self, mock_frappe):
		"""Retries extraction using local attachment."""
		mock_dn = MockOCRDN(status="Error", drive_file_id=None)
		mock_file = MagicMock()
		mock_file.get_content.return_value = b"%PDF-1.4 test"

		def get_doc_side_effect(doctype, name=None):
			if doctype == "OCR Delivery Note":
				return mock_dn
			if doctype == "File":
				return mock_file
			return MagicMock()

		mock_frappe.get_doc.side_effect = get_doc_side_effect
		mock_frappe.get_all.return_value = [SimpleNamespace(name="FILE-001", file_name="scan.pdf")]

		retry_dn_extraction("OCR-DN-00001")

		mock_frappe.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# TestGetOpenPurchaseOrders
# ---------------------------------------------------------------------------


class TestGetOpenPurchaseOrders:
	def test_returns_open_pos(self, mock_frappe):
		"""Returns open POs for supplier."""
		mock_frappe.get_list.return_value = [
			{
				"name": "PO-00001",
				"transaction_date": "2025-02-15",
				"grand_total": 5000,
				"status": "To Receive",
			}
		]
		result = get_open_purchase_orders_for_dn("Acme Ltd", "Test Company")
		assert len(result) == 1
		assert result[0]["name"] == "PO-00001"

	def test_permission_check(self, mock_frappe):
		"""Throws when no PO read permission."""
		mock_frappe.has_permission.return_value = False
		with pytest.raises(Exception):
			get_open_purchase_orders_for_dn("Acme Ltd", "Test Company")


# ---------------------------------------------------------------------------
# TestDnGeminiProcess
# ---------------------------------------------------------------------------


class TestDnGeminiProcess:
	@patch("erpocr_integration.tasks.gemini_extract.extract_delivery_note_data")
	@patch("erpocr_integration.dn_api._run_dn_matching")
	@patch("erpocr_integration.dn_api._populate_ocr_dn")
	def test_successful_extraction(
		self, mock_populate, mock_matching, mock_extract, mock_frappe, sample_settings
	):
		"""Successful extraction populates OCR DN and saves."""
		mock_extract.return_value = {
			"header_fields": {"supplier_name": "Test"},
			"line_items": [],
			"raw_response": "{}",
		}
		mock_dn = MockOCRDN()
		mock_frappe.get_doc.return_value = mock_dn
		mock_frappe.get_cached_doc.return_value = sample_settings

		dn_gemini_process(
			file_content=b"%PDF test",
			filename="scan.pdf",
			ocr_dn_name="OCR-DN-00001",
		)

		mock_extract.assert_called_once()
		mock_populate.assert_called_once()
		mock_matching.assert_called_once()

	@patch("erpocr_integration.tasks.gemini_extract.extract_delivery_note_data")
	def test_extraction_failure_sets_error(self, mock_extract, mock_frappe):
		"""Failed extraction sets status to Error."""
		mock_extract.side_effect = Exception("API failure")

		dn_gemini_process(
			file_content=b"%PDF test",
			filename="scan.pdf",
			ocr_dn_name="OCR-DN-00001",
		)

		# Check that status was set to Error
		mock_frappe.db.set_value.assert_any_call(
			"OCR Delivery Note",
			"OCR-DN-00001",
			{"status": "Error", "error_log": "ERR-00001"},
		)
