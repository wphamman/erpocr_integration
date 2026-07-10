"""Tests for auto-draft logic."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpocr_integration.tasks.auto_draft import (
	_auto_detect_document_type,
	_auto_link_purchase_order,
	_invoice_date_in_fiscal_year,
	_is_high_confidence,
	attempt_auto_draft,
)


def _make_ocr_import(**overrides):
	"""Create a minimal OCR Import-like object for testing."""
	defaults = dict(
		supplier="SUP-001",
		supplier_match_status="Auto Matched",
		items=[],
		status="Matched",
		# Totals fields a real OCR Import always carries (Q11 gate reads these).
		# Defaults leave the gate a no-op (line sum 0 → unverifiable → pass); the
		# Q11 tests set explicit values.
		subtotal=0.0,
		tax_amount=0.0,
		total_amount=0.0,
		currency="ZAR",
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


def _make_item(**overrides):
	defaults = dict(
		item_code="ITEM-001",
		match_status="Auto Matched",
		description_ocr="Test item",
		qty=1.0,
		rate=0.0,
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


class TestIsHighConfidence:
	def test_high_confidence_all_auto_matched(self):
		doc = _make_ocr_import(items=[_make_item()])
		is_high, reason = _is_high_confidence(doc)
		assert is_high is True
		assert reason == ""

	def test_high_confidence_confirmed_supplier(self):
		doc = _make_ocr_import(
			supplier_match_status="Confirmed",
			items=[_make_item()],
		)
		is_high, _ = _is_high_confidence(doc)
		assert is_high is True

	def test_low_confidence_fuzzy_supplier(self):
		doc = _make_ocr_import(
			supplier_match_status="Suggested",
			items=[_make_item()],
		)
		is_high, reason = _is_high_confidence(doc)
		assert is_high is False
		assert "supplier" in reason.lower()

	def test_low_confidence_unmatched_supplier(self):
		doc = _make_ocr_import(
			supplier_match_status="Unmatched",
			supplier=None,
			items=[_make_item()],
		)
		is_high, _ = _is_high_confidence(doc)
		assert is_high is False

	def test_low_confidence_no_supplier(self):
		doc = _make_ocr_import(supplier=None, items=[_make_item()])
		is_high, _ = _is_high_confidence(doc)
		assert is_high is False

	def test_low_confidence_fuzzy_item(self):
		doc = _make_ocr_import(
			items=[_make_item(match_status="Suggested")],
		)
		is_high, reason = _is_high_confidence(doc)
		assert is_high is False
		assert "item" in reason.lower()

	def test_low_confidence_unmatched_item(self):
		doc = _make_ocr_import(
			items=[_make_item(item_code=None, match_status="Unmatched")],
		)
		is_high, _ = _is_high_confidence(doc)
		assert is_high is False

	def test_low_confidence_no_items(self):
		doc = _make_ocr_import(items=[])
		is_high, reason = _is_high_confidence(doc)
		assert is_high is False
		assert "no items" in reason.lower()

	def test_mixed_items_one_fuzzy(self):
		doc = _make_ocr_import(
			items=[
				_make_item(item_code="A", match_status="Auto Matched"),
				_make_item(item_code="B", match_status="Suggested"),
			],
		)
		is_high, _ = _is_high_confidence(doc)
		assert is_high is False

	def test_all_items_service_mapped(self):
		"""Service mapping returns 'Auto Matched' — should be high confidence."""
		doc = _make_ocr_import(
			items=[_make_item(match_status="Auto Matched")],
		)
		is_high, _ = _is_high_confidence(doc)
		assert is_high is True


class TestAutoLinkPurchaseOrder:
	def test_links_po_when_all_items_match(self, mock_frappe):
		doc = _make_ocr_import(
			supplier="SUP-001",
			company="Test Co",
			purchase_order=None,
			items=[_make_item(item_code="ITEM-A"), _make_item(item_code="ITEM-B")],
		)
		mock_frappe.get_list.return_value = [
			SimpleNamespace(name="PO-001", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
		]
		mock_frappe.get_doc.return_value = SimpleNamespace(
			items=[
				SimpleNamespace(item_code="ITEM-A"),
				SimpleNamespace(item_code="ITEM-B"),
			]
		)

		linked = _auto_link_purchase_order(doc)

		assert linked is True
		assert doc.purchase_order == "PO-001"

	def test_no_link_when_no_open_pos(self, mock_frappe):
		doc = _make_ocr_import(
			supplier="SUP-001",
			company="Test Co",
			purchase_order=None,
			items=[_make_item(item_code="ITEM-A")],
		)
		mock_frappe.get_list.return_value = []

		linked = _auto_link_purchase_order(doc)

		assert linked is False
		assert not doc.purchase_order

	def test_no_link_when_items_dont_match(self, mock_frappe):
		doc = _make_ocr_import(
			supplier="SUP-001",
			company="Test Co",
			purchase_order=None,
			items=[_make_item(item_code="ITEM-A")],
		)
		mock_frappe.get_list.return_value = [
			SimpleNamespace(name="PO-001", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
		]
		mock_frappe.get_doc.return_value = SimpleNamespace(items=[SimpleNamespace(item_code="ITEM-X")])

		linked = _auto_link_purchase_order(doc)

		assert linked is False

	def test_no_link_when_no_supplier(self, mock_frappe):
		doc = _make_ocr_import(supplier=None, company="Test Co", purchase_order=None, items=[_make_item()])

		linked = _auto_link_purchase_order(doc)

		assert linked is False

	def test_picks_po_with_best_item_coverage(self, mock_frappe):
		doc = _make_ocr_import(
			supplier="SUP-001",
			company="Test Co",
			purchase_order=None,
			items=[_make_item(item_code="ITEM-A"), _make_item(item_code="ITEM-B")],
		)
		mock_frappe.get_list.return_value = [
			SimpleNamespace(name="PO-001", transaction_date="2026-01-15", grand_total=500, status="To Bill"),
			SimpleNamespace(name="PO-002", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
		]

		def get_doc_handler(doctype, name=None):
			if name == "PO-001":
				return SimpleNamespace(items=[SimpleNamespace(item_code="ITEM-A")])
			if name == "PO-002":
				return SimpleNamespace(
					items=[
						SimpleNamespace(item_code="ITEM-A"),
						SimpleNamespace(item_code="ITEM-B"),
					]
				)
			return SimpleNamespace(items=[])

		mock_frappe.get_doc.side_effect = get_doc_handler

		linked = _auto_link_purchase_order(doc)

		assert linked is True
		assert doc.purchase_order == "PO-002"

	def test_skips_po_linking_when_po_already_set(self, mock_frappe):
		doc = _make_ocr_import(
			supplier="SUP-001",
			company="Test Co",
			purchase_order="PO-EXISTING",
			items=[_make_item()],
		)

		linked = _auto_link_purchase_order(doc)

		assert linked is True
		assert doc.purchase_order == "PO-EXISTING"


class TestAutoDetectDocumentType:
	def test_defaults_to_purchase_invoice(self):
		doc = _make_ocr_import(purchase_order=None)
		assert _auto_detect_document_type(doc) == "Purchase Invoice"

	def test_purchase_invoice_when_po_linked(self):
		doc = _make_ocr_import(purchase_order="PO-001")
		assert _auto_detect_document_type(doc) == "Purchase Invoice"

	def test_purchase_invoice_when_no_po(self):
		doc = _make_ocr_import(purchase_order=None, items=[_make_item()])
		assert _auto_detect_document_type(doc) == "Purchase Invoice"


def _make_settings(**overrides):
	defaults = dict(enable_auto_draft=1)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


class TestAttemptAutoDraft:
	def test_creates_pi_when_high_confidence_and_matched(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			status="Matched",
			document_type="",
			purchase_order=None,
			purchase_invoice=None,
			purchase_receipt=None,
			journal_entry=None,
			company="Test Co",
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock(return_value="PI-001")
		doc.save = MagicMock()
		settings = _make_settings()
		mock_frappe.get_list.return_value = []  # No open POs

		result = attempt_auto_draft(doc, settings)

		assert result is True
		assert doc.document_type == "Purchase Invoice"
		assert doc.auto_drafted == 1
		doc.save.assert_called()
		doc.create_purchase_invoice.assert_called_once()

	def test_skips_when_auto_draft_disabled(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			document_type="",
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock()
		settings = _make_settings(enable_auto_draft=0)

		result = attempt_auto_draft(doc, settings)

		assert result is False
		doc.create_purchase_invoice.assert_not_called()

	def test_skips_when_status_needs_review(self, mock_frappe):
		"""High confidence matches but _update_status set Needs Review (e.g. missing expense_account)."""
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			status="Needs Review",
			document_type="",
			purchase_invoice=None,
			purchase_receipt=None,
			journal_entry=None,
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock()
		doc.save = MagicMock()
		settings = _make_settings()

		result = attempt_auto_draft(doc, settings)

		assert result is False
		# Reason persisted via db.set_value (not in-memory attribute)
		mock_frappe.db.set_value.assert_called()
		call_args = mock_frappe.db.set_value.call_args
		assert "Needs Review" in str(call_args)
		doc.create_purchase_invoice.assert_not_called()

	def test_skips_when_low_confidence(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			document_type="",
			supplier_match_status="Suggested",
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock()
		doc.save = MagicMock()
		settings = _make_settings()

		result = attempt_auto_draft(doc, settings)

		assert result is False
		mock_frappe.db.set_value.assert_called()
		doc.create_purchase_invoice.assert_not_called()

	def test_skips_when_document_already_created(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			document_type="Purchase Invoice",
			purchase_invoice="PI-EXISTING",
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock()
		settings = _make_settings()

		result = attempt_auto_draft(doc, settings)

		assert result is False
		doc.create_purchase_invoice.assert_not_called()

	def test_links_po_before_creating_pi(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			status="Matched",
			document_type="",
			purchase_order=None,
			purchase_invoice=None,
			purchase_receipt=None,
			journal_entry=None,
			company="Test Co",
			items=[_make_item(item_code="ITEM-A")],
		)
		doc.create_purchase_invoice = MagicMock(return_value="PI-001")
		doc.save = MagicMock()
		settings = _make_settings()

		# Mock: open PO with matching items
		mock_frappe.get_list.return_value = [
			SimpleNamespace(name="PO-001", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
		]
		mock_frappe.get_doc.return_value = SimpleNamespace(items=[SimpleNamespace(item_code="ITEM-A")])

		result = attempt_auto_draft(doc, settings)

		assert result is True
		assert doc.purchase_order == "PO-001"
		doc.create_purchase_invoice.assert_called_once()

	def test_falls_back_gracefully_on_create_error(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			status="Matched",
			document_type="",
			purchase_order=None,
			purchase_invoice=None,
			purchase_receipt=None,
			journal_entry=None,
			company="Test Co",
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock(side_effect=Exception("PI creation failed"))
		doc.save = MagicMock()
		settings = _make_settings()
		mock_frappe.get_list.return_value = []

		result = attempt_auto_draft(doc, settings)

		assert result is False
		assert "PI creation failed" in (doc.auto_draft_skipped_reason or "")

	def test_sets_skipped_reason_on_low_confidence(self, mock_frappe):
		doc = _make_ocr_import(
			name="OCR-IMP-001",
			document_type="",
			supplier=None,
			supplier_match_status="Unmatched",
			items=[_make_item()],
		)
		doc.save = MagicMock()
		settings = _make_settings()

		attempt_auto_draft(doc, settings)

		mock_frappe.db.set_value.assert_called()
		call_args = mock_frappe.db.set_value.call_args
		assert "supplier" in str(call_args).lower()


class TestInvoiceDateFiscalYearGuard:
	"""Guard against Gemini date misreads (e.g. 2001 for 2026) that would fail
	create_purchase_invoice deep in ERPNext's Fiscal Year validation."""

	def test_empty_date_passes(self, mock_frappe):
		doc = _make_ocr_import(invoice_date=None, company="Test Co")
		ok, reason = _invoice_date_in_fiscal_year(doc)
		assert ok is True
		assert reason == ""

	def test_missing_date_attr_passes(self, mock_frappe):
		# No invoice_date attribute at all (older records) — must not raise
		doc = _make_ocr_import(company="Test Co")
		ok, _ = _invoice_date_in_fiscal_year(doc)
		assert ok is True

	def test_valid_date_passes(self, mock_frappe):
		# erpnext's get_fiscal_year returns (mock) without raising → active FY
		doc = _make_ocr_import(invoice_date="2026-06-11", company="Test Co")
		ok, reason = _invoice_date_in_fiscal_year(doc)
		assert ok is True
		assert reason == ""

	def test_guard_calls_erpnext_not_frappe_utils(self, mock_frappe):
		"""Regression for the prod auto-draft blocker: get_fiscal_year lives in
		erpnext.accounts.utils — the old frappe.utils call AttributeError'd on
		every invocation and the blanket except reported EVERY date as outside
		the fiscal year, blocking all gate-passing auto-drafts."""
		import sys as _sys

		erpnext_utils = _sys.modules["erpnext.accounts.utils"]
		doc = _make_ocr_import(invoice_date="2026-06-18", company="Test Co")

		with patch.object(erpnext_utils, "get_fiscal_year") as mock_gfy:
			ok, _ = _invoice_date_in_fiscal_year(doc)

		assert ok is True
		mock_gfy.assert_called_once_with("2026-06-18", company="Test Co", verbose=0)

	def test_missing_erpnext_fails_open(self, mock_frappe):
		"""No ERPNext importable → guard passes (True) rather than fake-rejecting
		— a missing module must never masquerade as a fiscal-year problem."""
		import sys as _sys

		doc = _make_ocr_import(invoice_date="2026-06-18", company="Test Co")
		with patch.dict(_sys.modules, {"erpnext.accounts.utils": None}):
			ok, reason = _invoice_date_in_fiscal_year(doc)
		assert ok is True
		assert reason == ""

	def test_bad_date_fails(self, mock_frappe):
		import sys as _sys

		erpnext_utils = _sys.modules["erpnext.accounts.utils"]
		doc = _make_ocr_import(invoice_date="2001-06-26", company="Test Co")
		with patch.object(
			erpnext_utils,
			"get_fiscal_year",
			side_effect=Exception("Date 2001-06-26 is not in any active Fiscal Year"),
		):
			ok, reason = _invoice_date_in_fiscal_year(doc)
		assert ok is False
		assert "2001-06-26" in reason

	def test_attempt_skips_on_bad_date(self, mock_frappe):
		"""Mirrors prod OCR-IMP-00991: high-confidence + Matched, but a misread date.
		Auto-draft must skip cleanly (no create, reason persisted), not crash."""
		import sys as _sys

		doc = _make_ocr_import(
			name="OCR-IMP-991",
			status="Matched",
			document_type="",
			invoice_date="2001-06-26",
			company="Test Co",
			purchase_invoice=None,
			purchase_receipt=None,
			journal_entry=None,
			items=[_make_item()],
		)
		doc.create_purchase_invoice = MagicMock()
		doc.save = MagicMock()
		settings = _make_settings()

		erpnext_utils = _sys.modules["erpnext.accounts.utils"]
		with patch.object(
			erpnext_utils,
			"get_fiscal_year",
			side_effect=Exception("not in any active Fiscal Year"),
		):
			result = attempt_auto_draft(doc, settings)

		assert result is False
		doc.create_purchase_invoice.assert_not_called()
		mock_frappe.db.set_value.assert_called()
		assert "Fiscal Year" in str(mock_frappe.db.set_value.call_args)
