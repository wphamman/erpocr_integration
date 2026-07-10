"""Tests for OCR Import document creation methods and guards."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
	OCRImport,
	_build_taxes_from_template,
	_detect_tax_inclusive_rates,
	_extract_service_pattern,
	_resolve_ocr_description,
)

# ---------------------------------------------------------------------------
# _resolve_ocr_description
# ---------------------------------------------------------------------------


class TestResolveOcrDescription:
	def test_prefers_description_ocr(self):
		item = SimpleNamespace(
			description_ocr="Widget for Project X", item_name="WIDG-01", item_code="WIDG-01"
		)
		assert _resolve_ocr_description(item) == "Widget for Project X"

	def test_falls_back_to_user_edited_item_name(self):
		# description_ocr empty, user edited item_name to something meaningful
		item = SimpleNamespace(description_ocr="", item_name="Widget Assembly Unit 2", item_code="WIDG-01")
		assert _resolve_ocr_description(item) == "Widget Assembly Unit 2"

	def test_skips_when_item_name_is_raw_product_code(self):
		# description_ocr empty and item_name == item_code (unchanged product code)
		item = SimpleNamespace(description_ocr="", item_name="WIDG-01", item_code="WIDG-01")
		assert _resolve_ocr_description(item) == ""

	def test_empty_when_both_missing(self):
		item = SimpleNamespace(description_ocr="", item_name="", item_code="WIDG-01")
		assert _resolve_ocr_description(item) == ""

	def test_strips_whitespace(self):
		item = SimpleNamespace(description_ocr="  Widget  ", item_name="", item_code="")
		assert _resolve_ocr_description(item) == "Widget"

	def test_tolerates_none_fields(self):
		item = SimpleNamespace(description_ocr=None, item_name=None, item_code=None)
		assert _resolve_ocr_description(item) == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ocr_import(**overrides):
	"""Create an OCRImport instance with sensible defaults for testing."""
	doc = OCRImport.__new__(OCRImport)
	doc.name = "OCR-IMP-00001"
	doc.document_type = ""
	doc.supplier = "Test Supplier"
	doc.supplier_name_ocr = "Test Supplier OCR"
	doc.fleet_vehicle = ""
	doc.company = "Test Company"
	doc.currency = "ZAR"
	doc.invoice_number = "INV-001"
	doc.invoice_date = "2025-01-15"
	doc.due_date = "2025-02-15"
	doc.subtotal = 1000.00
	doc.tax_amount = 0
	doc.total_amount = 1000.00
	doc.tax_template = None
	doc.cost_center = ""
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
	"""Create a mock OCR Import Item row."""
	defaults = dict(
		description_ocr="Test Item",
		product_code="",
		item_code="ITEM-001",
		item_name="Test Item",
		qty=1,
		rate=500.00,
		amount=500.00,
		expense_account="5000 - Cost of Goods Sold - TC",
		cost_center="Main - TC",
		match_status="Auto Matched",
		purchase_order_item=None,
		po_qty=0,
		po_rate=0,
		pr_detail=None,
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


def _setup_frappe_for_create(mock_frappe, sample_settings, created_doc_name="JE-00001"):
	"""Configure frappe mock for a successful create_* call."""
	# Row-lock returns no existing documents
	mock_frappe.db.get_value.side_effect = _db_get_value_handler()
	mock_frappe.get_cached_doc.return_value = sample_settings
	# Created document mock
	created_doc = MagicMock()
	created_doc.name = created_doc_name
	created_doc.add_comment = MagicMock()
	mock_frappe.get_doc.return_value = created_doc
	mock_frappe.msgprint = MagicMock()
	return created_doc


def _db_get_value_handler(
	existing_pi=None,
	existing_pr=None,
	existing_je=None,
	account_company="Test Company",
	account_is_group=0,
	account_disabled=0,
	account_type=None,
	item_is_stock=0,
):
	"""Return a side_effect function for frappe.db.get_value that handles different doctypes."""

	def handler(doctype, name, fields=None, **kwargs):
		if doctype == "OCR Import":
			return SimpleNamespace(
				purchase_invoice=existing_pi,
				purchase_receipt=existing_pr,
				journal_entry=existing_je,
			)
		if doctype == "Account":
			if fields and "account_type" in (fields if isinstance(fields, (list, tuple)) else [fields]):
				return account_type
			return SimpleNamespace(
				company=account_company,
				is_group=account_is_group,
				disabled=account_disabled,
			)
		if doctype == "Item":
			return item_is_stock
		return None

	return handler


# ---------------------------------------------------------------------------
# Document type enforcement
# ---------------------------------------------------------------------------


class TestDocumentTypeEnforcement:
	def test_create_pi_requires_purchase_invoice_type(self, mock_frappe):
		doc = _make_ocr_import(document_type="Journal Entry")
		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_create_pr_requires_purchase_receipt_type(self, mock_frappe):
		doc = _make_ocr_import(document_type="Purchase Invoice")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()

	def test_create_je_requires_journal_entry_type(self, mock_frappe):
		doc = _make_ocr_import(document_type="Purchase Invoice")
		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_create_pi_rejects_blank_type(self, mock_frappe):
		doc = _make_ocr_import(document_type="")
		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_create_je_rejects_blank_type(self, mock_frappe):
		doc = _make_ocr_import(document_type="")
		with pytest.raises(Exception):
			doc.create_journal_entry()


# ---------------------------------------------------------------------------
# Cross-document duplicate lock
# ---------------------------------------------------------------------------


class TestCrossDocumentLock:
	def test_create_pi_blocks_when_je_exists(self, mock_frappe):
		doc = _make_ocr_import(document_type="Purchase Invoice")
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(existing_je="JE-00001")
		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_create_je_blocks_when_pi_exists(self, mock_frappe):
		doc = _make_ocr_import(document_type="Journal Entry")
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(existing_pi="PI-00001")
		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_create_je_blocks_when_pr_exists(self, mock_frappe):
		doc = _make_ocr_import(document_type="Journal Entry")
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(existing_pr="PR-00001")
		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_create_pr_blocks_when_je_exists(self, mock_frappe):
		doc = _make_ocr_import(document_type="Purchase Receipt")
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(existing_je="JE-00001")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()

	def test_create_pr_blocks_when_pi_exists(self, mock_frappe):
		doc = _make_ocr_import(document_type="Purchase Receipt")
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(existing_pi="PI-00001")
		with pytest.raises(Exception):
			doc.create_purchase_receipt()


# ---------------------------------------------------------------------------
# Journal Entry creation
# ---------------------------------------------------------------------------


class TestCreateJournalEntry:
	def test_je_created_successfully(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item(amount=500), _make_item(description_ocr="Item 2", amount=500)],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "JE-00001")

		result = doc.create_journal_entry()

		assert result == "JE-00001"
		assert doc.journal_entry == "JE-00001"
		assert doc.status == "Draft Created"
		doc.save.assert_called_once()

	def test_je_get_doc_called_with_correct_structure(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item(amount=800.50)],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings)

		doc.create_journal_entry()

		# Verify frappe.get_doc was called
		je_dict = mock_frappe.get_doc.call_args[0][0]
		assert je_dict["doctype"] == "Journal Entry"
		assert je_dict["company"] == "Test Company"
		assert je_dict["posting_date"] == "2025-01-15"
		assert je_dict["cheque_no"] == "INV-001"

		# Verify accounts structure: at least 1 debit + 1 credit line
		accounts = je_dict["accounts"]
		assert len(accounts) >= 2

		# Last line should be credit
		credit_line = accounts[-1]
		assert credit_line["credit_in_account_currency"] > 0
		assert credit_line["debit_in_account_currency"] == 0

	def test_je_balanced_debits_credits(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			items=[
				_make_item(amount=300.50),
				_make_item(description_ocr="Item 2", amount=199.50),
			],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings)

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		accounts = je_dict["accounts"]
		total_debit = sum(a["debit_in_account_currency"] for a in accounts)
		total_credit = sum(a["credit_in_account_currency"] for a in accounts)
		assert total_debit == total_credit

	def test_je_with_tax_adds_tax_line(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			tax_template="SA VAT 15%",
			tax_amount=150.00,
			items=[_make_item(amount=1000)],
		)
		# Mock tax template
		tax_template = MagicMock()
		tax_template.taxes = [MagicMock(account_head="2200 - VAT Input - TC")]

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return tax_template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_je = MagicMock()
		created_je.name = "JE-00001"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		accounts = je_dict["accounts"]
		# Should have: 1 expense debit + 1 tax debit + 1 credit = 3 lines
		assert len(accounts) == 3
		# Total debits == total credits
		total_debit = sum(a["debit_in_account_currency"] for a in accounts)
		total_credit = sum(a["credit_in_account_currency"] for a in accounts)
		assert total_debit == total_credit
		# Tax debit line amount
		tax_line = accounts[1]
		assert tax_line["debit_in_account_currency"] == 150.00

	def test_je_with_tax_inclusive_rates_no_double_count(self, mock_frappe, sample_settings):
		"""When item amounts include tax, JE should not double-count tax.

		Example: 2 items at R575 each (incl 15% VAT) = R1150 total, R150 VAT.
		Correct JE: DR Expense R500 + R500, DR VAT R150, CR Bank R1150.
		Bug (before fix): DR Expense R575 + R575, DR VAT R150, CR Bank R1300.
		"""
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			tax_template="SA VAT 15%",
			tax_amount=150.00,
			subtotal=1000.00,
			total_amount=1150.00,
			# Item amounts match total (inclusive), not subtotal
			items=[
				_make_item(amount=575.00, rate=575.00, qty=1),
				_make_item(amount=575.00, rate=575.00, qty=1, item_code="ITEM-002", description_ocr="Item 2"),
			],
		)
		tax_template = MagicMock()
		tax_template.taxes = [MagicMock(account_head="2200 - VAT Input - TC")]

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return tax_template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_je = MagicMock()
		created_je.name = "JE-00002"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		accounts = je_dict["accounts"]
		# 2 expense debits + 1 tax debit + 1 credit = 4 lines
		assert len(accounts) == 4
		# Expense lines should be ~500 each (575 - 75 tax share), not 575
		expense_total = accounts[0]["debit_in_account_currency"] + accounts[1]["debit_in_account_currency"]
		assert expense_total == 1000.00  # subtotal, not total_amount
		# Tax line
		assert accounts[2]["debit_in_account_currency"] == 150.00
		# Credit line should equal total_amount (1150), not 1300
		assert accounts[3]["credit_in_account_currency"] == 1150.00
		# Total debits == total credits
		total_debit = sum(a["debit_in_account_currency"] for a in accounts)
		total_credit = sum(a["credit_in_account_currency"] for a in accounts)
		assert total_debit == total_credit

	def test_je_requires_expense_accounts(self, mock_frappe, sample_settings):
		sample_settings.default_expense_account = None
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item(expense_account=None)],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_requires_credit_account(self, mock_frappe, sample_settings):
		sample_settings.default_credit_account = None
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_validates_account_company(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - WrongCo",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(account_company="Wrong Company")
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_rejects_group_account(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2000 - Liabilities - TC",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(account_is_group=1)
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_rejects_disabled_account(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Old Account - TC",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(account_disabled=1)
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_requires_supplier(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			supplier=None,
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item()],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		mock_frappe.get_cached_doc.return_value = sample_settings

		with pytest.raises(Exception):
			doc.create_journal_entry()

	def test_je_party_fields_on_payable_account(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item(amount=1000)],
		)

		# Configure account_type lookup to return "Payable" for credit account
		def handler(doctype, name, fields=None, **kwargs):
			if doctype == "OCR Import":
				return SimpleNamespace(purchase_invoice=None, purchase_receipt=None, journal_entry=None)
			if doctype == "Account":
				if isinstance(fields, str) and fields == "account_type":
					return "Payable"
				if isinstance(fields, (list, tuple)) and "account_type" in fields:
					return "Payable"
				return SimpleNamespace(company="Test Company", is_group=0, disabled=0)
			return None

		mock_frappe.db.get_value.side_effect = handler
		mock_frappe.get_cached_doc.return_value = sample_settings
		created_je = MagicMock()
		created_je.name = "JE-00001"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		credit_line = je_dict["accounts"][-1]
		assert credit_line["party_type"] == "Supplier"
		assert credit_line["party"] == "Test Supplier"


# ---------------------------------------------------------------------------
# Purchase Invoice with PO/PR refs
# ---------------------------------------------------------------------------


class TestCreatePurchaseInvoiceWithPORefs:
	def test_pi_with_po_refs(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			items=[_make_item(purchase_order_item="po-item-row-1")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert pi_item["purchase_order"] == "PO-00001"
		assert pi_item["po_detail"] == "po-item-row-1"

	def test_pi_with_po_and_pr_refs(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			purchase_receipt_link="PR-00001",
			items=[_make_item(purchase_order_item="po-item-row-1", pr_detail="pr-item-row-1")],
		)
		# Mock db.exists for PR validation
		mock_frappe.db.exists.return_value = True
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert pi_item["purchase_order"] == "PO-00001"
		assert pi_item["po_detail"] == "po-item-row-1"
		assert pi_item["purchase_receipt"] == "PR-00001"
		assert pi_item["pr_detail"] == "pr-item-row-1"

	def test_pi_validates_pr_belongs_to_po(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			purchase_receipt_link="PR-WRONG",
			items=[_make_item(purchase_order_item="po-item-row-1")],
		)
		# PR does NOT belong to PO
		mock_frappe.db.exists.return_value = False
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_pi_rejects_pr_without_po(self, mock_frappe, sample_settings):
		"""PR set but PO blank should throw — prevents arbitrary PR linking via API."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order=None,
			purchase_receipt_link="PR-00001",
			items=[_make_item(pr_detail="pr-item-row-1")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		with pytest.raises(Exception):
			doc.create_purchase_invoice()

	def test_pi_without_po_no_refs(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order=None,
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert "purchase_order" not in pi_item
		assert "po_detail" not in pi_item

	def test_pi_auto_matches_po_items_when_refs_missing(self, mock_frappe, sample_settings):
		"""PO set at header but item-level purchase_order_item not set — auto-match by item_code."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			items=[_make_item(purchase_order_item=None)],  # No item-level ref
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")
		# Mock get_all to return PO items for auto-matching
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="po-item-auto-1", item_code="ITEM-001"),
		]

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert pi_item["purchase_order"] == "PO-00001"
		assert pi_item["po_detail"] == "po-item-auto-1"

	def test_pi_auto_matches_pr_items_when_refs_missing(self, mock_frappe, sample_settings):
		"""PO+PR set at header but item-level refs not set — auto-match both."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			purchase_receipt_link="PR-00001",
			items=[_make_item(purchase_order_item=None, pr_detail=None)],
		)
		mock_frappe.db.exists.return_value = True
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")

		def get_all_handler(doctype, **kwargs):
			if doctype == "Purchase Order Item":
				return [SimpleNamespace(name="po-item-auto-1", item_code="ITEM-001")]
			if doctype == "Purchase Receipt Item":
				return [SimpleNamespace(name="pr-item-auto-1", item_code="ITEM-001")]
			return []

		mock_frappe.get_all.side_effect = get_all_handler

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert pi_item["purchase_order"] == "PO-00001"
		assert pi_item["po_detail"] == "po-item-auto-1"
		assert pi_item["purchase_receipt"] == "PR-00001"
		assert pi_item["pr_detail"] == "pr-item-auto-1"


# ---------------------------------------------------------------------------
# Purchase Receipt with PO refs
# ---------------------------------------------------------------------------


class TestCreatePurchaseReceiptWithPORefs:
	def test_pr_with_po_refs(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Receipt",
			purchase_order="PO-00001",
			status="Matched",
			items=[_make_item(purchase_order_item="po-item-row-1")],
		)
		# Mock is_stock_item check
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(item_is_stock=1)
		mock_frappe.get_cached_doc.return_value = sample_settings
		created_pr = MagicMock()
		created_pr.name = "PR-00001"
		mock_frappe.get_doc.return_value = created_pr
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_receipt()

		pr_dict = mock_frappe.get_doc.call_args[0][0]
		pr_item = pr_dict["items"][0]
		assert pr_item["purchase_order"] == "PO-00001"
		assert pr_item["purchase_order_item"] == "po-item-row-1"

	def test_pr_auto_matches_po_items_when_refs_missing(self, mock_frappe, sample_settings):
		"""PO set at header but item-level purchase_order_item not set — auto-match by item_code."""
		doc = _make_ocr_import(
			document_type="Purchase Receipt",
			purchase_order="PO-00001",
			status="Matched",
			items=[_make_item(purchase_order_item=None)],
		)
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(item_is_stock=1)
		mock_frappe.get_cached_doc.return_value = sample_settings
		created_pr = MagicMock()
		created_pr.name = "PR-00001"
		mock_frappe.get_doc.return_value = created_pr
		mock_frappe.msgprint = MagicMock()
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="po-item-auto-1", item_code="ITEM-001"),
		]

		doc.create_purchase_receipt()

		pr_dict = mock_frappe.get_doc.call_args[0][0]
		pr_item = pr_dict["items"][0]
		assert pr_item["purchase_order"] == "PO-00001"
		assert pr_item["purchase_order_item"] == "po-item-auto-1"


# ---------------------------------------------------------------------------
# Doc-level cost_center precedence (line → parent → settings default)
# ---------------------------------------------------------------------------


class TestCostCenterPrecedence:
	"""The doc-level cost_center on OCR Import applies to every line that doesn't
	have its own cost_center, falling back to settings.default_cost_center only
	when both the line and the doc are blank.
	"""

	def test_pi_line_cost_center_wins(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			cost_center="Doc CC - TC",
			items=[_make_item(cost_center="Line CC - TC")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-CC-001")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert pi_dict["items"][0]["cost_center"] == "Line CC - TC"

	def test_pi_doc_cost_center_used_when_line_blank(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			cost_center="Doc CC - TC",
			items=[_make_item(cost_center="")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-CC-002")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert pi_dict["items"][0]["cost_center"] == "Doc CC - TC"

	def test_pi_settings_default_used_when_both_blank(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			cost_center="",
			items=[_make_item(cost_center="")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-CC-003")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		# sample_settings.default_cost_center = "Main - TC"
		assert pi_dict["items"][0]["cost_center"] == "Main - TC"

	def test_pr_doc_cost_center_used_when_line_blank(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Receipt",
			status="Matched",
			cost_center="Doc CC - TC",
			items=[_make_item(cost_center="", purchase_order_item=None)],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PR-CC-001")

		doc.create_purchase_receipt()

		pr_dict = mock_frappe.get_doc.call_args[0][0]
		assert pr_dict["items"][0]["cost_center"] == "Doc CC - TC"

	def test_je_doc_cost_center_used_on_debit_tax_and_credit(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Journal Entry",
			cost_center="Doc CC - TC",
			credit_account="2100 - Accounts Payable - TC",
			items=[_make_item(cost_center="", expense_account="5000 - Cost of Goods Sold - TC")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "JE-CC-001")

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		# Every line (debit + credit) should land on the doc-level cost_center
		for line in je_dict["accounts"]:
			assert line["cost_center"] == "Doc CC - TC"


# ---------------------------------------------------------------------------
# Fleet vehicle tag flows through to the created Purchase Invoice
# ---------------------------------------------------------------------------


class TestFleetVehicleTag:
	"""The optional fleet_vehicle tag on OCR Import carries through to the
	created PI's custom_fleet_vehicle (a fleet_management-owned field), guarded
	by a runtime has_field check so it's a no-op when that app isn't installed.
	Mirrors ocr_fleet_slip.create_purchase_invoice (see TestCreatePurchaseInvoice
	in test_fleet_controller.py).
	"""

	def test_pi_tags_custom_fleet_vehicle_when_set(self, mock_frappe, sample_settings):
		"""fleet_vehicle set on OCR Import + field present on PI → tag flows through."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			fleet_vehicle="VEH-001",
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-FV-001")
		mock_frappe.get_meta.return_value.has_field.return_value = True

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert pi_dict["custom_fleet_vehicle"] == "VEH-001"
		# any_call (not called_with): the back-link has_field("custom_ocr_import")
		# check runs after the fleet-vehicle one in create_purchase_invoice.
		mock_frappe.get_meta.return_value.has_field.assert_any_call("custom_fleet_vehicle")

	def test_pi_omits_custom_fleet_vehicle_when_blank(self, mock_frappe, sample_settings):
		"""fleet_vehicle blank → key absent on pi_dict (stays NULL, not empty string)."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			fleet_vehicle="",
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-FV-002")
		mock_frappe.get_meta.return_value.has_field.return_value = True

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "custom_fleet_vehicle" not in pi_dict

	def test_pi_omits_custom_fleet_vehicle_when_field_missing(self, mock_frappe, sample_settings):
		"""fleet_management not installed (no custom_fleet_vehicle field on PI) →
		key absent on pi_dict even with a tag set, so PI insert doesn't fail on an
		unknown field."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			fleet_vehicle="VEH-001",
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-FV-003")
		mock_frappe.get_meta.return_value.has_field.return_value = False

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "custom_fleet_vehicle" not in pi_dict

	def test_pi_omits_custom_fleet_vehicle_when_whitespace_only(self, mock_frappe, sample_settings):
		"""A whitespace-only tag (possible via API on the Link field) is treated as
		untagged — never written as a malformed link value."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			fleet_vehicle="   ",
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-FV-004")
		mock_frappe.get_meta.return_value.has_field.return_value = True

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "custom_fleet_vehicle" not in pi_dict

	def test_pi_handles_missing_fleet_vehicle_attr(self, mock_frappe, sample_settings):
		"""v1.1.6: when fleet_management isn't installed, the `fleet_vehicle` Custom
		Field isn't created on OCR Import, so the attribute simply isn't there on a
		freshly-loaded doc. The .get() path must return None gracefully and skip the
		write — never raise AttributeError."""
		doc = _make_ocr_import(document_type="Purchase Invoice", items=[_make_item()])
		# Simulate field-not-installed: remove the attr the factory pre-sets to "".
		del doc.fleet_vehicle
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-FV-005")
		# has_field on PI may still return True if fleet_management is installed but
		# OCR Import's field isn't — that's a weird intermediate state but we still
		# need to behave: no fleet_vehicle to write means custom_fleet_vehicle stays
		# off the pi_dict.
		mock_frappe.get_meta.return_value.has_field.return_value = True

		doc.create_purchase_invoice()  # must not raise AttributeError

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "custom_fleet_vehicle" not in pi_dict


# ---------------------------------------------------------------------------
# Tax template application via _build_taxes_from_template
# ---------------------------------------------------------------------------


class TestTaxTemplateOnPIAndPR:
	def _make_tax_template(self):
		"""Create a mock tax template with one tax row."""
		tax_row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="On Net Total",
			row_id=None,
			account_head="2200 - VAT Input - TC",
			description="VAT 15%",
			rate=15.0,
			cost_center="Main - TC",
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		template = SimpleNamespace(company="Test Company", taxes=[tax_row])
		return template

	def test_pi_applies_tax_template(self, mock_frappe, sample_settings):
		"""PI creation applies tax template via shared helper."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="SA VAT 15%",
			tax_amount=150.00,
			subtotal=1000.00,
			total_amount=1150.00,
			items=[_make_item(rate=1000, qty=1, amount=1000)],
		)
		template = self._make_tax_template()

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_pi = MagicMock()
		created_pi.name = "PI-TAX-001"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert pi_dict["taxes_and_charges"] == "SA VAT 15%"
		assert len(pi_dict["taxes"]) == 1
		assert pi_dict["taxes"][0]["account_head"] == "2200 - VAT Input - TC"
		assert pi_dict["taxes"][0]["rate"] == 15.0

	def test_pr_applies_tax_template(self, mock_frappe, sample_settings):
		"""PR creation applies tax template via shared helper."""
		doc = _make_ocr_import(
			document_type="Purchase Receipt",
			status="Matched",
			tax_template="SA VAT 15%",
			tax_amount=150.00,
			subtotal=1000.00,
			total_amount=1150.00,
			items=[_make_item(rate=1000, qty=1, amount=1000)],
		)
		template = self._make_tax_template()

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler(item_is_stock=1)
		created_pr = MagicMock()
		created_pr.name = "PR-TAX-001"
		mock_frappe.get_doc.return_value = created_pr
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_receipt()

		pr_dict = mock_frappe.get_doc.call_args[0][0]
		assert pr_dict["taxes_and_charges"] == "SA VAT 15%"
		assert len(pr_dict["taxes"]) == 1
		assert pr_dict["taxes"][0]["account_head"] == "2200 - VAT Input - TC"
		assert pr_dict["taxes"][0]["rate"] == 15.0

	def test_pi_no_tax_template_skips(self, mock_frappe, sample_settings):
		"""PI without tax_template has no taxes in dict."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template=None,
			items=[_make_item()],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-NOTAX")

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "taxes_and_charges" not in pi_dict
		assert "taxes" not in pi_dict

	def test_tax_inclusive_rates_sets_flag(self, mock_frappe, sample_settings):
		"""When rates include tax, included_in_print_rate is set to 1."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="SA VAT 15%",
			tax_amount=150.00,
			subtotal=1000.00,
			total_amount=1150.00,
			# Rates match total (inclusive), not subtotal
			items=[_make_item(rate=1150, qty=1, amount=1150)],
		)
		template = self._make_tax_template()

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_pi = MagicMock()
		created_pi.name = "PI-INCL"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert pi_dict["taxes"][0]["included_in_print_rate"] == 1


# ---------------------------------------------------------------------------
# _update_status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
	def test_status_draft_created_when_journal_entry_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			journal_entry="JE-00001",
		)
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_status_draft_created_when_pi_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			purchase_invoice="PI-00001",
		)
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_status_draft_created_when_pr_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			purchase_receipt="PR-00001",
		)
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_status_not_changed_when_already_draft_created(self, mock_frappe):
		doc = _make_ocr_import(status="Draft Created")
		doc._update_status()
		assert doc.status == "Draft Created"

	def test_status_not_changed_when_already_completed(self, mock_frappe):
		doc = _make_ocr_import(status="Completed")
		doc._update_status()
		assert doc.status == "Completed"

	def test_status_not_changed_when_error(self, mock_frappe):
		doc = _make_ocr_import(status="Error")
		doc._update_status()
		assert doc.status == "Error"


# ---------------------------------------------------------------------------
# _detect_tax_inclusive_rates tests
# ---------------------------------------------------------------------------


class TestDetectTaxInclusiveRates:
	"""Tests for the country-agnostic tax inclusion detection heuristic."""

	def test_exclusive_rates_sa_b2b(self):
		"""SA B2B invoice: items sum matches subtotal (excl VAT)."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[
				_make_item(qty=2, rate=300.00),
				_make_item(qty=1, rate=400.00),
			],
		)
		# sum(rate*qty) = 600 + 400 = 1000 = subtotal → exclusive
		assert _detect_tax_inclusive_rates(doc) is False

	def test_inclusive_rates_consumer_receipt(self):
		"""Consumer receipt: items sum matches total (incl VAT)."""
		doc = _make_ocr_import(
			subtotal=869.57,
			tax_amount=130.43,
			total_amount=1000.00,
			items=[
				_make_item(qty=1, rate=600.00),
				_make_item(qty=1, rate=400.00),
			],
		)
		# sum(rate*qty) = 1000 = total → inclusive
		assert _detect_tax_inclusive_rates(doc) is True

	def test_no_tax_returns_false(self):
		"""No tax on invoice → not inclusive."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=0,
			total_amount=1000.00,
			items=[_make_item(qty=1, rate=1000.00)],
		)
		assert _detect_tax_inclusive_rates(doc) is False

	def test_no_subtotal_returns_false(self):
		"""Missing subtotal → can't compare, default to exclusive."""
		doc = _make_ocr_import(
			subtotal=0,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(qty=1, rate=1000.00)],
		)
		assert _detect_tax_inclusive_rates(doc) is False

	def test_real_data_chemicals_exclusive(self):
		"""Real data from Docker: chemical supplier, VAT-exclusive rates."""
		doc = _make_ocr_import(
			subtotal=66762.50,
			tax_amount=10014.39,
			total_amount=76776.89,
			items=[
				_make_item(qty=150, rate=15.35),
				_make_item(qty=250, rate=78.95),
				_make_item(qty=75, rate=90.30),
				_make_item(qty=1000, rate=37.95),
			],
		)
		# sum = 2302.5 + 19737.5 + 6772.5 + 37950 = 66762.5 = subtotal
		assert _detect_tax_inclusive_rates(doc) is False

	def test_real_data_restaurant_exclusive(self):
		"""Real data from Docker: restaurant receipt, rates matched subtotal."""
		doc = _make_ocr_import(
			subtotal=197.00,
			tax_amount=25.70,
			total_amount=220.00,
			items=[
				_make_item(qty=1, rate=105.00),
				_make_item(qty=1, rate=60.00),
				_make_item(qty=1, rate=32.00),
			],
		)
		# sum = 197 = subtotal → exclusive
		assert _detect_tax_inclusive_rates(doc) is False

	def test_eu_vat_inclusive(self):
		"""EU-style VAT-inclusive receipt (e.g., 20% VAT)."""
		doc = _make_ocr_import(
			subtotal=83.33,
			tax_amount=16.67,
			total_amount=100.00,
			items=[_make_item(qty=1, rate=100.00)],
		)
		# sum = 100 = total → inclusive
		assert _detect_tax_inclusive_rates(doc) is True

	def test_no_items_returns_false(self):
		"""No line items → can't determine, default to exclusive."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[],
		)
		assert _detect_tax_inclusive_rates(doc) is False

	def test_zero_rate_items_returns_false(self):
		"""Items with zero rates → can't determine."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(qty=1, rate=0)],
		)
		assert _detect_tax_inclusive_rates(doc) is False

	def test_ambiguous_invoice_defaults_to_exclusive(self):
		"""When rate*qty sum falls between subtotal and total but close to midpoint,
		the ambiguity threshold should kick in and default to exclusive (False).

		Example: subtotal=1000, tax=150, total=1150.
		If rates sum to 1070 → diff_to_subtotal=70, diff_to_total=80.
		Difference between distances = 10. Threshold = 150 * 0.05 = 7.5.
		10 > 7.5 so this is NOT ambiguous → should return False (closer to subtotal).

		But if rates sum to 1074 → diff_to_subtotal=74, diff_to_total=76.
		Difference = 2. 2 < 7.5 so this IS ambiguous → should return False (default).
		"""
		# Ambiguous: rates midway between subtotal and total
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(qty=1, rate=1074.00)],
		)
		# diff_to_subtotal = 74, diff_to_total = 76, |74-76| = 2 < 7.5 → ambiguous
		assert _detect_tax_inclusive_rates(doc) is False

	def test_clear_inclusive_passes_ambiguity_check(self):
		"""Clear inclusive case should still return True despite ambiguity check."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(qty=1, rate=1150.00)],
		)
		# diff_to_subtotal = 150, diff_to_total = 0, |150-0| = 150 >> 7.5
		assert _detect_tax_inclusive_rates(doc) is True

	def test_clear_exclusive_passes_ambiguity_check(self):
		"""Clear exclusive case should still return False."""
		doc = _make_ocr_import(
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(qty=1, rate=1000.00)],
		)
		# diff_to_subtotal = 0, diff_to_total = 150, |0-150| = 150 >> 7.5
		assert _detect_tax_inclusive_rates(doc) is False


# ---------------------------------------------------------------------------
# _extract_service_pattern tests
# ---------------------------------------------------------------------------


class TestExtractServicePattern:
	"""Tests for the service mapping pattern extraction logic."""

	def test_strips_month_name_and_year(self):
		"""'Monthly Subscription Feb 2026' → strips month + year."""
		result = _extract_service_pattern("Monthly Software Subscription Feb 2026")
		assert result == "monthly software subscription"

	def test_strips_full_month_name(self):
		"""Full month names like 'February' are stripped."""
		result = _extract_service_pattern("Afrihost VDSL Line Rental - February 2026")
		assert result == "afrihost vdsl line rental"

	def test_strips_date_format_dd_mm_yyyy(self):
		"""Date in DD/MM/YYYY format is stripped."""
		result = _extract_service_pattern("Delivery 15/01/2026")
		assert result == "delivery"

	def test_strips_date_format_yyyy_mm_dd(self):
		"""Date in YYYY-MM-DD format is stripped."""
		result = _extract_service_pattern("Service charge 2026-01-15")
		assert result == "service charge"

	def test_strips_ordinal_day(self):
		"""Ordinal day numbers (1st, 2nd, 15th) are stripped."""
		result = _extract_service_pattern("Service fee - 1st Jan 2025")
		assert result == "service fee"

	def test_strips_trailing_prepositions(self):
		"""Trailing prepositions left after stripping are cleaned up."""
		result = _extract_service_pattern("Subscription for the month of Jan 2026")
		assert result == "subscription for the month"

	def test_no_dates_unchanged(self):
		"""Descriptions without dates/months pass through (lowered, punctuation normalized)."""
		result = _extract_service_pattern("GREENLEAF CC - CHEMICALS")
		assert result == "greenleaf cc chemicals"

	def test_preserves_product_description(self):
		"""Product descriptions without temporal info are preserved (punctuation normalized)."""
		result = _extract_service_pattern("Sodium Hydroxide 50% Solution 25kg")
		assert result == "sodium hydroxide 50 solution 25kg"

	def test_multiple_date_parts(self):
		"""Multiple date components in one description are all stripped."""
		result = _extract_service_pattern("Invoice period 01/01/2026 to 31/01/2026")
		assert result == "invoice period"

	def test_fallback_on_short_result(self):
		"""Falls back to full description if stripping makes it too short."""
		result = _extract_service_pattern("Feb 2026")
		assert result == "feb 2026"

	def test_empty_string(self):
		"""Empty input returns empty string."""
		result = _extract_service_pattern("")
		assert result == ""

	def test_whitespace_only(self):
		"""Whitespace-only input returns empty string."""
		result = _extract_service_pattern("   ")
		assert result == ""

	def test_strips_dotted_date(self):
		"""European date format DD.MM.YYYY is stripped."""
		result = _extract_service_pattern("Hosting fee 15.01.2026")
		assert result == "hosting fee"

	def test_mixed_case_months(self):
		"""Month name matching is case-insensitive."""
		result = _extract_service_pattern("RENEWAL JANUARY 2025")
		assert result == "renewal"

	def test_month_with_trailing_comma(self):
		"""Month names with trailing punctuation are stripped."""
		result = _extract_service_pattern("Billed December, 2025")
		assert result == "billed"

	def test_real_description_restaurant(self):
		"""Real-world restaurant receipt description."""
		result = _extract_service_pattern("Food and Beverages")
		assert result == "food and beverages"

	def test_real_description_subscription_with_ref(self):
		"""Subscription with date range."""
		result = _extract_service_pattern("Pro Plan - Jan 2026 to Feb 2026")
		assert result == "pro plan"


# ---------------------------------------------------------------------------
# mark_no_action
# ---------------------------------------------------------------------------


class TestMarkNoAction:
	def test_marks_no_action_with_reason(self, mock_frappe):
		doc = _make_ocr_import(status="Needs Review")
		mock_frappe.msgprint = MagicMock()

		doc.mark_no_action("Receipt for OCR-IMP-00025")

		assert doc.status == "No Action"
		assert doc.no_action_reason == "Receipt for OCR-IMP-00025"
		doc.save.assert_called_once()

	def test_marks_no_action_from_matched(self, mock_frappe):
		doc = _make_ocr_import(status="Matched")
		mock_frappe.msgprint = MagicMock()

		doc.mark_no_action("Delivery note — not an invoice")

		assert doc.status == "No Action"

	def test_marks_no_action_from_error(self, mock_frappe):
		doc = _make_ocr_import(status="Error")
		mock_frappe.msgprint = MagicMock()

		doc.mark_no_action("Corrupted file, already processed elsewhere")

		assert doc.status == "No Action"

	def test_blocks_no_action_from_completed(self, mock_frappe):
		doc = _make_ocr_import(status="Completed")

		with pytest.raises(Exception):
			doc.mark_no_action("Some reason")

	def test_blocks_no_action_from_draft_created(self, mock_frappe):
		doc = _make_ocr_import(status="Draft Created")

		with pytest.raises(Exception):
			doc.mark_no_action("Some reason")

	def test_requires_reason(self, mock_frappe):
		doc = _make_ocr_import(status="Needs Review")

		with pytest.raises(Exception):
			doc.mark_no_action("")

	def test_requires_non_whitespace_reason(self, mock_frappe):
		doc = _make_ocr_import(status="Needs Review")

		with pytest.raises(Exception):
			doc.mark_no_action("   ")

	def test_update_status_preserves_no_action(self, mock_frappe):
		"""_update_status should not overwrite No Action status."""
		doc = _make_ocr_import(status="No Action")
		doc.no_action_reason = "Not an invoice"

		doc._update_status()

		assert doc.status == "No Action"


# ---------------------------------------------------------------------------
# Stale PO/PR item-ref clearing at create time
#
# Scenario: user runs Match PO / Match PR (captures po_detail/pr_detail by
# matching OCR item_codes at that moment), then changes item_code on the OCR
# row. Without the stale-ref guard, ERPNext's PI/PR-from-PO/PR sync pulls the
# *old* PR/PO item_code back onto the new PI/PR at insert time, and the user
# has to reselect item_codes on the draft.
# ---------------------------------------------------------------------------


def _stale_ref_db_get_value(po_item_codes=None, pr_item_codes=None, **handler_kwargs):
	"""Return a db.get_value side_effect that also answers Purchase Order Item and
	Purchase Receipt Item item_code lookups from caller-provided dicts."""
	base = _db_get_value_handler(**handler_kwargs)
	po_item_codes = po_item_codes or {}
	pr_item_codes = pr_item_codes or {}

	def handler(doctype, name, fields=None, **kwargs):
		if doctype == "Purchase Order Item":
			return po_item_codes.get(name)
		if doctype == "Purchase Receipt Item":
			return pr_item_codes.get(name)
		return base(doctype, name, fields, **kwargs)

	return handler


class TestStalePORefClearing:
	def test_pi_drops_stale_po_detail_when_item_code_changed(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			items=[_make_item(item_code="NEW-CODE", purchase_order_item="po-item-row-1")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")
		# Saved ref points at a PO item with a DIFFERENT item_code
		mock_frappe.db.get_value.side_effect = _stale_ref_db_get_value(
			po_item_codes={"po-item-row-1": "OLD-CODE"}
		)

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		pi_item = pi_dict["items"][0]
		assert pi_item["item_code"] == "NEW-CODE"
		# Stale po_detail must have been dropped so ERPNext doesn't resync it
		assert "po_detail" not in pi_item
		assert "purchase_order" not in pi_item

	def test_pi_keeps_matching_po_detail(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			items=[_make_item(item_code="MATCH-CODE", purchase_order_item="po-item-row-1")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")
		mock_frappe.db.get_value.side_effect = _stale_ref_db_get_value(
			po_item_codes={"po-item-row-1": "MATCH-CODE"}
		)

		doc.create_purchase_invoice()

		pi_item = mock_frappe.get_doc.call_args[0][0]["items"][0]
		assert pi_item["po_detail"] == "po-item-row-1"
		assert pi_item["purchase_order"] == "PO-00001"

	def test_pi_drops_stale_pr_detail_when_item_code_changed(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			purchase_order="PO-00001",
			purchase_receipt_link="PR-00001",
			items=[
				_make_item(
					item_code="NEW-CODE",
					purchase_order_item="po-item-row-1",
					pr_detail="pr-item-row-1",
				)
			],
		)
		mock_frappe.db.exists.return_value = True  # PR-belongs-to-PO validation
		_setup_frappe_for_create(mock_frappe, sample_settings, "PI-00001")
		# Both PO and PR saved refs point at OLD item_code
		mock_frappe.db.get_value.side_effect = _stale_ref_db_get_value(
			po_item_codes={"po-item-row-1": "OLD-CODE"},
			pr_item_codes={"pr-item-row-1": "OLD-CODE"},
		)

		doc.create_purchase_invoice()

		pi_item = mock_frappe.get_doc.call_args[0][0]["items"][0]
		assert pi_item["item_code"] == "NEW-CODE"
		assert "po_detail" not in pi_item
		assert "pr_detail" not in pi_item

	def test_pr_drops_stale_po_ref_when_item_code_changed(self, mock_frappe, sample_settings):
		doc = _make_ocr_import(
			document_type="Purchase Receipt",
			status="Matched",
			purchase_order="PO-00001",
			items=[_make_item(item_code="NEW-CODE", purchase_order_item="po-item-row-1")],
		)
		_setup_frappe_for_create(mock_frappe, sample_settings, "PR-00001")
		mock_frappe.db.get_value.side_effect = _stale_ref_db_get_value(
			po_item_codes={"po-item-row-1": "OLD-CODE"},
			item_is_stock=1,
		)

		doc.create_purchase_receipt()

		pr_item = mock_frappe.get_doc.call_args[0][0]["items"][0]
		assert pr_item["item_code"] == "NEW-CODE"
		assert "purchase_order_item" not in pr_item
		assert "purchase_order" not in pr_item


# ---------------------------------------------------------------------------
# on_update — alias / mapping / Item Supplier learning
# ---------------------------------------------------------------------------


class TestOnUpdateLearning:
	"""Covers v1.1 changes:
	- skip alias / service mapping / Item Supplier learning when item_code == default_item
	- enqueue Item Supplier learning when product_code + item_code + supplier are all set
	"""

	def _settings(self, mock_frappe, default_item="", **overrides):
		"""Set up OCR Settings cache with the given default_item."""
		settings = SimpleNamespace(default_item=default_item, **overrides)
		settings.get = lambda key, default=None: getattr(settings, key, default)
		mock_frappe.get_cached_doc.return_value = settings
		return settings

	def test_default_item_saves_service_mapping_not_alias(self, mock_frappe):
		"""Catch-all (default_item) line WITH an expense account: the service mapping
		(GL coding) IS learned so the line auto-codes next time — but NO item alias
		(useless: item is always the catch-all) and NO Item Supplier enqueue (would
		point a product code at the catch-all)."""
		self._settings(mock_frappe, default_item="ITEM001")
		doc = _make_ocr_import(
			supplier="Acme",
			items=[
				_make_item(
					item_code="ITEM001",
					match_status="Confirmed",
					expense_account="5000 - X - TC",
					product_code="P-999",
				)
			],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()
		doc._enqueue_item_supplier_learning = MagicMock()

		doc.on_update()

		doc._save_service_mapping.assert_called_once()
		doc._save_item_alias.assert_not_called()
		doc._enqueue_item_supplier_learning.assert_not_called()

	def test_default_item_without_expense_account_learns_nothing(self, mock_frappe):
		"""Catch-all line with NO expense account: nothing worth learning."""
		self._settings(mock_frappe, default_item="ITEM001")
		doc = _make_ocr_import(
			items=[_make_item(item_code="ITEM001", match_status="Confirmed", expense_account="")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()
		doc._enqueue_item_supplier_learning = MagicMock()

		doc.on_update()

		doc._save_item_alias.assert_not_called()
		doc._save_service_mapping.assert_not_called()
		doc._enqueue_item_supplier_learning.assert_not_called()

	def test_saves_alias_when_item_is_real_stock(self, mock_frappe):
		"""Real stock item: alias saved as before."""
		self._settings(mock_frappe, default_item="ITEM001")
		doc = _make_ocr_import(
			items=[_make_item(item_code="000060", match_status="Confirmed", product_code="")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()
		doc._enqueue_item_supplier_learning = MagicMock()

		doc.on_update()

		doc._save_item_alias.assert_called_once()

	def test_enqueues_item_supplier_when_all_signals_present(self, mock_frappe):
		"""Real stock + product_code + supplier → enqueue Item Supplier learning."""
		self._settings(mock_frappe, default_item="ITEM001")
		doc = _make_ocr_import(
			supplier="Acme",
			items=[
				_make_item(
					item_code="000060",
					match_status="Confirmed",
					product_code="ACME-STUD-63",
				)
			],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()

		doc.on_update()

		# enqueue went via frappe.enqueue with the right job_id + payload
		mock_frappe.enqueue.assert_called_once()
		kwargs = mock_frappe.enqueue.call_args.kwargs
		assert kwargs["item_code"] == "000060"
		assert kwargs["supplier"] == "Acme"
		assert kwargs["product_code"] == "ACME-STUD-63"
		assert kwargs["queue"] == "short"
		assert kwargs["deduplicate"] is True
		assert "000060" in kwargs["job_id"]
		assert "Acme" in kwargs["job_id"]
		assert "acme-stud-63" in kwargs["job_id"]  # normalized lower

	def test_skips_enqueue_when_no_product_code(self, mock_frappe):
		"""No product_code on the row → don't enqueue learning."""
		self._settings(mock_frappe, default_item="")
		doc = _make_ocr_import(
			items=[_make_item(item_code="000060", match_status="Confirmed", product_code="")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()

		doc.on_update()

		mock_frappe.enqueue.assert_not_called()

	def test_skips_enqueue_when_no_supplier(self, mock_frappe):
		"""No supplier on parent → don't enqueue learning."""
		self._settings(mock_frappe, default_item="")
		doc = _make_ocr_import(
			supplier="",
			items=[_make_item(item_code="000060", match_status="Confirmed", product_code="ACME-STUD-63")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()

		doc.on_update()

		mock_frappe.enqueue.assert_not_called()

	def test_does_not_enqueue_when_match_not_confirmed(self, mock_frappe):
		"""Suggested / Auto Matched / Unmatched: no learning until user confirms."""
		self._settings(mock_frappe, default_item="")
		doc = _make_ocr_import(
			supplier="Acme",
			items=[_make_item(item_code="000060", match_status="Suggested", product_code="ACME-STUD-63")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()

		doc.on_update()

		mock_frappe.enqueue.assert_not_called()
		doc._save_item_alias.assert_not_called()

	def test_enqueue_failure_does_not_break_save(self, mock_frappe):
		"""Queue glitch must not propagate and break the user's confirm flow."""
		self._settings(mock_frappe, default_item="")
		mock_frappe.enqueue = MagicMock(side_effect=Exception("redis down"))

		doc = _make_ocr_import(
			supplier="Acme",
			items=[_make_item(item_code="000060", match_status="Confirmed", product_code="ACME-STUD-63")],
		)
		doc.has_value_changed = MagicMock(return_value=False)
		doc._save_item_alias = MagicMock()
		doc._save_service_mapping = MagicMock()

		# Should NOT raise
		doc.on_update()
		mock_frappe.log_error.assert_called()


# ---------------------------------------------------------------------------
# Import (Actual) VAT injection — live-review V1 / roadmap C1-1
# ---------------------------------------------------------------------------


class TestImportVATInjection:
	"""Actual-charge templates get the extracted tax_amount injected; percentage
	templates are untouched (ERPNext computes their rows)."""

	def _actual_template(self):
		row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="Actual",
			row_id=None,
			account_head="9500/000 - Vat Control Account - CC",
			description="Customs VAT",
			rate=0.0,
			cost_center=None,
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		return SimpleNamespace(company="Test Company", taxes=[row])

	def test_cargo_compass_ji279503_acceptance(self, mock_frappe, sample_settings):
		"""Acceptance criterion (review C1-#1): reproduce ACC-PINV-2026-00416 —
		net R57,614.30 in service items + ONE Actual tax row of R64,038.90
		against the VAT control account — with no manual correction."""
		items = [
			_make_item(description_ocr="SHIPPING LINE CHARGES", qty=1, rate=9625.00, amount=9625.00),
			_make_item(description_ocr="ROADHAUL - FCL 40", qty=1, rate=18620.00, amount=18620.00),
			_make_item(description_ocr="EMPTY RETURN TO PORT", qty=1, rate=9500.00, amount=9500.00),
			_make_item(description_ocr="FUEL SURCHARGE", qty=1, rate=4551.00, amount=4551.00),
			_make_item(description_ocr="GENSET", qty=1, rate=5790.00, amount=5790.00),
			_make_item(description_ocr="CARGO DUES FCL", qty=1, rate=4052.65, amount=4052.65),
			_make_item(description_ocr="AGENCY", qty=1, rate=1797.83, amount=1797.83),
			_make_item(description_ocr="BAILEE FEE", qty=1, rate=1277.82, amount=1277.82),
			_make_item(description_ocr="CUSTOMS DOCUMENTATION", qty=1, rate=600.00, amount=600.00),
			_make_item(description_ocr="C.T.O. / NAVIS FEE", qty=1, rate=100.00, amount=100.00),
			_make_item(description_ocr="PORT HEALTH RELEASE", qty=1, rate=850.00, amount=850.00),
			_make_item(description_ocr="PLANT INSPECTOR RELEASE", qty=1, rate=850.00, amount=850.00),
		]
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="9 - Import with Std VAT",
			subtotal=57614.30,
			tax_amount=64038.90,
			total_amount=121653.20,
			items=items,
		)
		template = self._actual_template()

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_pi = MagicMock()
		created_pi.name = "PI-CARGO-001"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		# Items = the 12 zero-rated service lines, summing to the subtotal
		assert len(pi_dict["items"]) == 12
		assert abs(sum(i["rate"] * i["qty"] for i in pi_dict["items"]) - 57614.30) < 0.01
		# ONE Actual tax row carrying the extracted customs VAT
		assert pi_dict["taxes_and_charges"] == "9 - Import with Std VAT"
		assert len(pi_dict["taxes"]) == 1
		tax = pi_dict["taxes"][0]
		assert tax["charge_type"] == "Actual"
		assert tax["account_head"] == "9500/000 - Vat Control Account - CC"
		assert tax["tax_amount"] == 64038.90

	def test_percentage_template_rows_not_injected(self, mock_frappe, sample_settings):
		"""A percentage template must NOT get a tax_amount key — ERPNext
		computes percentage rows itself."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="1 - Standard VAT",
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(rate=1000, qty=1, amount=1000)],
		)
		row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="On Net Total",
			row_id=None,
			account_head="2200 - VAT Input - TC",
			description="VAT 15%",
			rate=15.0,
			cost_center=None,
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		template = SimpleNamespace(company="Test Company", taxes=[row])

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_pi = MagicMock()
		created_pi.name = "PI-PCT-001"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()

		doc.create_purchase_invoice()

		pi_dict = mock_frappe.get_doc.call_args[0][0]
		assert "tax_amount" not in pi_dict["taxes"][0]


# ---------------------------------------------------------------------------
# Alias upsert — live-review M1 / roadmap C1-6
# ---------------------------------------------------------------------------


class TestAliasUpsert:
	"""A later confirmation with a different target must UPDATE the alias, not
	be silently dropped (first-mapping-wins-forever poisoned auto-matching).
	Updates go through db.set_value (alias controllers are pass-only)."""

	def test_supplier_alias_corrected(self, mock_frappe):
		doc = _make_ocr_import(supplier="New Supplier", supplier_name_ocr="ACME LTD")
		mock_frappe.db.get_value.return_value = "Old Supplier"

		doc._save_supplier_alias()

		mock_frappe.db.set_value.assert_called_once_with(
			"OCR Supplier Alias", "ACME LTD", {"supplier": "New Supplier", "source": "Auto"}
		)
		mock_frappe.get_doc.assert_not_called()

	def test_supplier_alias_unchanged_skips_write(self, mock_frappe):
		doc = _make_ocr_import(supplier="Same Supplier", supplier_name_ocr="ACME LTD")
		mock_frappe.db.get_value.return_value = "Same Supplier"

		doc._save_supplier_alias()

		mock_frappe.db.set_value.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

	def test_supplier_alias_created_when_new(self, mock_frappe):
		doc = _make_ocr_import(supplier="New Supplier", supplier_name_ocr="ACME LTD")
		mock_frappe.db.get_value.return_value = None
		new_doc = MagicMock()
		mock_frappe.get_doc.return_value = new_doc

		doc._save_supplier_alias()

		new_doc.insert.assert_called_once_with(ignore_permissions=True)
		mock_frappe.db.set_value.assert_not_called()

	def test_item_alias_corrected(self, mock_frappe):
		"""v1.8.0 (Q7c): with a supplier on the record, the correction targets
		the SUPPLIER-SCOPED row (by name) — never a global row."""
		doc = _make_ocr_import()  # supplier="Test Supplier"
		item = _make_item(description_ocr="Widget", item_code="ITEM-B")
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(name="ALIAS-0001", item_code="ITEM-A")])

		doc._save_item_alias(item)

		assert mock_frappe.get_all.call_args.kwargs["filters"] == {
			"ocr_text": "Widget",
			"supplier": "Test Supplier",
		}
		mock_frappe.db.set_value.assert_called_once_with(
			"OCR Item Alias", "ALIAS-0001", {"item_code": "ITEM-B", "source": "Auto"}
		)

	def test_item_alias_stale_row_cannot_clobber(self, mock_frappe):
		"""allow_update=False (row unchanged in this save — a stale still-
		Confirmed record being re-saved) must NOT rewrite an existing alias,
		but may still insert a missing one."""
		doc = _make_ocr_import()
		item = _make_item(description_ocr="Widget", item_code="ITEM-B")
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(name="ALIAS-0001", item_code="ITEM-A")])

		doc._save_item_alias(item, allow_update=False)

		mock_frappe.db.set_value.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

		# Missing alias still inserts even without allow_update
		mock_frappe.get_all = MagicMock(return_value=[])
		new_doc = MagicMock()
		mock_frappe.get_doc.return_value = new_doc
		doc._save_item_alias(item, allow_update=False)
		new_doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_item_alias_saved_supplier_scoped(self, mock_frappe):
		"""v1.8.0 (Q7c): a confirm with a known parent supplier learns a
		supplier-scoped alias — the inserted row carries the supplier."""
		doc = _make_ocr_import(supplier="Supplier A")
		item = _make_item(description_ocr="Bracket 40mm", item_code="ITEM-A")
		mock_frappe.get_all = MagicMock(return_value=[])
		new_doc = MagicMock()
		mock_frappe.get_doc.return_value = new_doc

		doc._save_item_alias(item)

		inserted = mock_frappe.get_doc.call_args[0][0]
		assert inserted["supplier"] == "Supplier A"
		assert inserted["ocr_text"] == "Bracket 40mm"
		assert inserted["item_code"] == "ITEM-A"
		new_doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_item_alias_global_when_no_supplier(self, mock_frappe):
		"""No parent supplier → the alias stays global (blank supplier), and the
		existence check runs against global rows only."""
		doc = _make_ocr_import(supplier="")
		item = _make_item(description_ocr="Widget", item_code="ITEM-B")
		mock_frappe.get_all = MagicMock(return_value=[])
		new_doc = MagicMock()
		mock_frappe.get_doc.return_value = new_doc

		doc._save_item_alias(item)

		assert mock_frappe.get_all.call_args.kwargs["filters"] == {
			"ocr_text": "Widget",
			"supplier": ["is", "not set"],
		}
		inserted = mock_frappe.get_doc.call_args[0][0]
		assert inserted["supplier"] == ""

	def test_item_alias_scoped_correction_leaves_global_untouched(self, mock_frappe):
		"""The motivating bug: correcting a mapping for supplier A must not
		rewrite the global row other suppliers rely on. With a supplier set and
		no scoped row yet, the save INSERTS a scoped row — set_value (which
		would hit an existing row) is never called."""
		doc = _make_ocr_import(supplier="Supplier A")
		item = _make_item(description_ocr="Widget", item_code="ITEM-A2")
		# No supplier-scoped row exists (the global one is not in this filter's result)
		mock_frappe.get_all = MagicMock(return_value=[])
		new_doc = MagicMock()
		mock_frappe.get_doc.return_value = new_doc

		doc._save_item_alias(item)

		mock_frappe.db.set_value.assert_not_called()
		inserted = mock_frappe.get_doc.call_args[0][0]
		assert inserted["supplier"] == "Supplier A"


# ---------------------------------------------------------------------------
# JE multi-tax-account split — live-review M2 / roadmap C1-7
# ---------------------------------------------------------------------------


class TestJEMultiTaxSplit:
	def _je_doc(self, tax_amount):
		return _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			tax_template="VAT + Levy",
			tax_amount=tax_amount,
			items=[_make_item(amount=1000)],
		)

	def _setup(self, mock_frappe, sample_settings, tax_rows):
		template = SimpleNamespace(company="Test Company", taxes=tax_rows)

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_je = MagicMock()
		created_je.name = "JE-SPLIT-001"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

	def test_two_rated_rows_split_proportionally(self, mock_frappe, sample_settings):
		"""VAT 15% + levy 2% template, tax 170 → 150.00 + 20.00, JE balances."""
		rows = [
			SimpleNamespace(account_head="2200 - VAT Input - TC", rate=15.0),
			SimpleNamespace(account_head="2210 - Levy - TC", rate=2.0),
		]
		self._setup(mock_frappe, sample_settings, rows)

		self._je_doc(170.00).create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		accounts = je_dict["accounts"]
		# 1 expense + 2 tax debits + 1 credit
		assert len(accounts) == 4
		by_account = {a["account"]: a["debit_in_account_currency"] for a in accounts[:-1]}
		assert by_account["2200 - VAT Input - TC"] == 150.00
		assert by_account["2210 - Levy - TC"] == 20.00
		total_debit = sum(a["debit_in_account_currency"] for a in accounts)
		total_credit = sum(a["credit_in_account_currency"] for a in accounts)
		assert abs(total_debit - total_credit) < 0.005

	def test_uninferable_split_books_first_account_and_warns(self, mock_frappe, sample_settings):
		"""Zero-rate (Actual) row in a multi-row template → full amount on the
		first account + an orange review warning (never a silent drop)."""
		rows = [
			SimpleNamespace(account_head="2200 - VAT Input - TC", rate=15.0),
			SimpleNamespace(account_head="2220 - Actual Charge - TC", rate=0.0),
		]
		self._setup(mock_frappe, sample_settings, rows)

		self._je_doc(170.00).create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		accounts = je_dict["accounts"]
		tax_lines = [a for a in accounts if a["account"] == "2200 - VAT Input - TC"]
		assert tax_lines[0]["debit_in_account_currency"] == 170.00
		warning_calls = [
			c for c in mock_frappe.msgprint.call_args_list if c.kwargs.get("indicator") == "orange"
		]
		assert warning_calls


# ---------------------------------------------------------------------------
# unlink_document write guard — live-review S1 / roadmap C1-5
# ---------------------------------------------------------------------------


class TestUnlinkWriteGuard:
	def test_unlink_requires_ocr_import_write(self, mock_frappe):
		"""Read-only OCR Import + delete-on-PI must no longer be enough to
		unlink: the source-doc write check runs FIRST."""
		doc = _make_ocr_import(status="Draft Created", purchase_invoice="PI-001")
		doc.db_set = MagicMock()
		mock_frappe.has_permission.return_value = False

		with pytest.raises(Exception):
			doc.unlink_document()

		doc.db_set.assert_not_called()
		mock_frappe.delete_doc.assert_not_called()


# ---------------------------------------------------------------------------
# Code-review hardening of the import-VAT injection + JE split
# ---------------------------------------------------------------------------


class TestInjectionGuards:
	"""Injection must be scoped to pure-Actual templates and must never emit
	an Actual row flagged included_in_print_rate (ERPNext rejects that)."""

	def _mixed_template(self):
		pct = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="On Net Total",
			row_id=None,
			account_head="2200 - VAT Input - TC",
			description="VAT 15%",
			rate=15.0,
			cost_center=None,
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		actual = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="Actual",
			row_id=None,
			account_head="2230 - Freight - TC",
			description="Freight",
			rate=0.0,
			cost_center=None,
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		return SimpleNamespace(company="Test Company", taxes=[pct, actual])

	def _create_pi(self, mock_frappe, sample_settings, doc, template):
		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_pi = MagicMock()
		created_pi.name = "PI-GUARD-001"
		mock_frappe.get_doc.return_value = created_pi
		mock_frappe.msgprint = MagicMock()
		doc.create_purchase_invoice()
		return mock_frappe.get_doc.call_args[0][0]

	def test_mixed_template_not_injected(self, mock_frappe, sample_settings):
		"""A percentage template with an auxiliary Actual row must NOT get the
		extracted VAT injected — that would double-tax ordinary invoices."""
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="VAT 15% + Freight",
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(rate=1000, qty=1, amount=1000)],
		)
		pi_dict = self._create_pi(mock_frappe, sample_settings, doc, self._mixed_template())

		for tax in pi_dict["taxes"]:
			assert "tax_amount" not in tax

	def test_inclusive_detection_never_flags_actual_rows(self, mock_frappe, sample_settings):
		"""Case B shape: VAT-inclusive item sum + Actual import template. The
		Actual row must NOT get included_in_print_rate=1 (ERPNext's
		validate_inclusive_tax rejects it at insert)."""
		actual_row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="Actual",
			row_id=None,
			account_head="9500/000 - Vat Control Account - CC",
			description="Customs VAT",
			rate=0.0,
			cost_center=None,
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		template = SimpleNamespace(company="Test Company", taxes=[actual_row])
		# items sum ≈ total (inclusive shape) → _detect_tax_inclusive_rates True
		doc = _make_ocr_import(
			document_type="Purchase Invoice",
			tax_template="9 - Import with Std VAT",
			subtotal=1000.00,
			tax_amount=150.00,
			total_amount=1150.00,
			items=[_make_item(rate=1150, qty=1, amount=1150)],
		)
		pi_dict = self._create_pi(mock_frappe, sample_settings, doc, template)

		tax = pi_dict["taxes"][0]
		assert tax["included_in_print_rate"] == 0
		assert tax["tax_amount"] == 150.00  # still injected


class TestJESplitExactness:
	def test_three_row_split_sums_exactly(self, mock_frappe, sample_settings):
		"""Booked tax must equal the extracted tax_amount exactly for a 3-row
		odd split (rounding remainder handling)."""
		rows = [SimpleNamespace(account_head=f"22{i}0 - Tax {i} - TC", rate=3.0) for i in range(3)]
		template = SimpleNamespace(company="Test Company", taxes=rows)
		doc = _make_ocr_import(
			document_type="Journal Entry",
			credit_account="2100 - Accounts Payable - TC",
			tax_template="Three Way",
			tax_amount=1.00,
			items=[_make_item(amount=1000)],
		)

		def get_cached_doc_handler(doctype, name=None):
			if doctype == "OCR Settings":
				return sample_settings
			if doctype == "Purchase Taxes and Charges Template":
				return template
			return MagicMock()

		mock_frappe.get_cached_doc.side_effect = get_cached_doc_handler
		mock_frappe.db.get_value.side_effect = _db_get_value_handler()
		created_je = MagicMock()
		created_je.name = "JE-3WAY"
		mock_frappe.get_doc.return_value = created_je
		mock_frappe.msgprint = MagicMock()

		doc.create_journal_entry()

		je_dict = mock_frappe.get_doc.call_args[0][0]
		tax_debits = [
			a["debit_in_account_currency"] for a in je_dict["accounts"] if a["account"].startswith("22")
		]
		assert round(sum(tax_debits), 2) == 1.00


# ---------------------------------------------------------------------------
# Q9 (v1.9.0): _build_taxes_from_template explicit-args contract.
# The helper takes real values, not a doc-like proxy — both callers (invoice
# PI/PR, fleet Direct-Expense PI) pass values directly. These tests exercise
# the signature in isolation and prove the Actual-injection semantics (ADR-0008)
# are unchanged: inject into a pure-Actual template, never into a mixed one.
# ---------------------------------------------------------------------------
class TestBuildTaxesExplicitArgs:
	def _percentage_template(self):
		row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="On Net Total",
			row_id=None,
			account_head="2200 - VAT Input - TC",
			description="VAT 15%",
			rate=15.0,
			cost_center="Main - TC",
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		return SimpleNamespace(company="Test Company", taxes=[row])

	def _actual_template(self):
		row = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="Actual",
			row_id=None,
			account_head="2201 - Import VAT - TC",
			description="Import VAT",
			rate=0.0,
			cost_center="Main - TC",
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		return SimpleNamespace(company="Test Company", taxes=[row])

	def _mixed_template(self):
		pct = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="On Net Total",
			row_id=None,
			account_head="2200 - VAT Input - TC",
			description="VAT 15%",
			rate=15.0,
			cost_center="Main - TC",
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		actual = SimpleNamespace(
			category="Total",
			add_deduct_tax="Add",
			charge_type="Actual",
			row_id=None,
			account_head="5100 - Freight - TC",
			description="Freight",
			rate=0.0,
			cost_center="Main - TC",
			account_currency="ZAR",
			included_in_print_rate=0,
			included_in_paid_amount=0,
		)
		return SimpleNamespace(company="Test Company", taxes=[pct, actual])

	def test_no_template_returns_none(self, mock_frappe):
		assert _build_taxes_from_template(None, "Test Company", 0, False) == (None, None)
		assert _build_taxes_from_template("", "Test Company", 0, False) == (None, None)

	def test_company_mismatch_throws(self, mock_frappe):
		mock_frappe.get_cached_doc.return_value = self._percentage_template()
		mock_frappe.throw.side_effect = Exception("company mismatch")
		with pytest.raises(Exception, match="company mismatch"):
			_build_taxes_from_template("SA VAT 15%", "Other Company", 150.0, False)

	def test_percentage_template_no_injection(self, mock_frappe):
		"""A pure-percentage template is passed through; no tax_amount injected."""
		mock_frappe.get_cached_doc.return_value = self._percentage_template()
		name, taxes = _build_taxes_from_template("SA VAT 15%", "Test Company", 150.0, False)
		assert name == "SA VAT 15%"
		assert len(taxes) == 1
		assert taxes[0]["rate"] == 15.0
		assert "tax_amount" not in taxes[0]

	def test_actual_template_injects_extracted_vat(self, mock_frappe):
		"""ADR-0008: a pure-Actual (customs) template gets the extracted VAT
		injected into the Actual row — the fleet + invoice import-VAT path."""
		mock_frappe.get_cached_doc.return_value = self._actual_template()
		name, taxes = _build_taxes_from_template("9 - Import with Std VAT", "Test Company", 146.74, False)
		assert name == "9 - Import with Std VAT"
		assert taxes[0]["tax_amount"] == 146.74

	def test_mixed_template_not_injected(self, mock_frappe):
		"""ADR-0008 guard: a mixed template (percentage + auxiliary Actual) must
		NOT get the extracted VAT injected — that would double-tax ordinary invoices."""
		mock_frappe.get_cached_doc.return_value = self._mixed_template()
		mock_frappe.msgprint = MagicMock()
		_name, taxes = _build_taxes_from_template("SA VAT 15% + Freight", "Test Company", 146.74, False)
		assert all("tax_amount" not in t for t in taxes)

	def test_rates_include_tax_sets_print_rate_on_percentage(self, mock_frappe):
		"""rates_include_tax=True flips included_in_print_rate on percentage rows."""
		mock_frappe.get_cached_doc.return_value = self._percentage_template()
		_name, taxes = _build_taxes_from_template("SA VAT 15%", "Test Company", 150.0, True)
		assert taxes[0]["included_in_print_rate"] == 1

	def test_rates_include_tax_never_flags_actual_row(self, mock_frappe):
		"""Even with rates_include_tax=True, an Actual row keeps its original flag
		(ERPNext rejects Actual + inclusive at insert)."""
		mock_frappe.get_cached_doc.return_value = self._actual_template()
		_name, taxes = _build_taxes_from_template("9 - Import with Std VAT", "Test Company", 100.0, True)
		assert taxes[0]["included_in_print_rate"] == 0
