"""Tests for OCR Delivery Note controller (status, PO/PR creation, unlink, no action)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.erpnext_ocr.doctype.ocr_delivery_note.ocr_delivery_note import (
	OCRDeliveryNote,
	_resolve_rate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ocr_dn(**overrides):
	"""Create an OCRDeliveryNote instance with sensible defaults for testing."""
	doc = OCRDeliveryNote.__new__(OCRDeliveryNote)
	doc.name = "OCR-DN-00001"
	doc.status = "Needs Review"
	doc.supplier = "Test Supplier"
	doc.supplier_name_ocr = "Test Supplier OCR"
	doc.supplier_match_status = "Auto Matched"
	doc.company = "Test Company"
	doc.delivery_note_number = "DN-001"
	doc.delivery_date = "2025-02-20"
	doc.vehicle_number = ""
	doc.driver_name = ""
	doc.received_by = ""
	doc.confidence = 90.0
	doc.document_type = ""
	doc.purchase_order = None
	doc.purchase_order_result = None
	doc.purchase_receipt = None
	doc.no_action_reason = None
	doc.drive_link = None
	doc.drive_file_id = None
	doc.drive_folder_path = None
	doc.source_type = "Gemini Drive Scan"
	doc.items = []
	doc.save = MagicMock()
	doc.reload = MagicMock()
	doc.db_set = MagicMock()
	doc.has_value_changed = MagicMock(return_value=False)

	for key, value in overrides.items():
		setattr(doc, key, value)
	return doc


def _make_dn_item(**overrides):
	"""Create a mock OCR Delivery Note Item row."""
	defaults = dict(
		doctype="OCR Delivery Note Item",
		name="dn-item-001",
		description_ocr="Steel Rod",
		item_code="SR-12-6",
		item_name="Steel Rod 12mm x 6m",
		qty=50,
		uom="pcs",
		match_status="Auto Matched",
		purchase_order_item=None,
		po_qty=0,
		po_remaining_qty=0,
		idx=1,
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# TestResolveRate
# ---------------------------------------------------------------------------


class TestResolveRate:
	def test_rate_from_po_item(self, mock_frappe):
		"""When PO item is linked, use PO item rate."""
		mock_frappe.db.get_value.return_value = 125.50
		rate = _resolve_rate("SR-12-6", "po-item-001")
		assert rate == 125.50

	def test_rate_from_last_purchase_rate(self, mock_frappe):
		"""When no PO item, fall back to last_purchase_rate."""
		mock_frappe.db.get_value.side_effect = [
			None,  # PO item rate
			SimpleNamespace(last_purchase_rate=100.0, standard_rate=80.0),
		]
		rate = _resolve_rate("SR-12-6", "po-item-001")
		assert rate == 100.0

	def test_rate_from_standard_rate(self, mock_frappe):
		"""Fall back to standard_rate when no last_purchase_rate."""
		mock_frappe.db.get_value.side_effect = [
			None,  # PO item rate
			SimpleNamespace(last_purchase_rate=0, standard_rate=75.0),
		]
		rate = _resolve_rate("SR-12-6", "po-item-001")
		assert rate == 75.0

	def test_rate_zero_fallback(self, mock_frappe):
		"""Return 0 when no rate source available."""
		mock_frappe.db.get_value.return_value = None
		rate = _resolve_rate(None)
		assert rate == 0

	def test_rate_no_po_item(self, mock_frappe):
		"""When no PO item, use item master rates."""
		mock_frappe.db.get_value.return_value = SimpleNamespace(last_purchase_rate=200.0, standard_rate=150.0)
		rate = _resolve_rate("SR-12-6")
		assert rate == 200.0


# ---------------------------------------------------------------------------
# TestUpdateStatus
# ---------------------------------------------------------------------------


class TestUpdateStatus:
	def test_pending_with_data(self):
		"""Status becomes Needs Review when only supplier OCR text is present."""
		doc = _make_ocr_dn(
			status="Pending",
			supplier=None,
			supplier_name_ocr="Some Supplier",
			items=[],
		)
		doc._update_status()
		assert doc.status == "Needs Review"

	def test_matched_when_all_items_matched(self):
		"""Status becomes Matched when supplier + all items matched."""
		items = [
			_make_dn_item(item_code="SR-12-6", match_status="Auto Matched"),
			_make_dn_item(item_code="CM-50", match_status="Confirmed", idx=2),
		]
		doc = _make_ocr_dn(status="Pending", items=items)
		doc._update_status()
		assert doc.status == "Matched"

	def test_needs_review_when_items_unmatched(self):
		"""Status stays Needs Review when items are unmatched."""
		items = [
			_make_dn_item(item_code=None, match_status="Unmatched"),
		]
		doc = _make_ocr_dn(status="Pending", items=items)
		doc._update_status()
		assert doc.status == "Needs Review"

	def test_preserves_completed(self):
		"""_update_status does not overwrite Completed."""
		doc = _make_ocr_dn(status="Completed")
		doc._update_status()
		assert doc.status == "Completed"

	def test_preserves_draft_created(self):
		"""_update_status does not overwrite Draft Created."""
		doc = _make_ocr_dn(status="Draft Created")
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_preserves_no_action(self):
		"""_update_status does not overwrite No Action."""
		doc = _make_ocr_dn(status="No Action")
		doc._update_status()
		assert doc.status == "No Action"

	def test_preserves_error(self):
		"""_update_status does not overwrite Error."""
		doc = _make_ocr_dn(status="Error")
		doc._update_status()
		assert doc.status == "Error"

	def test_draft_created_when_po_linked(self):
		"""Status becomes Draft Created when PO result is linked."""
		doc = _make_ocr_dn(status="Pending", purchase_order_result="PO-00001")
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_draft_created_when_pr_linked(self):
		"""Status becomes Draft Created when PR is linked."""
		doc = _make_ocr_dn(status="Pending", purchase_receipt="PR-00001")
		doc._update_status()
		assert doc.status == "Draft Created"


# ---------------------------------------------------------------------------
# TestCreatePurchaseOrder
# ---------------------------------------------------------------------------


class TestCreatePurchaseOrder:
	def test_creates_po_draft(self, mock_frappe):
		"""Successfully creates a PO draft from DN with matched items."""
		mock_po = MagicMock()
		mock_po.name = "PO-00001"
		mock_po.items = []
		mock_frappe.get_doc.return_value = mock_po
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			purchase_order_result=None, purchase_receipt=None
		)
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="Stores - TC",
			default_warehouse="Stores - TC",
			get=lambda k, d=None: "Stores - TC" if "warehouse" in k else d,
		)
		mock_frappe.get_all.return_value = []

		items = [_make_dn_item(item_code="SR-12-6", qty=50)]
		doc = _make_ocr_dn(
			status="Matched",
			document_type="Purchase Order",
			items=items,
		)
		doc.create_purchase_order()

		assert doc.purchase_order_result == "PO-00001"
		assert doc.status == "Draft Created"
		mock_po.insert.assert_called_once()

	def test_blocks_wrong_status(self, mock_frappe):
		"""Blocks PO creation from non-Matched/Needs Review status."""
		doc = _make_ocr_dn(status="Completed", document_type="Purchase Order")
		with pytest.raises(Exception):
			doc.create_purchase_order()

	def test_blocks_wrong_document_type(self, mock_frappe):
		"""Blocks PO creation when document_type is not Purchase Order."""
		doc = _make_ocr_dn(status="Matched", document_type="Purchase Receipt")
		with pytest.raises(Exception):
			doc.create_purchase_order()

	def test_blocks_no_supplier(self, mock_frappe):
		"""Blocks PO creation when no supplier set."""
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			purchase_order_result=None, purchase_receipt=None
		)
		items = [_make_dn_item(item_code="SR-12-6")]
		doc = _make_ocr_dn(
			status="Matched",
			document_type="Purchase Order",
			supplier=None,
			items=items,
		)
		with pytest.raises(Exception):
			doc.create_purchase_order()

	def test_skips_unmatched_items(self, mock_frappe):
		"""Unmatched items are skipped; PO only contains matched items."""
		mock_po = MagicMock()
		mock_po.name = "PO-00002"
		mock_po.items = []
		mock_frappe.get_doc.return_value = mock_po
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			purchase_order_result=None, purchase_receipt=None
		)
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="",
			default_warehouse="",
			get=lambda k, d=None: d,
		)
		mock_frappe.get_all.return_value = []

		items = [
			_make_dn_item(item_code="SR-12-6", idx=1),
			_make_dn_item(item_code=None, match_status="Unmatched", idx=2),
		]
		doc = _make_ocr_dn(status="Matched", document_type="Purchase Order", items=items)
		doc.create_purchase_order()

		# get_doc called with PO dict — check items list length
		call_args = mock_frappe.get_doc.call_args
		po_dict = call_args[0][0]
		assert len(po_dict["items"]) == 1

	def test_blocks_duplicate_creation(self, mock_frappe):
		"""Row-lock prevents duplicate PO creation."""
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			purchase_order_result="PO-EXISTS", purchase_receipt=None
		)
		doc = _make_ocr_dn(status="Matched", document_type="Purchase Order")
		with pytest.raises(Exception):
			doc.create_purchase_order()

	def test_blocks_no_matched_items(self, mock_frappe):
		"""Blocks PO creation when all items are unmatched."""
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			purchase_order_result=None, purchase_receipt=None
		)
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="",
			default_warehouse="",
			get=lambda k, d=None: d,
		)
		items = [_make_dn_item(item_code=None, match_status="Unmatched")]
		doc = _make_ocr_dn(status="Matched", document_type="Purchase Order", items=items)
		with pytest.raises(Exception):
			doc.create_purchase_order()


# ---------------------------------------------------------------------------
# TestCreatePurchaseReceipt
# ---------------------------------------------------------------------------


class TestCreatePurchaseReceipt:
	def test_creates_pr_draft(self, mock_frappe):
		"""Successfully creates PR draft with rates from item master."""
		mock_pr = MagicMock()
		mock_pr.name = "PR-00001"
		mock_pr.items = []
		mock_frappe.get_doc.return_value = mock_pr

		def db_get_value_side_effect(doctype, name, fields=None, **kw):
			if doctype == "OCR Delivery Note":
				return SimpleNamespace(purchase_order_result=None, purchase_receipt=None)
			if doctype == "Item" and fields:
				return SimpleNamespace(last_purchase_rate=100.0, standard_rate=80.0)
			if doctype == "Item":
				return 1  # is_stock_item
			return None

		mock_frappe.db.get_value.side_effect = db_get_value_side_effect
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="Stores - TC",
			default_warehouse="Stores - TC",
			get=lambda k, d=None: "Stores - TC" if "warehouse" in k else d,
		)
		mock_frappe.get_all.return_value = []

		items = [_make_dn_item(item_code="SR-12-6", qty=50)]
		doc = _make_ocr_dn(
			status="Matched",
			document_type="Purchase Receipt",
			items=items,
		)
		doc.create_purchase_receipt()

		assert doc.purchase_receipt == "PR-00001"
		assert doc.status == "Draft Created"

	def test_blocks_wrong_status(self, mock_frappe):
		"""Blocks PR creation from Draft Created status."""
		doc = _make_ocr_dn(status="Draft Created", document_type="Purchase Receipt")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()

	def test_blocks_wrong_document_type(self, mock_frappe):
		"""Blocks PR creation when document_type is not Purchase Receipt."""
		doc = _make_ocr_dn(status="Matched", document_type="Purchase Order")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()

	def test_pr_includes_po_refs(self, mock_frappe):
		"""PR items include PO refs when PO is linked."""
		mock_pr = MagicMock()
		mock_pr.name = "PR-00002"
		mock_pr.items = []
		mock_frappe.get_doc.return_value = mock_pr

		def db_get_value_side_effect(doctype, name, fields=None, **kw):
			if doctype == "OCR Delivery Note":
				return SimpleNamespace(purchase_order_result=None, purchase_receipt=None)
			if doctype == "Purchase Order Item":
				return 100.0  # PO rate
			if doctype == "Item":
				return 1  # is_stock_item
			return None

		mock_frappe.db.get_value.side_effect = db_get_value_side_effect
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="",
			default_warehouse="",
			get=lambda k, d=None: d,
		)
		mock_frappe.get_all.return_value = []

		items = [
			_make_dn_item(
				item_code="SR-12-6",
				purchase_order_item="poi-001",
				qty=50,
			)
		]
		doc = _make_ocr_dn(
			status="Matched",
			document_type="Purchase Receipt",
			purchase_order="PO-00001",
			items=items,
		)
		doc.create_purchase_receipt()

		call_args = mock_frappe.get_doc.call_args
		pr_dict = call_args[0][0]
		pr_item = pr_dict["items"][0]
		assert pr_item["purchase_order"] == "PO-00001"
		assert pr_item["purchase_order_item"] == "poi-001"

	def test_pr_po_fallback_when_row_refs_missing(self, mock_frappe):
		"""PR auto-matches PO items by item_code when row-level refs are missing."""
		mock_pr = MagicMock()
		mock_pr.name = "PR-00003"
		mock_pr.items = []
		mock_frappe.get_doc.return_value = mock_pr

		def db_get_value_side_effect(doctype, name, fields=None, **kw):
			if doctype == "OCR Delivery Note":
				return SimpleNamespace(purchase_order_result=None, purchase_receipt=None)
			if doctype == "Purchase Order Item":
				return 100.0  # PO rate
			if doctype == "Item" and fields == "is_stock_item":
				return 1
			if doctype == "Item":
				return SimpleNamespace(last_purchase_rate=50, standard_rate=60)
			return None

		mock_frappe.db.get_value.side_effect = db_get_value_side_effect
		mock_frappe.get_cached_doc.return_value = SimpleNamespace(
			dn_default_warehouse="",
			default_warehouse="",
			get=lambda k, d=None: d,
		)
		# Return PO items for the fallback lookup
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="poi-auto-001", item_code="SR-12-6"),
		]

		items = [
			_make_dn_item(
				item_code="SR-12-6",
				purchase_order_item=None,  # No row-level ref (user skipped Match PO Items)
				qty=50,
			)
		]
		doc = _make_ocr_dn(
			status="Matched",
			document_type="Purchase Receipt",
			purchase_order="PO-00001",
			items=items,
		)
		doc.create_purchase_receipt()

		call_args = mock_frappe.get_doc.call_args
		pr_dict = call_args[0][0]
		pr_item = pr_dict["items"][0]
		assert pr_item["purchase_order"] == "PO-00001"
		assert pr_item["purchase_order_item"] == "poi-auto-001"


# ---------------------------------------------------------------------------
# TestUnlinkDocument
# ---------------------------------------------------------------------------


class TestUnlinkDocument:
	def test_unlinks_po(self, mock_frappe):
		"""Unlink & Reset deletes draft PO and resets status."""
		mock_frappe.db.get_value.return_value = 0  # docstatus=0 (draft)
		doc = _make_ocr_dn(
			status="Draft Created",
			purchase_order_result="PO-00001",
		)
		doc.unlink_document()

		doc.db_set.assert_any_call("purchase_order_result", "")
		doc.db_set.assert_any_call("document_type", "")
		doc.db_set.assert_any_call("status", "Pending")
		mock_frappe.delete_doc.assert_called_once_with("Purchase Order", "PO-00001", force=True)

	def test_unlinks_pr(self, mock_frappe):
		"""Unlink & Reset deletes draft PR and resets status."""
		mock_frappe.db.get_value.return_value = 0
		doc = _make_ocr_dn(
			status="Draft Created",
			purchase_receipt="PR-00001",
		)
		doc.unlink_document()

		doc.db_set.assert_any_call("purchase_receipt", "")
		mock_frappe.delete_doc.assert_called_once_with("Purchase Receipt", "PR-00001", force=True)

	def test_blocks_submitted(self, mock_frappe):
		"""Cannot unlink submitted document."""
		mock_frappe.db.get_value.return_value = 1  # submitted
		doc = _make_ocr_dn(
			status="Draft Created",
			purchase_order_result="PO-00001",
		)
		with pytest.raises(Exception):
			doc.unlink_document()

	def test_blocks_non_draft_created(self, mock_frappe):
		"""Cannot unlink when status is not Draft Created."""
		doc = _make_ocr_dn(status="Matched")
		with pytest.raises(Exception):
			doc.unlink_document()


# ---------------------------------------------------------------------------
# TestMarkNoAction
# ---------------------------------------------------------------------------


class TestMarkNoAction:
	def test_marks_no_action(self, mock_frappe):
		"""Successfully marks as No Action with reason."""
		doc = _make_ocr_dn(status="Needs Review")
		doc.mark_no_action("Not a delivery note")
		assert doc.status == "No Action"
		assert doc.no_action_reason == "Not a delivery note"

	def test_blocks_from_completed(self, mock_frappe):
		"""Cannot mark as No Action from Completed."""
		doc = _make_ocr_dn(status="Completed")
		with pytest.raises(Exception):
			doc.mark_no_action("test")

	def test_blocks_from_draft_created(self, mock_frappe):
		"""Cannot mark as No Action from Draft Created."""
		doc = _make_ocr_dn(status="Draft Created")
		with pytest.raises(Exception):
			doc.mark_no_action("test")

	def test_requires_reason(self, mock_frappe):
		"""Reason is required."""
		doc = _make_ocr_dn(status="Needs Review")
		with pytest.raises(Exception):
			doc.mark_no_action("")

	def test_marks_from_error(self, mock_frappe):
		"""Can mark as No Action from Error status."""
		doc = _make_ocr_dn(status="Error")
		doc.mark_no_action("Bad scan — not processable")
		assert doc.status == "No Action"

	def test_marks_from_matched(self, mock_frappe):
		"""Can mark as No Action from Matched status."""
		doc = _make_ocr_dn(status="Matched")
		doc.mark_no_action("Not needed")
		assert doc.status == "No Action"

	def test_permission_check(self, mock_frappe):
		"""Permission check is enforced."""
		mock_frappe.has_permission.return_value = False
		doc = _make_ocr_dn(status="Needs Review")
		with pytest.raises(Exception):
			doc.mark_no_action("reason")


# ---------------------------------------------------------------------------
# TestAliases
# ---------------------------------------------------------------------------


class TestAliases:
	def test_saves_supplier_alias_on_confirm(self, mock_frappe):
		"""Saves supplier alias when match status is Confirmed."""
		doc = _make_ocr_dn(
			supplier_match_status="Confirmed",
			supplier_name_ocr="Acme OCR",
			supplier="Acme Ltd",
		)
		doc.has_value_changed.return_value = True
		mock_frappe.db.exists.return_value = False

		doc.on_update()

		mock_frappe.get_doc.assert_called_once()
		call_args = mock_frappe.get_doc.call_args[0][0]
		assert call_args["doctype"] == "OCR Supplier Alias"
		assert call_args["ocr_text"] == "Acme OCR"
		assert call_args["supplier"] == "Acme Ltd"

	def test_skips_alias_when_not_confirmed(self, mock_frappe):
		"""Does not save alias when match status is not Confirmed."""
		doc = _make_ocr_dn(
			supplier_match_status="Auto Matched",
			supplier_name_ocr="Acme OCR",
			supplier="Acme Ltd",
		)
		doc.has_value_changed.return_value = True
		doc.on_update()
		mock_frappe.get_doc.assert_not_called()

	def test_saves_item_alias_on_confirm(self, mock_frappe):
		"""Saves item alias when item match status is Confirmed."""
		items = [
			_make_dn_item(
				item_code="SR-12-6",
				description_ocr="Steel Rod 12mm",
				match_status="Confirmed",
			)
		]
		doc = _make_ocr_dn(supplier_match_status="Auto Matched", items=items)
		doc.has_value_changed.return_value = False
		mock_frappe.db.exists.return_value = False

		doc.on_update()

		call_args = mock_frappe.get_doc.call_args[0][0]
		assert call_args["doctype"] == "OCR Item Alias"


# ---------------------------------------------------------------------------
# TestCopyScanToDocument
# ---------------------------------------------------------------------------


class TestCopyScanToDocument:
	def test_copies_attachment(self, mock_frappe):
		"""Copies file attachment to the created document."""
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="FILE-001", file_url="/private/files/scan.pdf", file_name="scan.pdf")
		]
		mock_file = MagicMock()
		mock_frappe.get_doc.return_value = mock_file

		doc = _make_ocr_dn()
		doc._copy_scan_to_document("Purchase Order", "PO-00001")

		assert mock_frappe.get_doc.call_count >= 1

	def test_adds_drive_link_comment(self, mock_frappe):
		"""Adds Drive link as comment on the created document."""
		mock_frappe.get_all.return_value = []  # no attachments
		mock_doc = MagicMock()
		mock_frappe.get_doc.return_value = mock_doc

		doc = _make_ocr_dn(
			drive_link="https://drive.google.com/test",
			drive_folder_path="2025/02/Acme",
		)
		doc._copy_scan_to_document("Purchase Receipt", "PR-00001")

		mock_doc.add_comment.assert_called_once()
		comment_text = mock_doc.add_comment.call_args[0][1]
		assert "Google Drive" in comment_text
