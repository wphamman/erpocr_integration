"""Tests for OCR Import document creation methods and guards."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
	OCRImport,
	_detect_tax_inclusive_rates,
	_extract_service_pattern,
)

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
	"""Create a mock OCR Import Item row."""
	defaults = dict(
		description_ocr="Test Item",
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
		assert doc.status == "Completed"
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


# ---------------------------------------------------------------------------
# _update_status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
	def test_status_completed_when_journal_entry_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			journal_entry="JE-00001",
		)
		doc._update_status()
		assert doc.status == "Completed"

	def test_status_completed_when_pi_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			purchase_invoice="PI-00001",
		)
		doc._update_status()
		assert doc.status == "Completed"

	def test_status_completed_when_pr_set(self, mock_frappe):
		doc = _make_ocr_import(
			status="Needs Review",
			purchase_receipt="PR-00001",
		)
		doc._update_status()
		assert doc.status == "Completed"

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
		result = _extract_service_pattern("CACTUSCRAFT CC - CHEMICALS")
		assert result == "cactuscraft cc chemicals"

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
