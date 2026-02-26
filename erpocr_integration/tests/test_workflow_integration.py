"""End-to-end workflow integration tests.

Tests the wiring between api.py endpoints and ocr_import.py create methods,
verifying guard behavior across document types and the full PO→PR→PI chain.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from erpocr_integration.api import (
	get_open_purchase_orders,
	get_purchase_receipts_for_po,
	match_po_items,
	match_pr_items,
	purchase_receipt_link_query,
)
from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import OCRImport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ocr_import(**overrides):
	doc = OCRImport.__new__(OCRImport)
	doc.name = "OCR-IMP-00001"
	doc.document_type = ""
	doc.supplier = "Test Supplier"
	doc.supplier_name_ocr = "Test Supplier OCR"
	doc.company = "Test Company"
	doc.currency = "ZAR"
	doc.invoice_number = "INV-001"
	doc.invoice_date = "2025-01-15"
	doc.due_date = "2025-02-15"
	doc.subtotal = 1000.00
	doc.tax_amount = 0
	doc.total_amount = 1000.00
	doc.tax_template = None
	doc.credit_account = ""
	doc.purchase_invoice = None
	doc.purchase_receipt = None
	doc.journal_entry = None
	doc.purchase_order = None
	doc.purchase_receipt_link = None
	doc.drive_link = None
	doc.drive_folder_path = None
	doc.status = "Needs Review"
	doc.items = []
	doc.save = MagicMock()
	for key, value in overrides.items():
		setattr(doc, key, value)
	return doc


def _make_item(**overrides):
	defaults = dict(
		description_ocr="Test Item",
		item_code="ITEM-001",
		item_name="Test Item",
		qty=1,
		rate=500.00,
		amount=500.00,
		expense_account="5000 - COGS - TC",
		cost_center="Main - TC",
		match_status="Auto Matched",
		purchase_order_item=None,
		po_qty=0,
		po_rate=0,
		pr_detail=None,
		idx=1,
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


class _MockSettings(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


def _sample_settings():
	return _MockSettings(
		default_company="Test Company",
		default_warehouse="Stores - TC",
		default_expense_account="5000 - COGS - TC",
		default_cost_center="Main - TC",
		default_tax_template="SA VAT 15%",
		non_vat_tax_template="Non-VAT",
		default_item=None,
		default_credit_account="2100 - AP - TC",
		matching_threshold=80,
	)


def _db_get_value_no_existing(doctype, name, fields=None, **kwargs):
	if doctype == "OCR Import":
		return SimpleNamespace(purchase_invoice=None, purchase_receipt=None, journal_entry=None)
	if doctype == "Account":
		if isinstance(fields, str) and fields == "account_type":
			return None
		if isinstance(fields, (list, tuple)) and "account_type" in fields:
			return None
		return SimpleNamespace(company="Test Company", is_group=0, disabled=0)
	if doctype == "Item":
		return 1  # is_stock_item
	return None


# ---------------------------------------------------------------------------
# Guard cross-flow tests
# ---------------------------------------------------------------------------


class TestGuardCrossFlow:
	"""Creating one document type should block creating another on the same import."""

	def test_create_je_then_attempt_pi_throws(self, mock_frappe):
		"""After JE is created, attempting PI creation should throw."""
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - AP - TC",
			items=[_make_item()],
		)
		settings = _sample_settings()
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = settings
		created_je = MagicMock()
		created_je.name = "JE-00001"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

		# Step 1: Create JE successfully
		doc.create_journal_entry()
		assert doc.journal_entry == "JE-00001"

		# Step 2: Now try PI — row-lock should find existing JE
		doc.document_type = "Purchase Invoice"

		def db_get_value_with_je(doctype, name, fields=None, **kwargs):
			if doctype == "OCR Import":
				return SimpleNamespace(purchase_invoice=None, purchase_receipt=None, journal_entry="JE-00001")
			return _db_get_value_no_existing(doctype, name, fields, **kwargs)

		mock_frappe.db.get_value.side_effect = db_get_value_with_je

		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_create_pi_then_attempt_je_throws(self, mock_frappe):
		"""After PI is created, attempting JE creation should throw."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			items=[_make_item()],
		)
		settings = _sample_settings()
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = settings
		created_pi = MagicMock()
		created_pi.name = "PI-00001"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()

		# Step 1: Create PI successfully
		doc.create_purchase_invoice()
		assert doc.purchase_invoice == "PI-00001"

		# Step 2: Now try JE — row-lock should find existing PI
		doc.document_type = "Journal Entry"
		doc.credit_account = "2100 - AP - TC"

		def db_get_value_with_pi(doctype, name, fields=None, **kwargs):
			if doctype == "OCR Import":
				return SimpleNamespace(purchase_invoice="PI-00001", purchase_receipt=None, journal_entry=None)
			return _db_get_value_no_existing(doctype, name, fields, **kwargs)

		mock_frappe.db.get_value.side_effect = db_get_value_with_pi

		with pytest.raises(Exception):
			doc.create_journal_entry()


# ---------------------------------------------------------------------------
# Status guard tests
# ---------------------------------------------------------------------------


class TestStatusGuards:
	"""Server-side status guards prevent document creation from invalid states."""

	@pytest.mark.parametrize("bad_status", ["Pending", "Error", "Completed"])
	def test_pi_rejects_invalid_status(self, mock_frappe, bad_status):
		doc = _make_ocr_import(status=bad_status, document_type="Purchase Invoice")
		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_pi_allows_matched(self, mock_frappe):
		doc = _make_ocr_import(status="Matched", document_type="Purchase Invoice", items=[_make_item()])
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = _sample_settings()
		pi = MagicMock()
		pi.name = "PI-TEST"
		mock_frappe.get_doc.return_value = pi
		mock_frappe.msgprint = MagicMock()
		doc.create_purchase_invoice()
		assert doc.purchase_invoice == "PI-TEST"

	def test_pi_allows_needs_review(self, mock_frappe):
		doc = _make_ocr_import(status="Needs Review", document_type="Purchase Invoice", items=[_make_item()])
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = _sample_settings()
		pi = MagicMock()
		pi.name = "PI-TEST"
		mock_frappe.get_doc.return_value = pi
		mock_frappe.msgprint = MagicMock()
		doc.create_purchase_invoice()
		assert doc.purchase_invoice == "PI-TEST"

	@pytest.mark.parametrize("bad_status", ["Pending", "Needs Review", "Error", "Completed"])
	def test_pr_rejects_invalid_status(self, mock_frappe, bad_status):
		doc = _make_ocr_import(status=bad_status, document_type="Purchase Receipt")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()

	def test_pr_allows_matched(self, mock_frappe):
		doc = _make_ocr_import(status="Matched", document_type="Purchase Receipt", items=[_make_item()])
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = _sample_settings()
		pr = MagicMock()
		pr.name = "PR-TEST"
		mock_frappe.get_doc.return_value = pr
		mock_frappe.msgprint = MagicMock()
		doc.create_purchase_receipt()
		assert doc.purchase_receipt == "PR-TEST"

	@pytest.mark.parametrize("bad_status", ["Pending", "Error", "Completed"])
	def test_je_rejects_invalid_status(self, mock_frappe, bad_status):
		doc = _make_ocr_import(
			status=bad_status, document_type="Journal Entry", credit_account="2100 - AP - TC"
		)
		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_allows_needs_review(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			document_type="Journal Entry",
			credit_account="2100 - AP - TC",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_no_existing
		mock_frappe.get_cached_doc.return_value = _sample_settings()
		je = MagicMock()
		je.name = "JE-TEST"
		mock_frappe.get_doc.return_value = je
		mock_frappe.msgprint = MagicMock()
		doc.create_journal_entry()
		assert doc.journal_entry == "JE-TEST"


# ---------------------------------------------------------------------------
# PO matching API tests
# ---------------------------------------------------------------------------


class TestPurchaseOrderMatching:
	def test_get_open_purchase_orders_permission_check(self, mock_frappe):
		mock_frappe.has_permission.return_value = False
		with pytest.raises(Exception):
			get_open_purchase_orders("Test Supplier", "Test Company")

	def test_get_open_purchase_orders_calls_get_list(self, mock_frappe):
		mock_frappe.has_permission.return_value = True
		mock_frappe.get_list.return_value = [
			{"name": "PO-00001", "transaction_date": "2025-01-01", "grand_total": 5000, "status": "To Bill"}
		]

		result = get_open_purchase_orders("Test Supplier", "Test Company")

		assert len(result) == 1
		assert result[0]["name"] == "PO-00001"
		# Verify correct filters (get_list respects user permissions)
		call_kwargs = mock_frappe.get_list.call_args
		filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
		assert filters["supplier"] == "Test Supplier"
		assert filters["company"] == "Test Company"
		assert filters["docstatus"] == 1

	def test_match_po_items_supplier_mismatch(self, mock_frappe):
		ocr_doc = MagicMock()
		ocr_doc.supplier = "Supplier A"
		ocr_doc.company = "Test Company"
		ocr_doc.items = []

		po_doc = MagicMock()
		po_doc.supplier = "Supplier B"
		po_doc.company = "Test Company"
		po_doc.items = []

		mock_frappe.get_doc.side_effect = lambda dt, name: ocr_doc if dt == "OCR Import" else po_doc

		with pytest.raises(Exception):
			match_po_items("OCR-IMP-00001", "PO-00001")

	def test_match_po_items_company_mismatch(self, mock_frappe):
		ocr_doc = MagicMock()
		ocr_doc.supplier = "Test Supplier"
		ocr_doc.company = "Company A"
		ocr_doc.items = []

		po_doc = MagicMock()
		po_doc.supplier = "Test Supplier"
		po_doc.company = "Company B"
		po_doc.items = []

		mock_frappe.get_doc.side_effect = lambda dt, name: ocr_doc if dt == "OCR Import" else po_doc

		with pytest.raises(Exception):
			match_po_items("OCR-IMP-00001", "PO-00001")

	def test_match_po_items_correct_matching(self, mock_frappe):
		"""Items matched by item_code in FIFO order."""
		ocr_item_1 = SimpleNamespace(
			idx=1, item_code="ITEM-A", item_name="Item A", description_ocr="Item A desc", qty=5, rate=100
		)
		ocr_item_2 = SimpleNamespace(
			idx=2, item_code="ITEM-B", item_name="Item B", description_ocr="Item B desc", qty=2, rate=200
		)
		ocr_item_3 = SimpleNamespace(
			idx=3, item_code=None, item_name=None, description_ocr="Unknown", qty=1, rate=50
		)

		ocr_doc = MagicMock()
		ocr_doc.supplier = "Test Supplier"
		ocr_doc.company = "Test Company"
		ocr_doc.items = [ocr_item_1, ocr_item_2, ocr_item_3]

		po_item_1 = MagicMock(name="poi-1", item_code="ITEM-A", item_name="Item A", qty=10, rate=95)
		po_item_2 = MagicMock(name="poi-2", item_code="ITEM-B", item_name="Item B", qty=3, rate=190)
		po_item_3 = MagicMock(name="poi-3", item_code="ITEM-C", item_name="Item C", qty=1, rate=300)

		po_doc = MagicMock()
		po_doc.supplier = "Test Supplier"
		po_doc.company = "Test Company"
		po_doc.items = [po_item_1, po_item_2, po_item_3]

		mock_frappe.get_doc.side_effect = lambda dt, name: ocr_doc if dt == "OCR Import" else po_doc
		# Mock get_purchase_receipts_for_po (called internally)
		mock_frappe.db.sql.return_value = []

		result = match_po_items("OCR-IMP-00001", "PO-00001")

		matches = result["matches"]
		assert len(matches) == 3

		# Item A matched
		assert matches[0]["match"] is not None
		assert matches[0]["match"]["po_item_code"] == "ITEM-A"

		# Item B matched
		assert matches[1]["match"] is not None
		assert matches[1]["match"]["po_item_code"] == "ITEM-B"

		# Unknown item not matched (no item_code)
		assert matches[2]["match"] is None

		# Unmatched PO item (ITEM-C)
		assert len(result["unmatched_po"]) == 1
		assert result["unmatched_po"][0]["item_code"] == "ITEM-C"


# ---------------------------------------------------------------------------
# PR matching API tests
# ---------------------------------------------------------------------------


class TestPurchaseReceiptMatching:
	def test_match_pr_items_validates_po_link(self, mock_frappe):
		"""PR must be linked to the selected PO."""
		ocr_doc = MagicMock()
		ocr_doc.purchase_order = "PO-00001"
		ocr_doc.items = []

		pr_item = MagicMock(purchase_order="PO-OTHER")
		pr_doc = MagicMock()
		pr_doc.items = [pr_item]

		mock_frappe.get_doc.side_effect = lambda dt, name: ocr_doc if dt == "OCR Import" else pr_doc

		with pytest.raises(Exception):
			match_pr_items("OCR-IMP-00001", "PR-00001")

	def test_match_pr_items_requires_po(self, mock_frappe):
		"""Must have a PO selected before matching PR items."""
		ocr_doc = MagicMock()
		ocr_doc.purchase_order = None

		mock_frappe.get_doc.side_effect = lambda dt, name: ocr_doc if dt == "OCR Import" else MagicMock()

		with pytest.raises(Exception):
			match_pr_items("OCR-IMP-00001", "PR-00001")

	def test_get_purchase_receipts_for_po_permission_check(self, mock_frappe):
		mock_frappe.has_permission.return_value = False
		with pytest.raises(Exception):
			get_purchase_receipts_for_po("PO-00001")


# ---------------------------------------------------------------------------
# Row-level permission enforcement tests
# ---------------------------------------------------------------------------


class TestRowLevelPermissions:
	"""Verify that match_po_items and match_pr_items enforce per-document
	permission checks, not just doctype-level checks."""

	def test_match_po_items_row_level_po_denied(self, mock_frappe):
		"""User has OCR Import write but NOT read on the specific PO."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			if doctype == "OCR Import":
				return True
			if doctype == "Purchase Order" and doc == "PO-RESTRICTED":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		with pytest.raises(Exception):
			match_po_items("OCR-IMP-00001", "PO-RESTRICTED")

		# Verify has_permission was called with the specific PO name (row-level)
		po_calls = [c for c in mock_frappe.has_permission.call_args_list if c.args[0] == "Purchase Order"]
		assert any(c.args[2] == "PO-RESTRICTED" for c in po_calls if len(c.args) > 2)
		# Should NOT have fetched the document (blocked before get_doc)
		mock_frappe.get_doc.assert_not_called()

	def test_match_po_items_row_level_ocr_denied(self, mock_frappe):
		"""User does NOT have write on the specific OCR Import."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			if doctype == "OCR Import" and doc == "OCR-IMP-SECRET":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		with pytest.raises(Exception):
			match_po_items("OCR-IMP-SECRET", "PO-00001")

		mock_frappe.get_doc.assert_not_called()

	def test_match_pr_items_row_level_pr_denied(self, mock_frappe):
		"""User has OCR Import write but NOT read on the specific PR."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			if doctype == "OCR Import":
				return True
			if doctype == "Purchase Receipt" and doc == "PR-RESTRICTED":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		with pytest.raises(Exception):
			match_pr_items("OCR-IMP-00001", "PR-RESTRICTED")

		# Verify has_permission was called with the specific PR name (row-level)
		pr_calls = [c for c in mock_frappe.has_permission.call_args_list if c.args[0] == "Purchase Receipt"]
		assert any(c.args[2] == "PR-RESTRICTED" for c in pr_calls if len(c.args) > 2)
		mock_frappe.get_doc.assert_not_called()


# ---------------------------------------------------------------------------
# purchase_receipt_link_query permission tests
# ---------------------------------------------------------------------------


class TestPurchaseReceiptLinkQuery:
	"""Verify that the Link query for purchase_receipt_link enforces
	doctype-level and row-level permission checks, and scopes to PO."""

	def test_returns_empty_when_no_pr_read_permission(self, mock_frappe):
		"""User without Purchase Receipt read → empty result."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			if doctype == "Purchase Receipt":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		result = purchase_receipt_link_query(
			"Purchase Receipt", "", "name", 0, 20, {"purchase_order": "PO-00001"}
		)
		assert result == []

	def test_returns_empty_when_no_po_read_permission(self, mock_frappe):
		"""User without Purchase Order read → empty result."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			if doctype == "Purchase Order":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		result = purchase_receipt_link_query(
			"Purchase Receipt", "", "name", 0, 20, {"purchase_order": "PO-00001"}
		)
		assert result == []

	def test_returns_empty_when_no_purchase_order_in_filters(self, mock_frappe):
		"""No PO in filters → empty result (prevents listing all PRs)."""
		mock_frappe.has_permission.return_value = True

		result = purchase_receipt_link_query("Purchase Receipt", "", "name", 0, 20, {})
		assert result == []

	def test_returns_empty_when_filters_is_none(self, mock_frappe):
		"""Null filters → empty result."""
		mock_frappe.has_permission.return_value = True

		result = purchase_receipt_link_query("Purchase Receipt", "", "name", 0, 20, None)
		assert result == []

	def test_returns_empty_when_row_level_po_denied(self, mock_frappe):
		"""User has PO read generally but NOT on the specific PO → empty."""

		def perm_side_effect(doctype, ptype="read", doc=None):
			# First two calls: doctype-level checks (pass)
			# Third call: row-level PO check (fail)
			if doctype == "Purchase Order" and doc == "PO-RESTRICTED":
				return False
			return True

		mock_frappe.has_permission.side_effect = perm_side_effect

		result = purchase_receipt_link_query(
			"Purchase Receipt", "", "name", 0, 20, {"purchase_order": "PO-RESTRICTED"}
		)
		assert result == []
		# Should NOT have queried DB (blocked before SQL)
		mock_frappe.db.sql.assert_not_called()

	def test_returns_empty_when_po_not_found(self, mock_frappe):
		"""PO doesn't exist (get_value returns None) → empty result."""
		mock_frappe.has_permission.return_value = True
		mock_frappe.db.get_value.return_value = None

		result = purchase_receipt_link_query(
			"Purchase Receipt", "", "name", 0, 20, {"purchase_order": "PO-NONEXISTENT"}
		)
		assert result == []

	def test_filters_by_row_level_pr_permission(self, mock_frappe):
		"""SQL returns 2 PRs but user only has read on one → only one returned."""
		mock_frappe.has_permission.side_effect = lambda dt, ptype="read", doc=None: (
			False if dt == "Purchase Receipt" and doc == "PR-RESTRICTED" else True
		)
		mock_frappe.db.get_value.return_value = "Test Company"
		mock_frappe.db.sql.return_value = [
			SimpleNamespace(name="PR-00001", posting_date="2025-01-10", status="Completed"),
			SimpleNamespace(name="PR-RESTRICTED", posting_date="2025-01-12", status="Completed"),
		]

		result = purchase_receipt_link_query(
			"Purchase Receipt", "", "name", 0, 20, {"purchase_order": "PO-00001"}
		)

		assert len(result) == 1
		assert result[0][0] == "PR-00001"
