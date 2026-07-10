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
		mock_frappe.db.get_value = MagicMock(
			side_effect=lambda doctype, filters, field: "SUP-001" if doctype == "OCR Supplier Alias" else None
		)
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("Acme Trading (Pty) Ltd")
		assert result == "SUP-001"
		assert status == "Auto Matched"

	def test_supplier_name_match(self, mock_frappe):
		# No alias, but supplier_name matches
		mock_frappe.db.get_value = MagicMock(
			side_effect=lambda doctype, filters, field: (
				None if doctype == "OCR Supplier Alias" else "Acme Trading (Pty) Ltd"
			)
		)
		from erpocr_integration.tasks.matching import match_supplier

		result, status = match_supplier("Acme Trading (Pty) Ltd")
		assert result == "Acme Trading (Pty) Ltd"
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
		"""A global alias (blank supplier) matches via the get_all NULL-filter
		lookup — every pre-v1.8.0 alias row keeps working through this tier."""
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(item_code="ITEM-001")])
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Premium Lollipops")
		assert result == "ITEM-001"
		assert status == "Auto Matched"

	def test_item_name_match(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(
			side_effect=lambda doctype, filters, field: None if doctype == "OCR Item Alias" else "ITEM-001"
		)
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
# Supplier-scoped item aliases (v1.8.0, Q7c) — the three ruled scenarios:
# existing-alias regression, supplier-scoped beats global, cross-supplier
# collision resolution.
# ---------------------------------------------------------------------------


class TestSupplierScopedItemAlias:
	def _wire_aliases(self, mock_frappe, scoped=None, global_rows=None):
		"""scoped: {(ocr_text, supplier): item_code}; global_rows: {ocr_text: item_code}."""
		scoped = scoped or {}
		global_rows = global_rows or {}

		def _get_value(doctype, filters=None, field=None, **kw):
			if doctype == "OCR Item Alias" and isinstance(filters, dict) and filters.get("supplier"):
				return scoped.get((filters["ocr_text"], filters["supplier"]))
			return None  # Item.item_name lookups miss — aliases decide these tests

		def _get_all(doctype, filters=None, **kw):
			if doctype == "OCR Item Alias" and filters and filters.get("supplier") == ["is", "not set"]:
				item = global_rows.get(filters["ocr_text"])
				return [SimpleNamespace(item_code=item)] if item else []
			return []

		mock_frappe.db.get_value = MagicMock(side_effect=_get_value)
		mock_frappe.get_all = MagicMock(side_effect=_get_all)
		mock_frappe.db.exists = MagicMock(return_value=False)

	def test_existing_global_alias_regression(self, mock_frappe):
		"""Every pre-v1.8.0 alias (blank supplier) keeps working unchanged —
		with AND without a supplier passed to match_item."""
		self._wire_aliases(mock_frappe, global_rows={"Widget": "ITEM-G"})
		from erpocr_integration.tasks.matching import match_item

		assert match_item("Widget") == ("ITEM-G", "Auto Matched")
		assert match_item("Widget", supplier="Supplier A") == ("ITEM-G", "Auto Matched")

	def test_supplier_scoped_beats_global(self, mock_frappe):
		"""A supplier-scoped alias wins over the global one for ITS supplier;
		every other supplier still gets the global mapping."""
		self._wire_aliases(
			mock_frappe,
			scoped={("Widget", "Supplier A"): "ITEM-A"},
			global_rows={"Widget": "ITEM-G"},
		)
		from erpocr_integration.tasks.matching import match_item

		assert match_item("Widget", supplier="Supplier A") == ("ITEM-A", "Auto Matched")
		assert match_item("Widget", supplier="Supplier B") == ("ITEM-G", "Auto Matched")
		assert match_item("Widget") == ("ITEM-G", "Auto Matched")

	def test_cross_supplier_collision_resolution(self, mock_frappe):
		"""The motivating case: the same printed description maps to DIFFERENT
		items per supplier — each supplier resolves to its own item, and an
		unknown supplier falls through (no false positive from either)."""
		self._wire_aliases(
			mock_frappe,
			scoped={
				("Bracket 40mm", "Supplier A"): "ITEM-A",
				("Bracket 40mm", "Supplier B"): "ITEM-B",
			},
		)
		from erpocr_integration.tasks.matching import match_item

		assert match_item("Bracket 40mm", supplier="Supplier A") == ("ITEM-A", "Auto Matched")
		assert match_item("Bracket 40mm", supplier="Supplier B") == ("ITEM-B", "Auto Matched")
		assert match_item("Bracket 40mm", supplier="Supplier C") == (None, "Unmatched")
		assert match_item("Bracket 40mm") == (None, "Unmatched")

	def test_scoped_read_orders_by_modified_desc(self, mock_frappe):
		"""Bounce rework 2: duplicates are legal now — the tier-1 scoped read
		must carry the SAME order_by as the global tier and the correction
		path, so reads deterministically hit the row corrections target
		(most-recently-modified) on v15 AND v16."""
		mock_frappe.db.get_value = MagicMock(return_value="ITEM-A")
		from erpocr_integration.tasks.matching import match_item

		result, _status = match_item("Widget", supplier="Supplier A")

		assert result == "ITEM-A"
		assert mock_frappe.db.get_value.call_args.kwargs["order_by"] == "modified desc, name asc"

	def test_fuzzy_tier_excludes_other_suppliers_scoped_aliases(self, mock_frappe):
		"""The Q7c invariant holds one tier down: supplier A's scoped alias
		must not become a fuzzy 'Suggested' candidate on supplier B's lines."""
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kw: (
				[SimpleNamespace(ocr_text="Bracket 40mm", item_code="ITEM-A", supplier="Supplier A")]
				if doctype == "OCR Item Alias"
				else []
			)
		)
		from erpocr_integration.tasks.matching import match_item_fuzzy

		# Supplier B: A's scoped alias is not a candidate → no match
		result, status, _ = match_item_fuzzy("BRACKET 40 MM", 80, supplier="Supplier B")
		assert result is None
		assert status == "Unmatched"

		# Supplier A: its own scoped alias IS a candidate
		result, status, _ = match_item_fuzzy("BRACKET 40 MM", 80, supplier="Supplier A")
		assert result == "ITEM-A"
		assert status == "Suggested"

		# No supplier (e.g. unmatched-supplier invoice): scoped rows excluded
		result, status, _ = match_item_fuzzy("BRACKET 40 MM", 80)
		assert result is None

	def test_fuzzy_tier_global_aliases_still_candidates(self, mock_frappe):
		"""Global (blank-supplier) alias rows keep working in the fuzzy pool
		for every supplier — the pre-v1.8.0 behavior."""
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kw: (
				[SimpleNamespace(ocr_text="Bracket 40mm", item_code="ITEM-G", supplier=None)]
				if doctype == "OCR Item Alias"
				else []
			)
		)
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, status, _ = match_item_fuzzy("BRACKET 40 MM", 80, supplier="Supplier B")
		assert result == "ITEM-G"
		assert status == "Suggested"

	def test_run_matching_passes_supplier_to_alias_tier(self, mock_frappe):
		"""End-to-end through api._run_matching: the matched supplier flows
		into the alias tier, so the supplier-scoped alias decides the line."""
		self._wire_aliases(
			mock_frappe,
			scoped={("Bracket 40mm", "Supplier A"): "ITEM-A"},
			global_rows={"Bracket 40mm": "ITEM-G"},
		)
		# Supplier resolution: alias table hit → "Supplier A"
		original_get_value = mock_frappe.db.get_value.side_effect

		def _get_value(doctype, filters=None, field=None, **kw):
			if doctype == "OCR Supplier Alias":
				return "Supplier A"
			return original_get_value(doctype, filters, field, **kw)

		mock_frappe.db.get_value = MagicMock(side_effect=_get_value)

		class _Settings(SimpleNamespace):
			def get(self, key, default=None):
				return getattr(self, key, default)

		settings = _Settings(matching_threshold=80, default_item="")
		ocr_import = SimpleNamespace(
			supplier_name_ocr="ACME LTD",
			supplier="",
			supplier_match_status="",
			company="Test Company",
			items=[
				SimpleNamespace(
					description_ocr="Bracket 40mm",
					product_code="",
					item_code="",
					item_name="",
					match_status="",
					expense_account="",
					cost_center="",
				)
			],
		)

		from erpocr_integration.api import _run_matching

		_run_matching(ocr_import, {}, settings)

		assert ocr_import.supplier == "Supplier A"
		assert ocr_import.items[0].item_code == "ITEM-A"  # scoped beats global
		assert ocr_import.items[0].match_status == "Auto Matched"


# ---------------------------------------------------------------------------
# match_item_by_supplier_part (Item Supplier lookup, tier 2)
# ---------------------------------------------------------------------------


class TestMatchItemBySupplierPart:
	def test_empty_supplier(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("", "P-001")
		assert result is None
		assert status == "Unmatched"

	def test_empty_product_code(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("Acme Trading", "")
		assert result is None
		assert status == "Unmatched"

	def test_whitespace_only_inputs(self, mock_frappe):
		"""Whitespace-only inputs should be treated as empty after strip."""
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("  ", "  ")
		assert result is None
		assert status == "Unmatched"

	def test_single_match(self, mock_frappe):
		"""Exactly one Item Supplier hit → Auto Matched."""
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(parent="ITEM-001")])
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("Acme", "P-001")
		assert result == "ITEM-001"
		assert status == "Auto Matched"

		# Verify filters used
		call_kwargs = mock_frappe.get_all.call_args
		assert call_kwargs.args[0] == "Item Supplier"
		filters = call_kwargs.kwargs["filters"]
		assert filters["parenttype"] == "Item"
		assert filters["supplier"] == "Acme"
		assert filters["supplier_part_no"] == "P-001"

	def test_no_match(self, mock_frappe):
		"""Zero hits → Unmatched, fall through to description tiers."""
		mock_frappe.get_all = MagicMock(return_value=[])
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("Acme", "P-001")
		assert result is None
		assert status == "Unmatched"

	def test_multi_hit_skipped_with_log(self, mock_frappe):
		"""Multi-hit ambiguity → skip + log; do NOT pick first."""
		mock_frappe.get_all = MagicMock(
			return_value=[
				SimpleNamespace(parent="ITEM-001"),
				SimpleNamespace(parent="ITEM-002"),
			]
		)
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("Acme", "P-001")
		assert result is None
		assert status == "Unmatched"
		# log_error called with the candidate items in the message
		mock_frappe.log_error.assert_called_once()
		log_message = mock_frappe.log_error.call_args.kwargs["message"]
		assert "ITEM-001" in log_message
		assert "ITEM-002" in log_message
		assert "Acme" in log_message
		assert "P-001" in log_message

	def test_strips_inputs(self, mock_frappe):
		"""Surrounding whitespace is stripped before query."""
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(parent="ITEM-001")])
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, _status = match_item_by_supplier_part("  Acme  ", "  P-001  ")
		assert result == "ITEM-001"
		filters = mock_frappe.get_all.call_args.kwargs["filters"]
		assert filters["supplier"] == "Acme"
		assert filters["supplier_part_no"] == "P-001"


# ---------------------------------------------------------------------------
# Q10 (v1.9.0): chained match confidence — supplier-keyed item tiers cap their
# status to the MIN of the chain. A fuzzy "Suggested" supplier caps its
# supplier-keyed item matches to "Suggested"; confirmed/exact suppliers don't.
# ---------------------------------------------------------------------------


class TestChainedConfidenceCap:
	def test_cap_helper(self):
		from erpocr_integration.tasks.matching import _cap_to_supplier

		# Only a Suggested supplier + an Auto Matched item caps.
		assert _cap_to_supplier("Auto Matched", "Suggested") == "Suggested"
		assert _cap_to_supplier("Auto Matched", "Auto Matched") == "Auto Matched"
		assert _cap_to_supplier("Auto Matched", "Confirmed") == "Auto Matched"
		assert _cap_to_supplier("Auto Matched", None) == "Auto Matched"
		# A non-high item status (already Suggested/Unmatched) is unchanged.
		assert _cap_to_supplier("Suggested", "Suggested") == "Suggested"
		assert _cap_to_supplier("Unmatched", "Suggested") == "Unmatched"

	def test_supplier_part_capped_under_suggested_supplier(self, mock_frappe):
		"""Tier 1 (Item Supplier lookup) under a fuzzy supplier → Suggested."""
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(parent="ITEM-001")])
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		result, status = match_item_by_supplier_part("Acme", "P-001", supplier_status="Suggested")
		assert result == "ITEM-001"
		assert status == "Suggested"  # capped

	def test_supplier_part_uncapped_under_confirmed_supplier(self, mock_frappe):
		"""Tier 1 under a Confirmed/Auto Matched supplier behaves as today."""
		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(parent="ITEM-001")])
		from erpocr_integration.tasks.matching import match_item_by_supplier_part

		for sup_status in ("Auto Matched", "Confirmed", None):
			result, status = match_item_by_supplier_part("Acme", "P-001", supplier_status=sup_status)
			assert result == "ITEM-001"
			assert status == "Auto Matched"

	def test_scoped_alias_capped_under_suggested_supplier(self, mock_frappe):
		"""Tier 2 (supplier-scoped alias) under a fuzzy supplier → Suggested."""
		mock_frappe.db.get_value = MagicMock(return_value="ITEM-A")  # scoped alias hit
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Widget", supplier="Supplier A", supplier_status="Suggested")
		assert result == "ITEM-A"
		assert status == "Suggested"  # capped to the supplier's confidence

	def test_scoped_alias_uncapped_under_confirmed_supplier(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(return_value="ITEM-A")
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Widget", supplier="Supplier A", supplier_status="Confirmed")
		assert result == "ITEM-A"
		assert status == "Auto Matched"

	def test_global_alias_not_capped(self, mock_frappe):
		"""Tier 3 (global alias) is NOT supplier-keyed — a Suggested supplier must
		not cap it (the mapping holds regardless of which supplier this is)."""

		def _get_value(doctype, filters=None, field=None, **kw):
			return None  # no scoped alias

		def _get_all(doctype, filters=None, **kw):
			if doctype == "OCR Item Alias" and filters and filters.get("supplier") == ["is", "not set"]:
				return [SimpleNamespace(item_code="ITEM-G")]
			return []

		mock_frappe.db.get_value = MagicMock(side_effect=_get_value)
		mock_frappe.get_all = MagicMock(side_effect=_get_all)
		mock_frappe.db.exists = MagicMock(return_value=False)
		from erpocr_integration.tasks.matching import match_item

		result, status = match_item("Widget", supplier="Supplier A", supplier_status="Suggested")
		assert result == "ITEM-G"
		assert status == "Auto Matched"  # global tier is supplier-independent

	def _wire_run_matching(self, mock_frappe, supplier_result):
		"""supplier_result: (supplier, status) the supplier tiers should resolve to.
		Item side: Item Supplier lookup hits ITEM-A (tier 1)."""
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kw: (
				[SimpleNamespace(parent="ITEM-A")] if doctype == "Item Supplier" else []
			)
		)

		def _get_value(doctype, filters=None, field=None, **kw):
			if doctype == "OCR Supplier Alias":
				return supplier_result[0] if supplier_result[1] == "Auto Matched" else None
			return None

		mock_frappe.db.get_value = MagicMock(side_effect=_get_value)
		mock_frappe.db.exists = MagicMock(return_value=False)

	def _make_import(self):
		return SimpleNamespace(
			supplier_name_ocr="ACME LTD",
			supplier="",
			supplier_match_status="",
			company="Test Company",
			items=[
				SimpleNamespace(
					description_ocr="Bracket 40mm",
					product_code="P-001",
					item_code="",
					item_name="",
					match_status="",
					expense_account="",
					cost_center="",
				)
			],
		)

	def test_run_matching_caps_item_under_suggested_supplier(self, mock_frappe):
		"""Invoice path end-to-end: a fuzzy supplier caps the tier-1 item to Suggested."""
		# Supplier: exact tiers miss, fuzzy resolves "Acme Ltd" as Suggested.
		self._wire_run_matching(mock_frappe, ("Acme Ltd", "Suggested"))
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kw: (
				[SimpleNamespace(parent="ITEM-A")]
				if doctype == "Item Supplier"
				else [SimpleNamespace(name="Acme Ltd", supplier_name="Acme Ltd")]
				if doctype == "Supplier"
				else []
			)
		)

		class _Settings(SimpleNamespace):
			def get(self, key, default=None):
				return getattr(self, key, default)

		ocr_import = self._make_import()
		from erpocr_integration.api import _run_matching

		_run_matching(ocr_import, {}, _Settings(matching_threshold=1, default_item=""))

		assert ocr_import.supplier_match_status == "Suggested"
		# Tier-1 item resolved off a guessed supplier → capped, NOT "Auto Matched"
		assert ocr_import.items[0].item_code == "ITEM-A"
		assert ocr_import.items[0].match_status == "Suggested"

	def test_run_matching_uncapped_under_exact_supplier(self, mock_frappe):
		"""Confirmed/exact supplier → tier-1 item stays Auto Matched (today's behavior)."""
		self._wire_run_matching(mock_frappe, ("Acme Ltd", "Auto Matched"))

		class _Settings(SimpleNamespace):
			def get(self, key, default=None):
				return getattr(self, key, default)

		ocr_import = self._make_import()
		from erpocr_integration.api import _run_matching

		_run_matching(ocr_import, {}, _Settings(matching_threshold=80, default_item=""))

		assert ocr_import.supplier_match_status == "Auto Matched"
		assert ocr_import.items[0].item_code == "ITEM-A"
		assert ocr_import.items[0].match_status == "Auto Matched"

	def test_capped_item_keeps_auto_draft_blocked(self, mock_frappe):
		"""Safety assertion: the capped item statuses must not accidentally unblock
		auto-draft. A Suggested supplier already blocks it — confirm the whole
		record fails _is_high_confidence even though the item pre-fill is kept."""
		from erpocr_integration.tasks.auto_draft import _is_high_confidence

		doc = SimpleNamespace(
			supplier="Acme Ltd",
			supplier_match_status="Suggested",
			items=[SimpleNamespace(item_code="ITEM-A", match_status="Suggested", description_ocr="Bracket")],
		)
		is_high, reason = _is_high_confidence(doc)
		assert is_high is False
		assert "Suggested" in reason

	def test_dn_path_caps_item_under_suggested_supplier(self, mock_frappe):
		"""DN path consistency: a fuzzy DN supplier caps the supplier-scoped alias
		tier to Suggested, same as the invoice path."""

		# Supplier fuzzy-resolves to Suggested; scoped alias hits ITEM-A.
		def _get_value(doctype, filters=None, field=None, **kw):
			if doctype == "OCR Supplier Alias":
				return None
			if doctype == "OCR Item Alias" and isinstance(filters, dict) and filters.get("supplier"):
				return "ITEM-A"
			return None

		mock_frappe.db.get_value = MagicMock(side_effect=_get_value)
		mock_frappe.db.exists = MagicMock(return_value=False)
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kw: (
				[SimpleNamespace(name="Acme Ltd", supplier_name="Acme Ltd")] if doctype == "Supplier" else []
			)
		)

		class _Settings(SimpleNamespace):
			def get(self, key, default=None):
				return getattr(self, key, default)

		ocr_dn = SimpleNamespace(
			supplier_name_ocr="ACME LTD",
			supplier="",
			supplier_match_status="",
			items=[
				SimpleNamespace(
					description_ocr="Widget",
					item_name="Widget",
					item_code="",
					match_status="",
				)
			],
		)
		from erpocr_integration.dn_api import _run_dn_matching

		_run_dn_matching(ocr_dn, _Settings(matching_threshold=1))

		assert ocr_dn.supplier_match_status == "Suggested"
		assert ocr_dn.items[0].item_code == "ITEM-A"
		assert ocr_dn.items[0].match_status == "Suggested"  # capped


# ---------------------------------------------------------------------------
# match_supplier_fuzzy
# ---------------------------------------------------------------------------


class TestMatchSupplierFuzzy:
	def _setup_suppliers(self, mock_frappe, suppliers, aliases=None):
		"""Helper to configure mock suppliers and aliases."""
		supplier_data = [SimpleNamespace(name=s[0], supplier_name=s[1]) for s in suppliers]
		alias_data = [SimpleNamespace(ocr_text=a[0], supplier=a[1]) for a in (aliases or [])]
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kwargs: supplier_data if doctype == "Supplier" else alias_data
		)

	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, status, _score = match_supplier_fuzzy("")
		assert result is None
		assert status == "Unmatched"

	def test_high_similarity_match(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			[
				("SUP-001", "Acme Trading (Pty) Ltd"),
			],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# Very similar — should match
		result, status, score = match_supplier_fuzzy("Acme Trading (Pty) Limited")
		assert result == "SUP-001"
		assert status == "Suggested"
		assert score >= 80

	def test_below_threshold(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			[
				("SUP-001", "Acme Trading (Pty) Ltd"),
			],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# Completely different — should not match
		result, status, _score = match_supplier_fuzzy("Cloudflare Inc", threshold=80)
		assert result is None
		assert status == "Unmatched"

	def test_best_of_multiple(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			[
				("SUP-001", "Acme Trading (Pty) Ltd"),
				("SUP-002", "Star Products (Pty) Ltd"),
			],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, _status, _score = match_supplier_fuzzy("Acme Trading Pty Ltd")
		assert result == "SUP-001"  # Closer match

	def test_alias_fuzzy_match(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			suppliers=[("SUP-001", "Official Name")],
			aliases=[("AcmeTrading", "SUP-001")],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		result, _status, _score = match_supplier_fuzzy("Acme Trading", threshold=50)
		# Should match via alias fuzzy if score is high enough
		assert result is not None

	def test_custom_threshold(self, mock_frappe):
		self._setup_suppliers(
			mock_frappe,
			[
				("SUP-001", "ABC Company"),
			],
		)
		from erpocr_integration.tasks.matching import match_supplier_fuzzy

		# With very low threshold, even poor matches succeed
		_result_low, _, _score_low = match_supplier_fuzzy("ABC Corp", threshold=30)
		# With high threshold, poor matches are rejected
		result_high, _, _score_high = match_supplier_fuzzy("XYZ Inc", threshold=95)
		assert result_high is None


# ---------------------------------------------------------------------------
# match_item_fuzzy
# ---------------------------------------------------------------------------


class TestMatchItemFuzzy:
	def _setup_items(self, mock_frappe, items, aliases=None):
		item_data = [SimpleNamespace(name=i[0], item_name=i[1]) for i in items]
		alias_data = [SimpleNamespace(ocr_text=a[0], item_code=a[1]) for a in (aliases or [])]
		mock_frappe.get_all = MagicMock(
			side_effect=lambda doctype, **kwargs: item_data if doctype == "Item" else alias_data
		)

	def test_empty_input(self, mock_frappe):
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, _status, _score = match_item_fuzzy("")
		assert result is None

	def test_high_similarity_match(self, mock_frappe):
		self._setup_items(
			mock_frappe,
			[
				("POP-050", "Premium Lollipops Assorted 50pk"),
			],
		)
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, status, _score = match_item_fuzzy("Premium Lollipops Assorted 50 pack")
		assert result == "POP-050"
		assert status == "Suggested"

	def test_below_threshold(self, mock_frappe):
		self._setup_items(
			mock_frappe,
			[
				("POP-050", "Premium Lollipops Assorted 50pk"),
			],
		)
		from erpocr_integration.tasks.matching import match_item_fuzzy

		result, _status, _score = match_item_fuzzy("Delivery Fee", threshold=80)
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
		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": "delivery",
					"item_code": "DELIVERY",
					"item_name": "Delivery Fee",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "Main - TC",
				}
			],
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee - Standard", company="Test Company")
		assert result is not None
		assert result["item_code"] == "DELIVERY"
		assert result["match_status"] == "Auto Matched"

	def test_supplier_specific_takes_priority(self, mock_frappe):
		self._setup_mappings(
			mock_frappe,
			supplier_mappings=[
				{
					"description_pattern": "delivery",
					"item_code": "DEL-STAR",
					"item_name": "Acme Trading Delivery",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "",
				}
			],
			generic_mappings=[
				{
					"description_pattern": "delivery",
					"item_code": "DELIVERY",
					"item_name": "Delivery Fee",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "",
				}
			],
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee", company="Test Company", supplier="SUP-001")
		assert result["item_code"] == "DEL-STAR"

	def test_no_match(self, mock_frappe):
		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": "subscription",
					"item_code": "SUB-001",
					"item_name": "Subscription",
					"expense_account": "5300 - Subscriptions - TC",
					"cost_center": "",
				}
			],
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee", company="Test Company")
		assert result is None

	def test_case_insensitive(self, mock_frappe):
		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": "delivery",
					"item_code": "DELIVERY",
					"item_name": "Delivery Fee",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "",
				}
			],
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("DELIVERY FEE", company="Test Company")
		assert result is not None
		assert result["item_code"] == "DELIVERY"


class TestSupplierDefaultMapping:
	"""Supplier-default ('*' wildcard) service mappings — code any line for a
	supplier whose descriptions vary too much to learn per-pattern."""

	def _setup(self, mock_frappe, supplier_mappings=None, generic_mappings=None, supplier_default=None):
		def side_effect(doctype, **kwargs):
			if doctype != "OCR Service Mapping":
				return []
			filters = kwargs.get("filters", {})
			if "description_pattern" in filters:  # Priority 3: the supplier-default query
				return [SimpleNamespace(**supplier_default)] if supplier_default else []
			if isinstance(filters.get("supplier"), str):  # Priority 1: supplier patterns
				return [SimpleNamespace(**m) for m in (supplier_mappings or [])]
			return [SimpleNamespace(**m) for m in (generic_mappings or [])]  # Priority 2: generic

		mock_frappe.get_all = MagicMock(side_effect=side_effect)

	def test_supplier_default_codes_unmatched_line(self, mock_frappe):
		"""'*' default codes a variable transport line that no pattern matches."""
		self._setup(
			mock_frappe,
			supplier_default={
				"description_pattern": "*",
				"item_code": "ITEM001",
				"item_name": "",
				"expense_account": "4150/001 - Transport - TC",
				"cost_center": "HO - TC",
			},
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item(
			"Star Pops LTT to Star Pops Plk Soneboy - HCH 371 L",
			company="Test Company",
			supplier="Louma",
		)
		assert result is not None
		assert result["item_code"] == "ITEM001"
		assert result["expense_account"] == "4150/001 - Transport - TC"
		assert result["match_status"] == "Auto Matched"

	def test_specific_pattern_beats_supplier_default(self, mock_frappe):
		"""A matching supplier pattern wins over the '*' default."""
		self._setup(
			mock_frappe,
			supplier_mappings=[
				{
					"description_pattern": "toll",
					"item_code": "TOLL-ITEM",
					"item_name": "Toll",
					"expense_account": "4160 - Tolls - TC",
					"cost_center": "",
				}
			],
			supplier_default={
				"description_pattern": "*",
				"item_code": "ITEM001",
				"item_name": "",
				"expense_account": "4150/001 - Transport - TC",
				"cost_center": "",
			},
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("N1 Toll Plaza", company="Test Company", supplier="Louma")
		assert result["item_code"] == "TOLL-ITEM"

	def test_no_supplier_default_returns_none(self, mock_frappe):
		"""No '*' row for the supplier → None (line goes to review)."""
		self._setup(mock_frappe)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Some novel line", company="Test Company", supplier="Louma")
		assert result is None

	def test_generic_pattern_beats_supplier_default(self, mock_frappe):
		"""Policy (deliberate): a recognised GENERIC pattern wins over the supplier
		'*' default. The default is a last resort for UNrecognised lines only — so
		adding it is purely additive and never disables existing generic patterns."""
		self._setup(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": "delivery",
					"item_code": "DELIVERY",
					"item_name": "Delivery Fee",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "",
				}
			],
			supplier_default={
				"description_pattern": "*",
				"item_code": "ITEM001",
				"item_name": "",
				"expense_account": "4150/001 - Transport - TC",
				"cost_center": "",
			},
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("Delivery Fee", company="Test Company", supplier="Louma")
		assert result["item_code"] == "DELIVERY"

	def test_default_not_applied_without_supplier(self, mock_frappe):
		"""The '*' default is supplier-scoped — never fires when no supplier is set."""
		self._setup(
			mock_frappe,
			supplier_default={
				"description_pattern": "*",
				"item_code": "ITEM001",
				"item_name": "",
				"expense_account": "X - TC",
				"cost_center": "",
			},
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("anything", company="Test Company")  # no supplier
		assert result is None

	def test_wildcard_row_not_matched_as_substring(self, mock_frappe):
		"""A '*' row returned in the pattern query must be skipped by the substring
		loop — it only ever acts as the explicit Priority-3 default."""
		self._setup(
			mock_frappe,
			supplier_mappings=[
				{
					"description_pattern": "*",
					"item_code": "ITEM001",
					"item_name": "",
					"expense_account": "X - TC",
					"cost_center": "",
				}
			],
			supplier_default=None,  # no explicit default row
		)
		from erpocr_integration.tasks.matching import match_service_item

		result = match_service_item("anything at all", company="Test Company", supplier="Louma")
		assert result is None


# ---------------------------------------------------------------------------
# End-to-end matching shape tests
# ---------------------------------------------------------------------------


class TestMatchingShapeEndToEnd:
	"""Prove that patterns saved by _extract_service_pattern match future
	invoice variants via match_service_item — covering punctuation, case,
	date, and formatting differences across invoices for the same service."""

	def _setup_mappings(self, mock_frappe, generic_mappings=None):
		"""Configure mock service mappings (generic only)."""

		def get_all_side_effect(doctype, **kwargs):
			if doctype != "OCR Service Mapping":
				return []
			filters = kwargs.get("filters", {})
			supplier_filter = filters.get("supplier")
			if isinstance(supplier_filter, str):
				return []  # No supplier-specific mappings
			return [SimpleNamespace(**m) for m in (generic_mappings or [])]

		mock_frappe.get_all = MagicMock(side_effect=get_all_side_effect)

	def test_subscription_different_months(self, mock_frappe):
		"""Pattern from 'Feb 2026' invoice matches 'March 2026' invoice."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)
		from erpocr_integration.tasks.matching import match_service_item

		# Simulate: user confirmed "Monthly Software Subscription Feb 2026"
		pattern = _extract_service_pattern("Monthly Software Subscription Feb 2026")
		assert "feb" not in pattern
		assert "2026" not in pattern

		# Store pattern as a service mapping
		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": pattern,
					"item_code": "SUB-001",
					"item_name": "Software Subscription",
					"expense_account": "5300 - Subscriptions - TC",
					"cost_center": "Main - TC",
				}
			],
		)

		# Future invoice with different month/year should match
		result = match_service_item("Monthly Software Subscription March 2027", company="Test Company")
		assert result is not None
		assert result["item_code"] == "SUB-001"

	def test_isp_rental_punctuation_variants(self, mock_frappe):
		"""Pattern from hyphenated ISP description matches unhyphenated variant."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)
		from erpocr_integration.tasks.matching import match_service_item

		# First invoice: "Afrihost VDSL Line Rental - February 2026"
		pattern = _extract_service_pattern("Afrihost VDSL Line Rental - February 2026")

		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": pattern,
					"item_code": "ISP-VDSL",
					"item_name": "VDSL Line Rental",
					"expense_account": "5400 - Internet - TC",
					"cost_center": "",
				}
			],
		)

		# Future invoice: different punctuation + month
		result = match_service_item("Afrihost VDSL Line Rental March 2026", company="Test Company")
		assert result is not None
		assert result["item_code"] == "ISP-VDSL"

		# Future invoice: with slashes instead of hyphens
		result2 = match_service_item("Afrihost VDSL Line Rental / April 2026", company="Test Company")
		assert result2 is not None
		assert result2["item_code"] == "ISP-VDSL"

	def test_delivery_with_date_variants(self, mock_frappe):
		"""Pattern from 'Delivery 15/01/2026' matches 'Delivery 22/03/2027'."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)
		from erpocr_integration.tasks.matching import match_service_item

		pattern = _extract_service_pattern("Delivery 15/01/2026")

		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": pattern,
					"item_code": "DELIVERY",
					"item_name": "Delivery Fee",
					"expense_account": "5200 - Delivery - TC",
					"cost_center": "",
				}
			],
		)

		# Different date
		result = match_service_item("Delivery 22/03/2027", company="Test Company")
		assert result is not None
		assert result["item_code"] == "DELIVERY"

		# ISO date format
		result2 = match_service_item("Delivery 2027-03-22", company="Test Company")
		assert result2 is not None
		assert result2["item_code"] == "DELIVERY"

	def test_pro_plan_date_range(self, mock_frappe):
		"""Pattern from 'Pro Plan - Jan 2026 to Feb 2026' matches different date range."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)
		from erpocr_integration.tasks.matching import match_service_item

		pattern = _extract_service_pattern("Pro Plan - Jan 2026 to Feb 2026")

		self._setup_mappings(
			mock_frappe,
			generic_mappings=[
				{
					"description_pattern": pattern,
					"item_code": "PRO-PLAN",
					"item_name": "Pro Plan",
					"expense_account": "5300 - Subscriptions - TC",
					"cost_center": "",
				}
			],
		)

		# Different months — "pro plan" is the core pattern
		result = match_service_item("Pro-Plan - Mar 2026 to Apr 2026", company="Test Company")
		assert result is not None
		assert result["item_code"] == "PRO-PLAN"

	def test_pattern_too_generic_falls_back(self):
		"""If stripping produces only stopwords, fallback to full description."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)

		# "For Jan 2026" → stripped → "for" (single stopword, zero content tokens) → should fall back
		result = _extract_service_pattern("For Jan 2026")
		# Should NOT be just "for" — should include more context
		assert result != "for"
		# Fallback should be the full normalized description
		assert "jan" in result or "for" in result

	def test_single_meaningful_word_not_rejected(self):
		"""A single meaningful word like 'delivery' is a valid pattern."""
		from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
			_extract_service_pattern,
		)

		# "Delivery 15/01/2026" → stripped → "delivery" (1 content token, should NOT fallback)
		result = _extract_service_pattern("Delivery 15/01/2026")
		assert result == "delivery"
