> **ARCHIVED — COMPLETE.** Shipped in v0.7 and live in v1.2.0 (see [CHANGELOG.md](../../../CHANGELOG.md)).
> This is the original point-in-time build plan; its unticked `- [ ]` checkboxes are historical
> and were never back-filled. Do not execute as a live plan. Current state of the feature:
> [docs/architecture.md](../../architecture.md) + [docs/implementation-patterns.md](../../implementation-patterns.md).

# Phase 7: Auto-Draft + Stats Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically create PI/PR drafts when extraction + matching produces high-confidence results, eliminating the manual "review and click Create" ceremony. Add stats tracking visible to owner/FM only.

**Architecture:** After matching completes in `gemini_process()`, a new `_attempt_auto_draft()` function checks confidence (supplier + all items must be alias/exact/service-mapping matched, NOT fuzzy/unmatched). If high-confidence, it auto-detects document type (default: PI), auto-links PO if one exists, and calls the existing `create_purchase_invoice()` method. Low-confidence falls back to current "Needs Review" flow unchanged. Stats are tracked via new fields on OCR Import and exposed through a role-gated API endpoint + Frappe page.

**Tech Stack:** Frappe/ERPNext v15, Python 3.11+, existing Gemini OCR pipeline

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `erpocr_integration/tasks/auto_draft.py` | **Create** | Confidence check, doc type detection, PO auto-link, orchestrator |
| `erpocr_integration/tests/test_auto_draft.py` | **Create** | Unit tests for all auto-draft functions |
| `erpocr_integration/api.py` | Modify (~5 lines) | Call `_attempt_auto_draft()` after matching in `gemini_process()` |
| `erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.json` | Modify | Add `enable_auto_draft` checkbox |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json` | Modify | Add `auto_drafted`, `auto_draft_skipped_reason` fields |
| `erpocr_integration/stats_api.py` | **Create** | Whitelisted stats aggregation endpoint |
| `erpocr_integration/tests/test_stats_api.py` | **Create** | Unit tests for stats endpoint |
| `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.json` | **Create** | Frappe page definition |
| `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.js` | **Create** | Stats dashboard UI |
| `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.html` | **Create** | Stats dashboard template |
| `erpocr_integration/hooks.py` | No change needed | Page roles defined in page JSON |

---

## Phase 7A: Auto-Draft

### Task 1: Add `enable_auto_draft` to OCR Settings

**Files:**
- Modify: `erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.json`

- [ ] **Step 1: Add the field to the DocType JSON**

In `ocr_settings.json`, add a new Section Break + checkbox after the matching section (after `matching_threshold`). Find the field with `"fieldname": "matching_threshold"` and add after it:

```json
{
  "fieldname": "auto_draft_section",
  "fieldtype": "Section Break",
  "label": "Auto-Draft",
  "collapsible": 1
},
{
  "fieldname": "enable_auto_draft",
  "fieldtype": "Check",
  "label": "Enable Auto-Draft",
  "default": "0",
  "description": "Automatically create PI/PR drafts when extraction produces high-confidence matches. Low-confidence records fall back to manual review."
}
```

- [ ] **Step 2: Verify JSON is valid**

Run: `python -c "import json; json.load(open('erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.json'))"`
Expected: No output (valid JSON)

- [ ] **Step 3: Commit**

```bash
git add erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.json
git commit -m "feat: add enable_auto_draft setting to OCR Settings"
```

---

### Task 2: Add tracking fields to OCR Import

**Files:**
- Modify: `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json`

- [ ] **Step 1: Add auto_drafted and auto_draft_skipped_reason fields**

In `ocr_import.json`, find the field with `"fieldname": "error_log"` (in the Status section). Add after it:

```json
{
  "fieldname": "auto_drafted",
  "fieldtype": "Check",
  "label": "Auto-Drafted",
  "read_only": 1,
  "default": "0",
  "description": "Set automatically when this record was auto-drafted without human intervention"
},
{
  "fieldname": "auto_draft_skipped_reason",
  "fieldtype": "Small Text",
  "label": "Auto-Draft Skip Reason",
  "read_only": 1,
  "hidden": 1,
  "description": "Why auto-draft was not attempted or failed"
}
```

- [ ] **Step 2: Verify JSON is valid**

Run: `python -c "import json; json.load(open('erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json'))"`
Expected: No output (valid JSON)

- [ ] **Step 3: Commit**

```bash
git add erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json
git commit -m "feat: add auto_drafted tracking fields to OCR Import"
```

---

### Task 3: Confidence assessment function

**Files:**
- Create: `erpocr_integration/tasks/auto_draft.py`
- Create: `erpocr_integration/tests/test_auto_draft.py`

- [ ] **Step 1: Write failing tests for `_is_high_confidence()`**

Create `erpocr_integration/tests/test_auto_draft.py`:

```python
"""Tests for auto-draft logic."""

from types import SimpleNamespace

import pytest

from erpocr_integration.tasks.auto_draft import _is_high_confidence


def _make_ocr_import(**overrides):
    """Create a minimal OCR Import-like object for testing."""
    defaults = dict(
        supplier="SUP-001",
        supplier_match_status="Auto Matched",
        items=[],
        status="Matched",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_item(**overrides):
    defaults = dict(
        item_code="ITEM-001",
        match_status="Auto Matched",
        description_ocr="Test item",
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
        is_high, reason = _is_high_confidence(doc)
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
        is_high, reason = _is_high_confidence(doc)
        assert is_high is False

    def test_low_confidence_no_supplier(self):
        doc = _make_ocr_import(supplier=None, items=[_make_item()])
        is_high, reason = _is_high_confidence(doc)
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
        is_high, reason = _is_high_confidence(doc)
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
        is_high, reason = _is_high_confidence(doc)
        assert is_high is False

    def test_all_items_service_mapped(self):
        """Service mapping returns 'Auto Matched' — should be high confidence."""
        doc = _make_ocr_import(
            items=[_make_item(match_status="Auto Matched")],
        )
        is_high, reason = _is_high_confidence(doc)
        assert is_high is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_auto_draft.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` (module doesn't exist yet)

- [ ] **Step 3: Implement `_is_high_confidence()`**

Create `erpocr_integration/tasks/auto_draft.py`:

```python
"""Auto-draft logic for high-confidence OCR Imports.

When extraction + matching produces high-confidence results (alias/exact matches,
not fuzzy), automatically creates the PI/PR draft — eliminating the manual
"review and click Create" ceremony.
"""

# High-confidence match statuses (NOT "Suggested" or "Unmatched")
_HIGH_CONFIDENCE_STATUSES = frozenset({"Auto Matched", "Confirmed"})


def _is_high_confidence(ocr_import) -> tuple[bool, str]:
    """Check if an OCR Import has high-confidence matches suitable for auto-draft.

    Returns:
        (is_high_confidence, reason_if_not)
    """
    # Supplier must be resolved with high confidence
    if not ocr_import.supplier:
        return False, "No supplier matched"
    if ocr_import.supplier_match_status not in _HIGH_CONFIDENCE_STATUSES:
        return False, f"Supplier match is '{ocr_import.supplier_match_status}' (needs alias or exact)"

    # Must have at least one item
    if not ocr_import.items:
        return False, "No items extracted"

    # All items must be high-confidence matched
    for item in ocr_import.items:
        if item.match_status not in _HIGH_CONFIDENCE_STATUSES:
            return False, f"Item '{item.description_ocr or '?'}' match is '{item.match_status}'"
        if not item.item_code:
            return False, f"Item '{item.description_ocr or '?'}' has no item_code"

    return True, ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestIsHighConfidence -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Lint**

Run: `ruff check erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py && ruff format erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py`

- [ ] **Step 6: Commit**

```bash
git add erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git commit -m "feat: add confidence assessment for auto-draft"
```

---

### Task 4: PO auto-linking

**Files:**
- Modify: `erpocr_integration/tasks/auto_draft.py`
- Modify: `erpocr_integration/tests/test_auto_draft.py`

- [ ] **Step 1: Write failing tests for `_auto_link_purchase_order()`**

Add to `test_auto_draft.py`:

```python
from erpocr_integration.tasks.auto_draft import _auto_link_purchase_order


class TestAutoLinkPurchaseOrder:
    def test_links_po_when_all_items_match(self, mock_frappe):
        doc = _make_ocr_import(
            supplier="SUP-001",
            company="Test Co",
            items=[_make_item(item_code="ITEM-A"), _make_item(item_code="ITEM-B")],
        )
        # Mock: one open PO with matching items
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
            items=[_make_item(item_code="ITEM-A")],
        )
        mock_frappe.get_list.return_value = [
            SimpleNamespace(name="PO-001", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
        ]
        mock_frappe.get_doc.return_value = SimpleNamespace(
            items=[SimpleNamespace(item_code="ITEM-X")]  # Different item
        )

        linked = _auto_link_purchase_order(doc)

        assert linked is False

    def test_no_link_when_no_supplier(self, mock_frappe):
        doc = _make_ocr_import(supplier=None, company="Test Co", items=[_make_item()])

        linked = _auto_link_purchase_order(doc)

        assert linked is False

    def test_picks_po_with_best_item_coverage(self, mock_frappe):
        """When multiple POs exist, pick the one where all OCR items match."""
        doc = _make_ocr_import(
            supplier="SUP-001",
            company="Test Co",
            items=[_make_item(item_code="ITEM-A"), _make_item(item_code="ITEM-B")],
        )
        mock_frappe.get_list.return_value = [
            SimpleNamespace(name="PO-001", transaction_date="2026-01-15", grand_total=500, status="To Bill"),
            SimpleNamespace(name="PO-002", transaction_date="2026-01-01", grand_total=1000, status="To Bill"),
        ]

        def get_doc_handler(doctype, name=None):
            if name == "PO-001":
                return SimpleNamespace(items=[SimpleNamespace(item_code="ITEM-A")])  # Partial
            if name == "PO-002":
                return SimpleNamespace(items=[
                    SimpleNamespace(item_code="ITEM-A"),
                    SimpleNamespace(item_code="ITEM-B"),
                ])  # Full match
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

        assert linked is True  # Already linked
        assert doc.purchase_order == "PO-EXISTING"  # Unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAutoLinkPurchaseOrder -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_auto_link_purchase_order()`**

Add to `erpocr_integration/tasks/auto_draft.py`:

```python
import frappe


def _auto_link_purchase_order(ocr_import) -> bool:
    """Attempt to find and link an open PO for this OCR Import.

    Searches open POs by supplier + company, picks the one where all OCR item_codes
    appear in PO items. Sets `ocr_import.purchase_order` if found.

    Returns:
        True if a PO was linked (or already linked), False otherwise.
    """
    if ocr_import.purchase_order:
        return True  # Already linked

    if not ocr_import.supplier or not ocr_import.company:
        return False

    ocr_item_codes = {item.item_code for item in ocr_import.items if item.item_code}
    if not ocr_item_codes:
        return False

    # Find open POs for this supplier
    open_pos = frappe.get_list(
        "Purchase Order",
        filters={
            "supplier": ocr_import.supplier,
            "company": ocr_import.company,
            "docstatus": 1,
            "status": ["in", ["To Receive and Bill", "To Receive", "To Bill"]],
        },
        fields=["name", "transaction_date", "grand_total", "status"],
        order_by="transaction_date desc",
        limit_page_length=20,
        ignore_permissions=True,
    )

    if not open_pos:
        return False

    # Find PO where all OCR items have matching PO items
    best_po = None
    for po in open_pos:
        po_doc = frappe.get_doc("Purchase Order", po.name)
        po_item_codes = {item.item_code for item in po_doc.items}

        if ocr_item_codes.issubset(po_item_codes):
            best_po = po.name
            break  # First full match wins (most recent due to ordering)

    if best_po:
        ocr_import.purchase_order = best_po
        return True

    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAutoLinkPurchaseOrder -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
ruff format erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git add erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git commit -m "feat: add PO auto-linking for auto-draft"
```

---

### Task 5: Document type auto-detection

**Files:**
- Modify: `erpocr_integration/tasks/auto_draft.py`
- Modify: `erpocr_integration/tests/test_auto_draft.py`

- [ ] **Step 1: Write failing tests for `_auto_detect_document_type()`**

Add to `test_auto_draft.py`:

```python
from erpocr_integration.tasks.auto_draft import _auto_detect_document_type


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
```

Note: For the initial rollout, auto-draft always creates PI. PR requires all stock items + warehouse config. JE requires explicit expense accounts + credit account. Both are too risky for auto-draft v1. This function exists as the extension point for future refinement.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAutoDetectDocumentType -v`
Expected: FAIL

- [ ] **Step 3: Implement `_auto_detect_document_type()`**

Add to `auto_draft.py`:

```python
def _auto_detect_document_type(ocr_import) -> str:
    """Auto-detect the appropriate document type for this OCR Import.

    Current logic: always returns Purchase Invoice. PI is the safest default
    because it accepts unmatched items via default_item, doesn't require
    warehouse config, and is the most common document type.

    Future: could detect PR (all stock items + PO) or JE (expense receipts).
    """
    return "Purchase Invoice"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAutoDetectDocumentType -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git commit -m "feat: add document type auto-detection for auto-draft"
```

---

### Task 6: Auto-draft orchestrator

**Files:**
- Modify: `erpocr_integration/tasks/auto_draft.py`
- Modify: `erpocr_integration/tests/test_auto_draft.py`

- [ ] **Step 1: Write failing tests for `attempt_auto_draft()`**

Add to `test_auto_draft.py`:

```python
from unittest.mock import MagicMock, patch

from erpocr_integration.tasks.auto_draft import attempt_auto_draft


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
        assert "Needs Review" in (doc.auto_draft_skipped_reason or "")
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
        assert doc.auto_draft_skipped_reason != ""
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
        mock_frappe.get_doc.return_value = SimpleNamespace(
            items=[SimpleNamespace(item_code="ITEM-A")]
        )

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

        assert doc.auto_draft_skipped_reason
        assert "supplier" in doc.auto_draft_skipped_reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAttemptAutoDraft -v`
Expected: FAIL

- [ ] **Step 3: Implement `attempt_auto_draft()`**

Add to `auto_draft.py`:

```python
def attempt_auto_draft(ocr_import, settings) -> bool:
    """Attempt to auto-draft a document from a high-confidence OCR Import.

    Called after matching completes in gemini_process(). If confidence is high,
    sets document_type, links PO if possible, saves, and calls the create method.
    Falls back gracefully on any error — the record stays at its current status
    (Matched/Needs Review) and the user can handle it manually.

    Returns:
        True if auto-draft succeeded, False otherwise.
    """
    if not getattr(settings, "enable_auto_draft", 0):
        return False

    # Don't auto-draft if a document already exists
    if ocr_import.purchase_invoice or ocr_import.purchase_receipt or ocr_import.journal_entry:
        return False

    # Check confidence
    is_high, reason = _is_high_confidence(ocr_import)
    if not is_high:
        ocr_import.auto_draft_skipped_reason = reason
        return False

    # Gate on "Matched" status — _update_status() already validates that
    # non-stock items have expense_account, etc. If the record didn't reach
    # "Matched" after save, it needs human attention even if matches look good.
    if ocr_import.status != "Matched":
        ocr_import.auto_draft_skipped_reason = (
            f"Status is '{ocr_import.status}' (requires 'Matched')"
        )
        return False

    try:
        # Auto-link PO if possible (sets ocr_import.purchase_order)
        _auto_link_purchase_order(ocr_import)

        # Auto-detect document type
        doc_type = _auto_detect_document_type(ocr_import)
        ocr_import.document_type = doc_type
        ocr_import.auto_drafted = 1

        # Save to persist document_type + PO link + auto_drafted flag
        # (create_purchase_invoice checks these fields)
        ocr_import.save(ignore_permissions=True)

        # Create the document
        if doc_type == "Purchase Invoice":
            ocr_import.create_purchase_invoice()
        elif doc_type == "Purchase Receipt":
            ocr_import.create_purchase_receipt()

        return True

    except Exception as e:
        # Fall back gracefully — record stays at Matched/Needs Review
        ocr_import.auto_draft_skipped_reason = f"Auto-draft failed: {e}"
        ocr_import.document_type = ""
        ocr_import.auto_drafted = 0
        try:
            ocr_import.save(ignore_permissions=True)
        except Exception:
            pass  # Best-effort — don't mask the original error
        frappe.log_error(
            title="Auto-Draft Failed",
            message=f"Auto-draft failed for {ocr_import.name}: {e}\n{frappe.get_traceback()}",
        )
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_auto_draft.py::TestAttemptAutoDraft -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest erpocr_integration/tests/ -q`
Expected: 481+ passed

- [ ] **Step 6: Lint and commit**

```bash
ruff check erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
ruff format erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git add erpocr_integration/tasks/auto_draft.py erpocr_integration/tests/test_auto_draft.py
git commit -m "feat: add auto-draft orchestrator with graceful fallback"
```

---

### Task 7: Hook auto-draft into `gemini_process()`

**Files:**
- Modify: `erpocr_integration/api.py` (lines ~350-360, after save/insert loop)

- [ ] **Step 1: Write integration test**

Add to `test_auto_draft.py`:

```python
class TestGeminiProcessIntegration:
    """Test that auto-draft is called at the right point in the pipeline."""

    def test_auto_draft_called_for_each_ocr_import(self, mock_frappe):
        """Patch attempt_auto_draft at the api module level and verify it's called
        once per OCR Import after extraction + matching completes."""
        from erpocr_integration import api as ocr_api

        # Mock the Gemini extraction to return a single invoice
        with patch.object(ocr_api, "extract_invoice_data", return_value=[{
            "header_fields": {"supplier_name": "Test", "invoice_number": "INV-1",
                              "invoice_date": "2026-01-01", "total_amount": 100},
            "line_items": [{"description": "Item A", "quantity": 1, "unit_price": 100, "amount": 100}],
        }]):
            # Mock settings with auto_draft enabled
            settings_mock = _make_settings(
                enable_auto_draft=1, default_company="Test Co",
                default_tax_template=None, non_vat_tax_template=None,
                matching_threshold=80,
            )
            mock_frappe.get_cached_doc.return_value = settings_mock

            # Mock the OCR Import placeholder doc
            placeholder = MagicMock()
            placeholder.name = "OCR-IMP-TEST"
            placeholder.email_message_id = None
            placeholder.drive_file_id = None
            placeholder.drive_retry_count = 0
            mock_frappe.get_doc.return_value = placeholder

            with patch.object(ocr_api, "_run_matching"):
                with patch("erpocr_integration.api.attempt_auto_draft") as mock_auto:
                    mock_auto.return_value = True

                    ocr_api.gemini_process(
                        pdf_content=b"fake",
                        filename="test.pdf",
                        ocr_import_name="OCR-IMP-TEST",
                        source_type="Gemini Manual Upload",
                        uploaded_by="Administrator",
                    )

                    mock_auto.assert_called_once()
```

- [ ] **Step 2: Add auto-draft call to `gemini_process()`**

In `erpocr_integration/api.py`, find the block after the invoice processing loop (after `all_ocr_import_names.append(ocr_import.name)`), right before `frappe.db.commit()`. Add the auto-draft attempt:

```python
		# --- existing code ---
		all_ocr_import_names = []
		for idx, extracted_data in enumerate(invoice_list):
			# ... existing processing code ...
			all_ocr_import_names.append(ocr_import.name)

		# Auto-draft: attempt to create documents for high-confidence records
		if getattr(settings, "enable_auto_draft", 0):
			from erpocr_integration.tasks.auto_draft import attempt_auto_draft

			for doc_name in all_ocr_import_names:
				try:
					ocr_doc = frappe.get_doc("OCR Import", doc_name)
					attempt_auto_draft(ocr_doc, settings)
				except Exception:
					frappe.log_error(
						title="Auto-Draft Error",
						message=f"Auto-draft failed for {doc_name}\n{frappe.get_traceback()}",
					)

		frappe.db.commit()  # nosemgrep
		# --- existing code continues ---
```

Note: We reload each doc with `frappe.get_doc()` because the doc was already saved in the loop. The `attempt_auto_draft` function handles its own save + create calls.

- [ ] **Step 3: Update the realtime message for auto-drafted records**

In the same function, find the realtime publish at the end. Update the message to reflect auto-draft:

```python
		# Publish realtime update
		ocr_import_first = frappe.get_doc("OCR Import", ocr_import_name)
		if ocr_import_first.auto_drafted:
			msg = "Auto-drafted! Document created automatically. Please review and submit."
			if invoice_count > 1:
				msg = f"{invoice_count} invoices extracted. High-confidence records auto-drafted."
		else:
			msg = "Extraction complete! Please review and confirm matches."
			if invoice_count > 1:
				msg = f"Extraction complete! {invoice_count} invoices created. Please review."
```

- [ ] **Step 4: Run full test suite**

Run: `pytest erpocr_integration/tests/ -q`
Expected: All tests pass (existing tests unaffected because `enable_auto_draft` defaults to 0)

- [ ] **Step 5: Lint and commit**

```bash
ruff check erpocr_integration/api.py
ruff format erpocr_integration/api.py
git add erpocr_integration/api.py erpocr_integration/tests/test_auto_draft.py
git commit -m "feat: hook auto-draft into gemini_process pipeline"
```

---

## Phase 7B: Stats Dashboard

### Task 8: Stats API endpoint

**Files:**
- Create: `erpocr_integration/stats_api.py`
- Create: `erpocr_integration/tests/test_stats_api.py`

- [ ] **Step 1: Write tests for `get_ocr_stats()`**

Create `erpocr_integration/tests/test_stats_api.py`:

```python
"""Tests for OCR stats API endpoint."""

from types import SimpleNamespace
from datetime import datetime

import pytest

from erpocr_integration.stats_api import _compute_stats


class TestComputeStats:
    def test_touchless_rate_all_auto_drafted(self):
        records = [
            {"status": "Draft Created", "auto_drafted": 1, "source_type": "Gemini Drive Scan"},
            {"status": "Completed", "auto_drafted": 1, "source_type": "Gemini Drive Scan"},
        ]
        stats = _compute_stats(records)
        assert stats["touchless_draft_rate"] == 100.0

    def test_touchless_rate_mixed(self):
        records = [
            {"status": "Draft Created", "auto_drafted": 1, "source_type": "Gemini Drive Scan"},
            {"status": "Needs Review", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
            {"status": "Completed", "auto_drafted": 0, "source_type": "Gemini Email"},
            {"status": "Completed", "auto_drafted": 1, "source_type": "Gemini Email"},
        ]
        stats = _compute_stats(records)
        assert stats["touchless_draft_rate"] == 50.0  # 2 of 4

    def test_touchless_rate_zero_records(self):
        stats = _compute_stats([])
        assert stats["touchless_draft_rate"] == 0.0

    def test_exception_rate(self):
        records = [
            {"status": "Needs Review", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
            {"status": "Completed", "auto_drafted": 1, "source_type": "Gemini Drive Scan"},
            {"status": "Matched", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
        ]
        stats = _compute_stats(records)
        # 2 of 3 need manual intervention (Needs Review + Matched without auto_drafted)
        assert stats["exception_rate"] == pytest.approx(66.67, abs=0.1)

    def test_volume_by_source(self):
        records = [
            {"status": "Completed", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
            {"status": "Completed", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
            {"status": "Completed", "auto_drafted": 0, "source_type": "Gemini Email"},
        ]
        stats = _compute_stats(records)
        assert stats["by_source"]["Gemini Drive Scan"] == 2
        assert stats["by_source"]["Gemini Email"] == 1

    def test_status_breakdown(self):
        records = [
            {"status": "Completed", "auto_drafted": 1, "source_type": "Gemini Drive Scan"},
            {"status": "Needs Review", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
            {"status": "Error", "auto_drafted": 0, "source_type": "Gemini Drive Scan"},
        ]
        stats = _compute_stats(records)
        assert stats["by_status"]["Completed"] == 1
        assert stats["by_status"]["Needs Review"] == 1
        assert stats["by_status"]["Error"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_stats_api.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement stats API**

Create `erpocr_integration/stats_api.py`:

```python
"""OCR stats API — aggregation endpoint for the stats dashboard.

Role-gated to System Manager. Not visible to regular accounts users.
"""

import frappe
from frappe import _


@frappe.whitelist()
def get_ocr_stats(from_date=None, to_date=None):
    """Return OCR processing statistics for the stats dashboard.

    Args:
        from_date: Start date filter (default: 90 days ago)
        to_date: End date filter (default: today)
    """
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Managers can view OCR stats."))

    if not from_date:
        from_date = frappe.utils.add_days(frappe.utils.today(), -90)
    if not to_date:
        to_date = frappe.utils.today()

    records = frappe.get_all(
        "OCR Import",
        filters={"creation": ["between", [from_date, to_date]]},
        fields=[
            "name", "status", "auto_drafted", "source_type",
            "supplier", "supplier_match_status", "creation",
            "auto_draft_skipped_reason",
        ],
        limit_page_length=0,
        ignore_permissions=True,
    )

    stats = _compute_stats(records)
    stats["from_date"] = str(from_date)
    stats["to_date"] = str(to_date)
    return stats


def _compute_stats(records: list[dict]) -> dict:
    """Compute aggregate stats from a list of OCR Import records."""
    total = len(records)
    if total == 0:
        return {
            "total": 0,
            "touchless_draft_rate": 0.0,
            "exception_rate": 0.0,
            "by_status": {},
            "by_source": {},
            "auto_drafted_count": 0,
            "manual_count": 0,
        }

    auto_drafted = sum(1 for r in records if r.get("auto_drafted"))
    # Exception = anything that needs/needed manual intervention
    # (Needs Review, Matched without auto_drafted, Error)
    exceptions = sum(
        1 for r in records
        if not r.get("auto_drafted")
        and r.get("status") in ("Needs Review", "Matched", "Error")
    )

    by_status = {}
    by_source = {}
    for r in records:
        status = r.get("status", "Unknown")
        by_status[status] = by_status.get(status, 0) + 1
        source = r.get("source_type", "Unknown")
        by_source[source] = by_source.get(source, 0) + 1

    return {
        "total": total,
        "touchless_draft_rate": round(auto_drafted / total * 100, 1) if total else 0.0,
        "exception_rate": round(exceptions / total * 100, 1) if total else 0.0,
        "by_status": by_status,
        "by_source": by_source,
        "auto_drafted_count": auto_drafted,
        "manual_count": total - auto_drafted,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_stats_api.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check erpocr_integration/stats_api.py erpocr_integration/tests/test_stats_api.py
ruff format erpocr_integration/stats_api.py erpocr_integration/tests/test_stats_api.py
git add erpocr_integration/stats_api.py erpocr_integration/tests/test_stats_api.py
git commit -m "feat: add OCR stats API endpoint"
```

---

### Task 9: Stats page (Frappe page)

**Files:**
- Create: `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.json`
- Create: `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.js`
- Create: `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.html`

- [ ] **Step 1: Create page definition JSON**

Create `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.json`:

```json
{
  "content": null,
  "creation": "2026-03-27 00:00:00.000000",
  "docstatus": 0,
  "doctype": "Page",
  "icon": "chart",
  "idx": 0,
  "modified": "2026-03-27 00:00:00.000000",
  "modified_by": "Administrator",
  "module": "ERPNext OCR",
  "name": "ocr-stats",
  "owner": "Administrator",
  "page_name": "ocr-stats",
  "roles": [
    {
      "role": "System Manager"
    }
  ],
  "standard": "Yes",
  "system_page": 0,
  "title": "OCR Stats"
}
```

- [ ] **Step 2: Create page JS**

Create `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.js`:

```javascript
frappe.pages['ocr-stats'].on_page_load = function(wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'OCR Processing Stats',
		single_column: true
	});

	page.main.html(frappe.render_template('ocr_stats'));

	// Date range controls
	page.add_field({
		fieldname: 'from_date',
		label: __('From'),
		fieldtype: 'Date',
		default: frappe.datetime.add_days(frappe.datetime.nowdate(), -90),
		change: function() { load_stats(page); }
	});

	page.add_field({
		fieldname: 'to_date',
		label: __('To'),
		fieldtype: 'Date',
		default: frappe.datetime.nowdate(),
		change: function() { load_stats(page); }
	});

	load_stats(page);
};

function load_stats(page) {
	let from_date = page.fields_dict.from_date.get_value();
	let to_date = page.fields_dict.to_date.get_value();

	frappe.call({
		method: 'erpocr_integration.stats_api.get_ocr_stats',
		args: { from_date: from_date, to_date: to_date },
		callback: function(r) {
			if (r.message) {
				render_stats(page, r.message);
			}
		}
	});
}

function render_stats(page, stats) {
	let $main = page.main;

	// KPI cards
	$main.find('.stat-total').text(stats.total);
	$main.find('.stat-touchless').text(stats.touchless_draft_rate + '%');
	$main.find('.stat-exception').text(stats.exception_rate + '%');
	$main.find('.stat-auto-drafted').text(stats.auto_drafted_count);
	$main.find('.stat-manual').text(stats.manual_count);

	// Status breakdown
	let status_html = '';
	let status_order = ['Completed', 'Draft Created', 'Matched', 'Needs Review', 'Error', 'No Action', 'Pending'];
	let status_colors = {
		'Completed': 'green', 'Draft Created': 'blue', 'Matched': 'cyan',
		'Needs Review': 'orange', 'Error': 'red', 'No Action': 'grey', 'Pending': 'yellow'
	};
	status_order.forEach(function(s) {
		let count = stats.by_status[s] || 0;
		if (count > 0) {
			let color = status_colors[s] || 'grey';
			status_html += '<div class="stat-row"><span class="indicator-pill ' + color + '">' +
				s + '</span><strong>' + count + '</strong></div>';
		}
	});
	$main.find('.status-breakdown').html(status_html);

	// Source breakdown
	let source_html = '';
	Object.keys(stats.by_source || {}).forEach(function(src) {
		source_html += '<div class="stat-row"><span>' + src + '</span><strong>' +
			stats.by_source[src] + '</strong></div>';
	});
	$main.find('.source-breakdown').html(source_html);
}
```

- [ ] **Step 3: Create page HTML template**

Create `erpocr_integration/erpnext_ocr/page/ocr_stats/ocr_stats.html`:

```html
<div class="ocr-stats-page" style="padding: 15px;">
	<div class="row" style="margin-bottom: 20px;">
		<div class="col-sm-2">
			<div class="stat-card text-center" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px;">
				<div class="stat-total" style="font-size: 2em; font-weight: bold;">-</div>
				<div class="text-muted">Total Processed</div>
			</div>
		</div>
		<div class="col-sm-2">
			<div class="stat-card text-center" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px;">
				<div class="stat-touchless" style="font-size: 2em; font-weight: bold; color: var(--green-500);">-</div>
				<div class="text-muted">Touchless Draft Rate</div>
			</div>
		</div>
		<div class="col-sm-2">
			<div class="stat-card text-center" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px;">
				<div class="stat-exception" style="font-size: 2em; font-weight: bold; color: var(--orange-500);">-</div>
				<div class="text-muted">Exception Rate</div>
			</div>
		</div>
		<div class="col-sm-2">
			<div class="stat-card text-center" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px;">
				<div class="stat-auto-drafted" style="font-size: 2em; font-weight: bold; color: var(--blue-500);">-</div>
				<div class="text-muted">Auto-Drafted</div>
			</div>
		</div>
		<div class="col-sm-2">
			<div class="stat-card text-center" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px;">
				<div class="stat-manual" style="font-size: 2em; font-weight: bold;">-</div>
				<div class="text-muted">Manual</div>
			</div>
		</div>
	</div>
	<div class="row">
		<div class="col-sm-6">
			<h5>Status Breakdown</h5>
			<div class="status-breakdown" style="padding: 10px;">Loading...</div>
		</div>
		<div class="col-sm-6">
			<h5>By Source</h5>
			<div class="source-breakdown" style="padding: 10px;">Loading...</div>
		</div>
	</div>
</div>

<style>
	.stat-row {
		display: flex;
		justify-content: space-between;
		padding: 6px 0;
		border-bottom: 1px solid var(--border-color);
	}
</style>
```

- [ ] **Step 4: Verify page files exist**

Run: `ls -la erpocr_integration/erpnext_ocr/page/ocr_stats/`
Expected: Three files (`.json`, `.js`, `.html`)

- [ ] **Step 5: Commit**

```bash
git add erpocr_integration/erpnext_ocr/page/ocr_stats/
git add erpocr_integration/stats_api.py erpocr_integration/tests/test_stats_api.py
git commit -m "feat: add OCR stats dashboard page"
```

---

### Task 10: Version bump + final validation

**Files:**
- Modify: `erpocr_integration/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest erpocr_integration/tests/ -v`
Expected: All tests pass (481 existing + ~26 new = ~507 total)

- [ ] **Step 2: Lint entire codebase**

Run: `ruff check erpocr_integration/ && ruff format --check erpocr_integration/`
Expected: All checks passed

- [ ] **Step 3: Version bump**

Update `erpocr_integration/__init__.py`:
```python
__version__ = "0.7.0"
```

Update README.md badge:
```html
<img src="https://img.shields.io/badge/version-0.7.0-green" alt="Version 0.7.0">
```

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git commit -m "feat: Phase 7 — auto-draft + stats dashboard (v0.7.0)

Auto-Draft: high-confidence OCR Imports (alias/exact matches) automatically
create PI drafts. Low-confidence falls back to manual review (unchanged).
Stats: role-gated dashboard showing touchless rate, exception rate, volume."

git push
```

---

## Rollout Checklist

After pushing v0.7.0:

1. **Deploy to both sites**: `bench get-app --upgrade erpocr_integration` + `bench migrate` + `bench restart`
2. **Run migrate** to create new fields (`auto_drafted`, `auto_draft_skipped_reason`, `enable_auto_draft`)
3. **Enable on primary site first**: OCR Settings > Auto-Draft section > check "Enable Auto-Draft"
4. **Monitor for 1 week**: Check stats page at `/app/ocr-stats` — watch touchless rate
5. **Enable on secondary site**: Only after primary validates (secondary needs alias learning first)
6. **Expected touchless rate**: Primary ~50-70% (with mature aliases), Secondary ~10-20% initially

## Future Extensions (not in this plan)

- Auto-Submit: opt-in per supplier rule (trusted + PO-backed + amount tolerance)
- DN auto-draft: auto-create PR when PO found + all items matched
- Fleet auto-draft: auto-create PI when vehicle matched + supplier resolved
- Monthly stats email to FM
- Alias seeding: export primary site aliases to secondary for shared suppliers
