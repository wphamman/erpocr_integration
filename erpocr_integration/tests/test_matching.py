"""Tests for erpocr_integration.tasks.matching — supplier/item matching with mocked frappe."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# match_supplier
# ---------------------------------------------------------------------------

class TestMatchSupplier:
	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("")
		assert result is None
		assert status == "Unmatched"

	def test_none_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier(None)
		assert result is None
		assert status == "Unmatched"

	def test_alias_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(side_effect=lambda doctype, filters, field: (
			"SUP-001" if doctype == "OCR Supplier Alias" else None
		))
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("Star Pops (Pty) Ltd")
		assert result == "SUP-001"
		assert status == "Auto Matched"

	def test_supplier_name_match(self, mock_frappe):
		# No alias, but supplier_name matches
		mock_frappe.db.get_value = MagicMock(side_effect=lambda doctype, filters, field: (
			None if doctype == "OCR Supplier Alias" else "Star Pops (Pty) Ltd"
		))
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("Star Pops (Pty) Ltd")
		assert result == "Star Pops (Pty) Ltd"
		assert status == "Auto Matched"

	def test_supplier_doc_exists(self, mock_frappe):
		# No alias, no supplier_name match, but doc exists by name
		mock_frappe.db.get_value = MagicMock(return_value=None)
		mock_frappe.db.exists = MagicMock(return_value=True)
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("SUP-001")
		assert result == "SUP-001"
		assert status == "Auto Matched"

	def test_no_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(return_value=None)
		mock_frappe.db.exists = MagicMock(return_value=False)
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("Unknown Supplier")
		assert result is None
		assert status == "Unmatched"


# ---------------------------------------------------------------------------
# match_item
# ---------------------------------------------------------------------------

class TestMatchItem:
	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("")
		assert result is None
		assert status == "Unmatched"

	def test_alias_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(side_effect=lambda doctype, filters, field: (
			"ITEM-001" if doctype == "OCR Item Alias" else None
		))
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Premium Lollipops")
		assert result == "ITEM-001"
		assert status == "Auto Matched"

	def test_item_name_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(side_effect=lambda doctype, filters, field: (
			None if doctype == "OCR Item Alias" else "ITEM-001"
		))
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Premium Lollipops")
		assert result == "ITEM-001"
		assert status == "Auto Matched"

	def test_item_code_exists(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(return_value=None)
		mock_frappe.db.exists = MagicMock(return_value=True)
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("POP-050")
		assert result == "POP-050"
		assert status == "Auto Matched"

	def test_no_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(return_value=None)
		mock_frappe.db.exists = MagicMock(return_value=False)
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Unknown Item")
		assert result is None
		assert status == "Unmatched"


# ---------------------------------------------------------------------------
# match_supplier_fuzzy
# ---------------------------------------------------------------------------

class TestMatchSupplierFuzzy:
	def _setup_suppliers(self, mock_frappe, suppliers, aliases=None):
		"""Helper to configure mock suppliers and aliases."""
		supplier_data = [SimpleNamespace(name=s[0], supplier_name=s[1]) for s in suppliers]
		alias_data = [SimpleNamespace(ocr_text=a[0], supplier=a[1]) for a in (aliases or [])]
		mock_frappe.get_all = MagicMock(side_effect=lambda doctype, **kwargs: (
			supplier_data if doctype == "Supplier" else alias_data
		))

	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, status, score = match_supplier_fuzzy("")
		assert result is None
		assert status == "Unmatched"

	def test_high_similarity_match(self, mock_frappe):
		self._setup_suppliers(mock_frappe, [
			("SUP-001", "Star Pops (Pty) Ltd"),
		])
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# Very similar — should match
		result, status, score = match_supplier_fuzzy("Star Pops (Pty) Limited")
		assert result == "SUP-001"
		assert status == "Suggested"
		assert score >= 80

	def test_below_threshold(self, mock_frappe):
		self._setup_suppliers(mock_frappe, [
			("SUP-001", "Star Pops (Pty) Ltd"),
		])
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# Completely different — should not match
		result, status, score = match_supplier_fuzzy("Cloudflare Inc", threshold=80)
		assert result is None
		assert status == "Unmatched"

	def test_best_of_multiple(self, mock_frappe):
		self._setup_suppliers(mock_frappe, [
			("SUP-001", "Star Pops (Pty) Ltd"),
			("SUP-002", "Star Products (Pty) Ltd"),
		])
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, status, score = match_supplier_fuzzy("Star Pops Pty Ltd")
		assert result == "SUP-001"  # Closer match

	def test_alias_fuzzy_match(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			suppliers=[("SUP-001", "Official Name")],
			aliases=[("StarPops", "SUP-001")],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, status, score = match_supplier_fuzzy("Star Pops", threshold=50)
		# Should match via alias fuzzy if score is high enough
		assert result is not None

	def test_custom_threshold(self, mock_frappe):
		self._setup_suppliers(mock_frappe, [
			("SUP-001", "ABC Company"),
		])
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# With very low threshold, even poor matches succeed
		result_low, _, score_low = match_supplier_fuzzy("ABC Corp", threshold=30)
		# With high threshold, poor matches are rejected
		result_high, _, score_high = match_supplier_fuzzy("XYZ Inc", threshold=95)
		assert result_high is None


# ---------------------------------------------------------------------------
# match_item_fuzzy
# ---------------------------------------------------------------------------

class TestMatchItemFuzzy:
	def _setup_items(self, mock_frappe, items, aliases=None):
		item_data = [SimpleNamespace(name=i[0], item_name=i[1]) for i in items]
		alias_data = [SimpleNamespace(ocr_text=a[0], item_code=a[1]) for a in (aliases or [])]
		mock_frappe.get_all = MagicMock(side_effect=lambda doctype, **kwargs: (
			item_data if doctype == "Item" else alias_data
		))

	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, status, score = match_item_fuzzy("")
		assert result is None

	def test_high_similarity_match(self, mock_frappe):
		self._setup_items(mock_frappe, [
			("POP-050", "Premium Lollipops Assorted 50pk"),
		])
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, status, score = match_item_fuzzy("Premium Lollipops Assorted 50 pack")
		assert result == "POP-050"
		assert status == "Suggested"

	def test_below_threshold(self, mock_frappe):
		self._setup_items(mock_frappe, [
			("POP-050", "Premium Lollipops Assorted 50pk"),
		])
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, status, score = match_item_fuzzy("Delivery Fee", threshold=80)
		assert result is None


# ---------------------------------------------------------------------------
# match_service_item
# ---------------------------------------------------------------------------

class TestMatchServiceItem:
	def _setup_mappings(self, mock_frappe, supplier_mappings=None, generic_mappings=None):
		"""Configure mock service mappings."""
		def get_all_side_effect(doctype, **kwargs):
			if doctype != "OCR Service Mapping":
				return []
			filters = kwargs.get("filters", {})
			supplier_filter = filters.get("supplier")
			# Supplier-specific: filter is a string (e.g., "SUP-001")
			# Generic: filter is ["is", "not set"] (a list)
			if isinstance(supplier_filter, str):
				return [SimpleNamespace(**m) for m in (supplier_mappings or [])]
			return [SimpleNamespace(**m) for m in (generic_mappings or [])]
		mock_frappe.get_all = MagicMock(side_effect=get_all_side_effect)

	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("")
		assert result is None

	def test_none_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item(None)
		assert result is None

	def test_generic_match(self, mock_frappe):
		self._setup_mappings(mock_frappe, generic_mappings=[{
			"description_pattern": "delivery",
			"item_code": "DELIVERY",
			"item_name": "Delivery Fee",
			"expense_account": "5200 - Delivery - TC",
			"cost_center": "Main - TC",
		}])
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee - Standard", company="Test Company")
		assert result is not None
		assert result["item_code"] == "DELIVERY"
		assert result["match_status"] == "Auto Matched"

	def test_supplier_specific_takes_priority(self, mock_frappe):
		self._setup_mappings(
			mock_frappe,
			supplier_mappings=[{
				"description_pattern": "delivery",
				"item_code": "DEL-STAR",
				"item_name": "Star Pops Delivery",
				"expense_account": "5200 - Delivery - TC",
				"cost_center": "",
			}],
			generic_mappings=[{
				"description_pattern": "delivery",
				"item_code": "DELIVERY",
				"item_name": "Delivery Fee",
				"expense_account": "5200 - Delivery - TC",
				"cost_center": "",
			}],
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee", company="Test Company", supplier="SUP-001")
		assert result["item_code"] == "DEL-STAR"

	def test_no_match(self, mock_frappe):
		self._setup_mappings(mock_frappe, generic_mappings=[{
			"description_pattern": "subscription",
			"item_code": "SUB-001",
			"item_name": "Subscription",
			"expense_account": "5300 - Subscriptions - TC",
			"cost_center": "",
		}])
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee", company="Test Company")
		assert result is None

	def test_case_insensitive(self, mock_frappe):
		self._setup_mappings(mock_frappe, generic_mappings=[{
			"description_pattern": "delivery",
			"item_code": "DELIVERY",
			"item_name": "Delivery Fee",
			"expense_account": "5200 - Delivery - TC",
			"cost_center": "",
		}])
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("DELIVERY FEE", company="Test Company")
		assert result is not None
		assert result["item_code"] == "DELIVERY"
