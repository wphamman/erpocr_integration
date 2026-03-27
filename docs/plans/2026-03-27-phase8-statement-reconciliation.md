# Phase 8: Statement Reconciliation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically detect supplier statements in the Drive scan folder, extract transaction lines via Gemini, and reconcile them against ERPNext Purchase Invoices — flagging mismatches, missing invoices, and unreconciled items for review.

**Architecture:** A Gemini-based document classifier runs on each file before the existing pipeline. Invoices route to the unchanged `gemini_process()`. Statements route to a new `statement_gemini_process()` that extracts transaction lines into an OCR Statement DocType, then auto-reconciles each line against ERPNext PIs by supplier + bill_no. A reverse check finds ERPNext PIs not mentioned on the statement. Danell sees a color-coded reconciliation view and handles only the exceptions.

**Tech Stack:** Frappe/ERPNext v15, Python 3.11+, Gemini 2.5 Flash API, existing OCR pipeline

---

## Review Amendments (apply throughout)

1. **Retry cap on OCR Statement**: Same `MAX_DRIVE_RETRIES=3` behavior as OCR Import. The `_process_statement_file()` dedup check must count retries and give up after 3 failures — not just skip non-error records.

2. **Reverse check gated on trustworthy period**: Only add "Not in Statement" rows when both `period_from` AND `period_to` are set. If either is missing, skip the reverse check and log a warning on the OCR Statement (e.g., set a `reverse_check_skipped` flag or add a comment).

3. **Normalize references before matching**: Use `normalize_for_matching()` from `erpocr_integration/tasks/matching.py` when comparing statement `reference` to `Purchase Invoice.bill_no`. This handles `INV/00123` vs `INV-00123` vs `INV 00123` variations.

4. **No new scheduler hook**: The existing `poll_drive_scan_folder` already covers the shared folder. Classification routes inside `_process_scan_file()`. Only `doctype_js` needs updating in hooks.py.

5. **Log classifier result**: Store classification result + confidence on the OCR Import/Statement record for audit trail. Add `classification_result` and `classification_confidence` fields to OCR Import DocType (Data + Float, read_only, hidden). When classifier runs, set these fields on whichever placeholder is created.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `erpocr_integration/tasks/classify_document.py` | **Create** | Gemini-based document classification (invoice vs statement) |
| `erpocr_integration/tests/test_classify_document.py` | **Create** | Classification tests |
| `erpocr_integration/tasks/gemini_extract.py` | Modify | Add `extract_statement_data()` with statement-specific prompt/schema |
| `erpocr_integration/tests/test_gemini_extract.py` | Modify | Add statement extraction tests |
| `erpocr_integration/statement_api.py` | **Create** | `statement_gemini_process()` background job + reconciliation |
| `erpocr_integration/tests/test_statement_api.py` | **Create** | Statement pipeline + reconciliation tests |
| `erpocr_integration/tasks/reconcile.py` | **Create** | `reconcile_statement()` matching logic |
| `erpocr_integration/tests/test_reconcile.py` | **Create** | Reconciliation unit tests |
| `erpocr_integration/erpnext_ocr/doctype/ocr_statement/` | **Create** | OCR Statement DocType (header) |
| `erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/` | **Create** | OCR Statement Item DocType (child table) |
| `erpocr_integration/public/js/ocr_statement.js` | **Create** | Client script for OCR Statement form |
| `erpocr_integration/tasks/drive_integration.py` | Modify | Add classification to `_process_scan_file()` |
| `erpocr_integration/hooks.py` | Modify | Add `doctype_js` entry for OCR Statement (no new scheduler needed) |

---

## Task 1: Document Classification Function

**Files:**
- Create: `erpocr_integration/tasks/classify_document.py`
- Create: `erpocr_integration/tests/test_classify_document.py`

- [ ] **Step 1: Write failing tests**

Create `erpocr_integration/tests/test_classify_document.py`:

```python
"""Tests for Gemini-based document classification."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.tasks.classify_document import classify_document


class TestClassifyDocument:
    def test_classifies_invoice(self, mock_frappe):
        """Gemini returns 'invoice' classification."""
        mock_settings = SimpleNamespace(
            gemini_model="gemini-2.5-flash",
        )
        mock_settings.get_password = MagicMock(return_value="fake-api-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
            mock_api.return_value = {"document_type": "invoice", "confidence": 0.95}

            result = classify_document(b"fake-pdf-content", "invoice.pdf")

            assert result == "invoice"

    def test_classifies_statement(self, mock_frappe):
        """Gemini returns 'statement' classification."""
        mock_settings = SimpleNamespace(
            gemini_model="gemini-2.5-flash",
        )
        mock_settings.get_password = MagicMock(return_value="fake-api-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
            mock_api.return_value = {"document_type": "statement", "confidence": 0.90}

            result = classify_document(b"fake-pdf-content", "statement.pdf")

            assert result == "statement"

    def test_defaults_to_invoice_on_unknown(self, mock_frappe):
        """Unknown classification defaults to invoice (safe fallback)."""
        mock_settings = SimpleNamespace(
            gemini_model="gemini-2.5-flash",
        )
        mock_settings.get_password = MagicMock(return_value="fake-api-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
            mock_api.return_value = {"document_type": "other", "confidence": 0.5}

            result = classify_document(b"fake-pdf-content", "unknown.pdf")

            assert result == "invoice"

    def test_defaults_to_invoice_on_api_error(self, mock_frappe):
        """API error defaults to invoice (don't break existing pipeline)."""
        mock_settings = SimpleNamespace(
            gemini_model="gemini-2.5-flash",
        )
        mock_settings.get_password = MagicMock(return_value="fake-api-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
            mock_api.side_effect = Exception("API error")

            result = classify_document(b"fake-pdf-content", "broken.pdf")

            assert result == "invoice"

    def test_defaults_to_invoice_on_no_api_key(self, mock_frappe):
        """No API key defaults to invoice."""
        mock_settings = SimpleNamespace(
            gemini_model="gemini-2.5-flash",
        )
        mock_settings.get_password = MagicMock(return_value="")
        mock_frappe.get_single.return_value = mock_settings

        result = classify_document(b"fake-pdf-content", "test.pdf")

        assert result == "invoice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest erpocr_integration/tests/test_classify_document.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement classification function**

Create `erpocr_integration/tasks/classify_document.py`:

```python
"""Gemini-based document classification.

Classifies uploaded documents as 'invoice' or 'statement' before routing
to the appropriate processing pipeline. Defaults to 'invoice' on any
error to avoid breaking the existing pipeline.
"""

import base64
import json

import frappe
import requests

_CLASSIFICATION_PROMPT = """Classify this document as one of:
- "invoice": A single invoice or a PDF containing multiple invoices. Has line items with quantities, unit prices, and amounts. May have a single supplier and invoice number.
- "statement": A supplier/vendor account statement. Lists multiple invoice references, payments, credits, and a running balance over a period. Typically has opening balance, closing balance, and an aging summary.

Look at the overall structure, not just the title. A document with columns like "Date | Reference | Debit | Credit | Balance" is a statement. A document with "Qty | Description | Unit Price | Amount" is an invoice.

Return ONLY the document type."""

_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "description": "Either 'invoice' or 'statement'",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0 to 1.0",
        },
    },
    "required": ["document_type", "confidence"],
}


def classify_document(
    file_content: bytes,
    filename: str,
    mime_type: str = "application/pdf",
) -> str:
    """Classify a document as 'invoice' or 'statement' using Gemini.

    Returns 'invoice' as safe default on any error — never blocks the
    existing pipeline.
    """
    try:
        settings = frappe.get_single("OCR Settings")
        api_key = settings.get_password("gemini_api_key")
        if not api_key:
            return "invoice"

        model = settings.gemini_model or "gemini-2.5-flash"
        result = _call_classification_api(file_content, api_key, model, mime_type)

        doc_type = result.get("document_type", "").lower().strip()
        if doc_type == "statement":
            return "statement"
        return "invoice"

    except Exception:
        frappe.log_error(
            title="Document Classification Error",
            message=f"Classification failed for {filename}, defaulting to invoice\n"
            f"{frappe.get_traceback()}",
        )
        return "invoice"


def _call_classification_api(
    file_content: bytes,
    api_key: str,
    model: str,
    mime_type: str,
) -> dict:
    """Call Gemini API for document classification. Returns parsed response."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    file_base64 = base64.b64encode(file_content).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _CLASSIFICATION_PROMPT},
                    {"inline_data": {"mime_type": mime_type, "data": file_base64}},
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _CLASSIFICATION_SCHEMA,
        },
    }

    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    candidates = data.get("candidates", [])
    text = candidates[0]["content"]["parts"][0]["text"]
    return json.loads(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest erpocr_integration/tests/test_classify_document.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check erpocr_integration/tasks/classify_document.py erpocr_integration/tests/test_classify_document.py
ruff format erpocr_integration/tasks/classify_document.py erpocr_integration/tests/test_classify_document.py
git add erpocr_integration/tasks/classify_document.py erpocr_integration/tests/test_classify_document.py
git commit -m "feat: add Gemini-based document classification (invoice vs statement)"
```

---

## Task 2: OCR Statement + OCR Statement Item DocTypes

**Files:**
- Create: `erpocr_integration/erpnext_ocr/doctype/ocr_statement/ocr_statement.json`
- Create: `erpocr_integration/erpnext_ocr/doctype/ocr_statement/ocr_statement.py`
- Create: `erpocr_integration/erpnext_ocr/doctype/ocr_statement/__init__.py`
- Create: `erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/ocr_statement_item.json`
- Create: `erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/__init__.py`

This task creates the DocType JSON files. The DocType structure follows the same patterns as OCR Import.

- [ ] **Step 1: Create OCR Statement Item (child table) DocType JSON**

Create directory and files:
```bash
mkdir -p erpocr_integration/erpnext_ocr/doctype/ocr_statement_item
touch erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/__init__.py
```

Create `erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/ocr_statement_item.json`:

```json
{
    "actions": [],
    "autoname": "hash",
    "creation": "2026-03-27 00:00:00.000000",
    "doctype": "DocType",
    "engine": "InnoDB",
    "field_order": [
        "reference",
        "transaction_date",
        "description",
        "column_break_1",
        "debit",
        "credit",
        "balance",
        "section_break_recon",
        "recon_status",
        "matched_invoice",
        "column_break_recon",
        "erp_amount",
        "erp_outstanding",
        "difference"
    ],
    "fields": [
        {
            "fieldname": "reference",
            "fieldtype": "Data",
            "in_list_view": 1,
            "label": "Reference"
        },
        {
            "fieldname": "transaction_date",
            "fieldtype": "Date",
            "in_list_view": 1,
            "label": "Date"
        },
        {
            "fieldname": "description",
            "fieldtype": "Data",
            "label": "Description"
        },
        {
            "fieldname": "column_break_1",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "debit",
            "fieldtype": "Currency",
            "in_list_view": 1,
            "label": "Debit"
        },
        {
            "fieldname": "credit",
            "fieldtype": "Currency",
            "in_list_view": 1,
            "label": "Credit"
        },
        {
            "fieldname": "balance",
            "fieldtype": "Currency",
            "label": "Balance"
        },
        {
            "fieldname": "section_break_recon",
            "fieldtype": "Section Break",
            "label": "Reconciliation"
        },
        {
            "fieldname": "recon_status",
            "fieldtype": "Select",
            "in_list_view": 1,
            "label": "Recon Status",
            "options": "\nMatched\nAmount Mismatch\nMissing from ERPNext\nNot in Statement\nPayment\nUnreconciled",
            "read_only": 1
        },
        {
            "fieldname": "matched_invoice",
            "fieldtype": "Link",
            "label": "Matched Invoice",
            "options": "Purchase Invoice",
            "read_only": 1
        },
        {
            "fieldname": "column_break_recon",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "erp_amount",
            "fieldtype": "Currency",
            "label": "ERPNext Amount",
            "read_only": 1
        },
        {
            "fieldname": "erp_outstanding",
            "fieldtype": "Currency",
            "label": "Outstanding",
            "read_only": 1
        },
        {
            "fieldname": "difference",
            "fieldtype": "Currency",
            "label": "Difference",
            "read_only": 1
        }
    ],
    "istable": 1,
    "links": [],
    "modified": "2026-03-27 00:00:00.000000",
    "modified_by": "Administrator",
    "module": "ERPNext OCR",
    "name": "OCR Statement Item",
    "naming_rule": "Random",
    "owner": "Administrator",
    "permissions": [],
    "sort_field": "creation",
    "sort_order": "ASC",
    "states": [],
    "track_changes": 0
}
```

- [ ] **Step 2: Create OCR Statement (parent) DocType JSON**

```bash
mkdir -p erpocr_integration/erpnext_ocr/doctype/ocr_statement
touch erpocr_integration/erpnext_ocr/doctype/ocr_statement/__init__.py
```

Create `erpocr_integration/erpnext_ocr/doctype/ocr_statement/ocr_statement.json`:

```json
{
    "actions": [],
    "autoname": "naming_series:",
    "creation": "2026-03-27 00:00:00.000000",
    "doctype": "DocType",
    "engine": "InnoDB",
    "field_order": [
        "naming_series",
        "status",
        "source_filename",
        "column_break_header",
        "source_type",
        "uploaded_by",
        "company",
        "section_break_drive",
        "drive_file_id",
        "drive_retry_count",
        "drive_link",
        "column_break_drive",
        "drive_folder_path",
        "section_break_supplier",
        "supplier_name_ocr",
        "supplier",
        "column_break_supplier",
        "supplier_match_status",
        "section_break_period",
        "statement_date",
        "period_from",
        "period_to",
        "column_break_period",
        "opening_balance",
        "closing_balance",
        "currency",
        "section_break_recon_summary",
        "total_lines",
        "matched_count",
        "mismatch_count",
        "column_break_recon_summary",
        "missing_count",
        "not_in_statement_count",
        "payment_count",
        "section_break_items",
        "items",
        "section_break_status",
        "error_log",
        "section_break_raw",
        "raw_payload"
    ],
    "fields": [
        {
            "fieldname": "naming_series",
            "fieldtype": "Select",
            "hidden": 1,
            "label": "Naming Series",
            "options": "OCR-STMT-.#####",
            "reqd": 1
        },
        {
            "fieldname": "status",
            "fieldtype": "Select",
            "in_list_view": 1,
            "in_standard_filter": 1,
            "label": "Status",
            "options": "Pending\nExtracting\nReconciled\nReviewed\nError",
            "read_only": 1
        },
        {
            "fieldname": "source_filename",
            "fieldtype": "Data",
            "in_list_view": 1,
            "label": "Source File",
            "read_only": 1
        },
        {
            "fieldname": "column_break_header",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "source_type",
            "fieldtype": "Select",
            "label": "Source",
            "options": "Gemini Drive Scan\nGemini Email\nGemini Manual Upload",
            "read_only": 1
        },
        {
            "fieldname": "uploaded_by",
            "fieldtype": "Link",
            "label": "Uploaded By",
            "options": "User",
            "read_only": 1
        },
        {
            "fieldname": "company",
            "fieldtype": "Link",
            "label": "Company",
            "options": "Company",
            "reqd": 1
        },
        {
            "fieldname": "section_break_drive",
            "fieldtype": "Section Break",
            "collapsible": 1,
            "label": "Google Drive"
        },
        {
            "fieldname": "drive_file_id",
            "fieldtype": "Data",
            "label": "Drive File ID",
            "read_only": 1,
            "unique": 1
        },
        {
            "fieldname": "drive_retry_count",
            "fieldtype": "Int",
            "default": "0",
            "hidden": 1,
            "label": "Retry Count"
        },
        {
            "fieldname": "drive_link",
            "fieldtype": "Data",
            "label": "Drive Link",
            "read_only": 1
        },
        {
            "fieldname": "column_break_drive",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "drive_folder_path",
            "fieldtype": "Data",
            "label": "Archive Path",
            "read_only": 1
        },
        {
            "fieldname": "section_break_supplier",
            "fieldtype": "Section Break",
            "label": "Supplier"
        },
        {
            "fieldname": "supplier_name_ocr",
            "fieldtype": "Data",
            "label": "Supplier (OCR)",
            "read_only": 1
        },
        {
            "fieldname": "supplier",
            "fieldtype": "Link",
            "in_list_view": 1,
            "label": "Supplier",
            "options": "Supplier"
        },
        {
            "fieldname": "column_break_supplier",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "supplier_match_status",
            "fieldtype": "Select",
            "label": "Supplier Match",
            "options": "\nAuto Matched\nSuggested\nUnmatched\nConfirmed",
            "read_only": 1
        },
        {
            "fieldname": "section_break_period",
            "fieldtype": "Section Break",
            "label": "Statement Period"
        },
        {
            "fieldname": "statement_date",
            "fieldtype": "Date",
            "in_list_view": 1,
            "label": "Statement Date"
        },
        {
            "fieldname": "period_from",
            "fieldtype": "Date",
            "label": "Period From"
        },
        {
            "fieldname": "period_to",
            "fieldtype": "Date",
            "label": "Period To"
        },
        {
            "fieldname": "column_break_period",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "opening_balance",
            "fieldtype": "Currency",
            "label": "Opening Balance",
            "read_only": 1
        },
        {
            "fieldname": "closing_balance",
            "fieldtype": "Currency",
            "label": "Closing Balance",
            "read_only": 1
        },
        {
            "fieldname": "currency",
            "fieldtype": "Link",
            "label": "Currency",
            "options": "Currency"
        },
        {
            "fieldname": "section_break_recon_summary",
            "fieldtype": "Section Break",
            "label": "Reconciliation Summary"
        },
        {
            "fieldname": "total_lines",
            "fieldtype": "Int",
            "label": "Total Lines",
            "read_only": 1
        },
        {
            "fieldname": "matched_count",
            "fieldtype": "Int",
            "label": "Matched",
            "read_only": 1
        },
        {
            "fieldname": "mismatch_count",
            "fieldtype": "Int",
            "label": "Mismatches",
            "read_only": 1
        },
        {
            "fieldname": "column_break_recon_summary",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "missing_count",
            "fieldtype": "Int",
            "label": "Missing from ERPNext",
            "read_only": 1
        },
        {
            "fieldname": "not_in_statement_count",
            "fieldtype": "Int",
            "label": "Not in Statement",
            "read_only": 1
        },
        {
            "fieldname": "payment_count",
            "fieldtype": "Int",
            "label": "Payments",
            "read_only": 1
        },
        {
            "fieldname": "section_break_items",
            "fieldtype": "Section Break",
            "label": "Transaction Lines"
        },
        {
            "fieldname": "items",
            "fieldtype": "Table",
            "label": "Items",
            "options": "OCR Statement Item"
        },
        {
            "fieldname": "section_break_status",
            "fieldtype": "Section Break",
            "label": "Processing"
        },
        {
            "fieldname": "error_log",
            "fieldtype": "Link",
            "label": "Error Log",
            "options": "Error Log",
            "read_only": 1
        },
        {
            "fieldname": "section_break_raw",
            "fieldtype": "Section Break",
            "collapsible": 1,
            "label": "Raw Data"
        },
        {
            "fieldname": "raw_payload",
            "fieldtype": "Code",
            "label": "Raw Gemini Response",
            "read_only": 1
        }
    ],
    "links": [],
    "modified": "2026-03-27 00:00:00.000000",
    "modified_by": "Administrator",
    "module": "ERPNext OCR",
    "name": "OCR Statement",
    "naming_rule": "By \"Naming Series\" field",
    "owner": "Administrator",
    "permissions": [
        {
            "create": 1,
            "delete": 1,
            "email": 1,
            "export": 1,
            "print": 1,
            "read": 1,
            "report": 1,
            "role": "OCR Manager",
            "share": 1,
            "write": 1
        },
        {
            "create": 1,
            "delete": 1,
            "email": 1,
            "export": 1,
            "print": 1,
            "read": 1,
            "report": 1,
            "role": "System Manager",
            "share": 1,
            "write": 1
        }
    ],
    "sort_field": "creation",
    "sort_order": "DESC",
    "states": [],
    "title_field": "supplier_name_ocr",
    "track_changes": 1
}
```

- [ ] **Step 3: Create minimal controller**

Create `erpocr_integration/erpnext_ocr/doctype/ocr_statement/ocr_statement.py`:

```python
# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OCRStatement(Document):
    def mark_reviewed(self):
        """Mark this statement as reviewed by the user."""
        if self.status != "Reconciled":
            frappe.throw("Can only mark Reconciled statements as Reviewed.")
        self.status = "Reviewed"
        self.save()
        frappe.msgprint("Statement marked as reviewed.", indicator="green")
```

- [ ] **Step 4: Validate JSON files**

Run: `python -c "import json; json.load(open('erpocr_integration/erpnext_ocr/doctype/ocr_statement/ocr_statement.json')); json.load(open('erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/ocr_statement_item.json')); print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add erpocr_integration/erpnext_ocr/doctype/ocr_statement/ erpocr_integration/erpnext_ocr/doctype/ocr_statement_item/
git commit -m "feat: add OCR Statement and OCR Statement Item DocTypes"
```

---

## Task 3: Gemini Statement Extraction

**Files:**
- Modify: `erpocr_integration/tasks/gemini_extract.py`
- Modify: `erpocr_integration/tests/test_gemini_extract.py`

- [ ] **Step 1: Write failing tests for `extract_statement_data()`**

Add to `erpocr_integration/tests/test_gemini_extract.py`:

```python
from erpocr_integration.tasks.gemini_extract import extract_statement_data


class TestExtractStatementData:
    def test_extracts_statement_header(self, mock_frappe):
        mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
        mock_settings.get_password = MagicMock(return_value="fake-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.gemini_extract._call_gemini_api") as mock_api:
            mock_api.return_value = {
                "candidates": [{"content": {"parts": [{"text": json.dumps({
                    "supplier_name": "Louma Trading",
                    "statement_date": "2026-02-28",
                    "period_from": "2026-02-01",
                    "period_to": "2026-02-28",
                    "opening_balance": 5000.0,
                    "closing_balance": 12000.0,
                    "currency": "ZAR",
                    "transactions": [
                        {"reference": "IN238509", "date": "2026-02-02", "description": "Tax Invoice",
                         "debit": 19780.0, "credit": 0, "balance": 24780.0},
                        {"reference": "PMT-001", "date": "2026-02-15", "description": "Payment received",
                         "debit": 0, "credit": 10000.0, "balance": 14780.0},
                    ],
                })}]}}]
            }

            result = extract_statement_data(b"fake-pdf", "statement.pdf")

        assert result["header_fields"]["supplier_name"] == "Louma Trading"
        assert result["header_fields"]["statement_date"] == "2026-02-28"
        assert result["header_fields"]["opening_balance"] == 5000.0
        assert result["header_fields"]["closing_balance"] == 12000.0
        assert len(result["transactions"]) == 2
        assert result["transactions"][0]["reference"] == "IN238509"
        assert result["transactions"][0]["debit"] == 19780.0
        assert result["transactions"][1]["credit"] == 10000.0

    def test_handles_empty_transactions(self, mock_frappe):
        mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
        mock_settings.get_password = MagicMock(return_value="fake-key")
        mock_frappe.get_single.return_value = mock_settings

        with patch("erpocr_integration.tasks.gemini_extract._call_gemini_api") as mock_api:
            mock_api.return_value = {
                "candidates": [{"content": {"parts": [{"text": json.dumps({
                    "supplier_name": "Test",
                    "statement_date": "2026-02-28",
                    "period_from": "", "period_to": "",
                    "opening_balance": 0, "closing_balance": 0,
                    "currency": "ZAR",
                    "transactions": [],
                })}]}}]
            }

            with pytest.raises(Exception, match="No transactions"):
                extract_statement_data(b"fake-pdf", "empty.pdf")
```

- [ ] **Step 2: Implement `extract_statement_data()`**

Add to `erpocr_integration/tasks/gemini_extract.py` (after the fleet extraction functions):

```python
def extract_statement_data(
    pdf_content: bytes, filename: str, mime_type: str = "application/pdf"
) -> dict:
    """Extract transaction lines from a supplier statement using Gemini API."""
    start_time = time.time()

    settings = frappe.get_single("OCR Settings")
    api_key = settings.get_password("gemini_api_key")
    if not api_key:
        frappe.throw(_("Gemini API key not configured in OCR Settings"))

    model = settings.gemini_model or "gemini-2.5-flash"
    prompt = _build_statement_prompt()
    schema = _build_statement_schema()

    try:
        response_data = _call_gemini_api(pdf_content, prompt, schema, api_key, model, mime_type)
    except Exception as e:
        frappe.log_error(
            title="Gemini API Error",
            message=f"Statement extraction failed for {filename}\n{frappe.get_traceback()}",
        )
        raise Exception(f"Failed to call Gemini API: {e!s}") from e

    is_valid, error_msg = _validate_gemini_response(response_data)
    if not is_valid:
        raise Exception(f"Invalid Gemini response: {error_msg}")

    try:
        candidates = response_data.get("candidates", [])
        text = candidates[0]["content"]["parts"][0]["text"]
        extracted = json.loads(text)
    except Exception as e:
        raise Exception(f"Failed to parse Gemini response: {e!s}") from e

    transactions = extracted.get("transactions", [])
    if not transactions:
        raise Exception("No transactions found in statement")

    from erpocr_integration.tasks.process_import import _clean_ocr_text, _parse_date

    header_fields = {
        "supplier_name": _clean_ocr_text(extracted.get("supplier_name", "")),
        "statement_date": _parse_date(extracted.get("statement_date", "")),
        "period_from": _parse_date(extracted.get("period_from", "")),
        "period_to": _parse_date(extracted.get("period_to", "")),
        "opening_balance": extracted.get("opening_balance") or 0.0,
        "closing_balance": extracted.get("closing_balance") or 0.0,
        "currency": (extracted.get("currency") or "").upper().strip(),
    }

    parsed_transactions = []
    for txn in transactions:
        parsed_transactions.append({
            "reference": _clean_ocr_text(txn.get("reference", "")),
            "date": _parse_date(txn.get("date", "")),
            "description": _clean_ocr_text(txn.get("description", "")),
            "debit": txn.get("debit") or 0.0,
            "credit": txn.get("credit") or 0.0,
            "balance": txn.get("balance") or 0.0,
        })

    return {
        "header_fields": header_fields,
        "transactions": parsed_transactions,
        "raw_response": json.dumps(response_data, indent=2),
        "extraction_time": time.time() - start_time,
        "source_filename": filename,
    }


def _build_statement_prompt() -> str:
    return """Extract ALL transaction lines from this supplier account statement.

For each transaction line, extract:
- reference: The invoice number, credit note number, or payment reference
- date: Transaction date in YYYY-MM-DD format
- description: Description text (e.g., "Tax Invoice", "Payment", "Credit Note")
- debit: Amount charged/invoiced (0 if this is a payment/credit)
- credit: Amount paid/credited (0 if this is an invoice/debit)
- balance: Running balance after this transaction

Also extract the statement header:
- supplier_name: The supplier/vendor name
- statement_date: The date the statement was generated
- period_from: Start of the statement period
- period_to: End of the statement period
- opening_balance: Balance at the start of the period
- closing_balance: Balance at the end of the period
- currency: Currency code (e.g., ZAR, USD)

Extract EVERY line — do not skip any transactions. Include payments, credit notes, and debit notes alongside invoices."""


def _build_statement_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "supplier_name": {"type": "string"},
            "statement_date": {"type": "string"},
            "period_from": {"type": "string"},
            "period_to": {"type": "string"},
            "opening_balance": {"type": "number"},
            "closing_balance": {"type": "number"},
            "currency": {"type": "string"},
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reference": {"type": "string"},
                        "date": {"type": "string"},
                        "description": {"type": "string"},
                        "debit": {"type": "number"},
                        "credit": {"type": "number"},
                        "balance": {"type": "number"},
                    },
                    "required": ["reference", "date", "debit", "credit"],
                },
            },
        },
        "required": ["supplier_name", "transactions"],
    }
```

- [ ] **Step 3: Run tests, lint, commit**

```bash
pytest erpocr_integration/tests/test_gemini_extract.py -v
ruff check erpocr_integration/tasks/gemini_extract.py
ruff format erpocr_integration/tasks/gemini_extract.py
git add erpocr_integration/tasks/gemini_extract.py erpocr_integration/tests/test_gemini_extract.py
git commit -m "feat: add Gemini statement extraction prompt and schema"
```

---

## Task 4: Reconciliation Logic

**Files:**
- Create: `erpocr_integration/tasks/reconcile.py`
- Create: `erpocr_integration/tests/test_reconcile.py`

- [ ] **Step 1: Write failing tests for `reconcile_statement()`**

Create `erpocr_integration/tests/test_reconcile.py`:

```python
"""Tests for statement reconciliation logic."""

from types import SimpleNamespace

import pytest

from erpocr_integration.tasks.reconcile import reconcile_statement


def _make_statement(**overrides):
    defaults = dict(
        supplier="SUP-001",
        company="Test Co",
        period_from="2026-02-01",
        period_to="2026-02-28",
        items=[],
    )
    defaults.update(overrides)
    obj = SimpleNamespace(**defaults)
    obj.append = lambda table, row: obj.items.append(SimpleNamespace(**row))
    return obj


def _make_statement_item(**overrides):
    defaults = dict(
        reference="INV-001",
        transaction_date="2026-02-15",
        description="Tax Invoice",
        debit=1000.0,
        credit=0.0,
        balance=1000.0,
        recon_status="",
        matched_invoice="",
        erp_amount=0,
        erp_outstanding=0,
        difference=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestReconcileStatement:
    def test_matches_invoice_by_bill_no(self, mock_frappe):
        stmt = _make_statement(
            items=[_make_statement_item(reference="INV-001", debit=1000.0)],
        )
        # Mock: PI found with matching bill_no and amount
        mock_frappe.get_all.return_value = [
            {"name": "PI-001", "bill_no": "INV-001", "grand_total": 1000.0,
             "outstanding_amount": 0.0, "status": "Paid"},
        ]

        reconcile_statement(stmt)

        assert stmt.items[0].recon_status == "Matched"
        assert stmt.items[0].matched_invoice == "PI-001"
        assert stmt.items[0].erp_amount == 1000.0

    def test_detects_amount_mismatch(self, mock_frappe):
        stmt = _make_statement(
            items=[_make_statement_item(reference="INV-002", debit=1500.0)],
        )
        mock_frappe.get_all.return_value = [
            {"name": "PI-002", "bill_no": "INV-002", "grand_total": 1400.0,
             "outstanding_amount": 0.0, "status": "Paid"},
        ]

        reconcile_statement(stmt)

        assert stmt.items[0].recon_status == "Amount Mismatch"
        assert stmt.items[0].erp_amount == 1400.0
        assert stmt.items[0].difference == 100.0

    def test_marks_missing_from_erpnext(self, mock_frappe):
        stmt = _make_statement(
            items=[_make_statement_item(reference="INV-UNKNOWN", debit=500.0)],
        )
        mock_frappe.get_all.return_value = []  # No PI found

        reconcile_statement(stmt)

        assert stmt.items[0].recon_status == "Missing from ERPNext"

    def test_marks_credit_as_payment(self, mock_frappe):
        stmt = _make_statement(
            items=[_make_statement_item(reference="PMT-001", debit=0.0, credit=5000.0)],
        )
        mock_frappe.get_all.return_value = []

        reconcile_statement(stmt)

        assert stmt.items[0].recon_status == "Payment"

    def test_reverse_check_adds_not_in_statement(self, mock_frappe):
        stmt = _make_statement(
            items=[_make_statement_item(reference="INV-001", debit=1000.0)],
        )
        # Mock: two PIs exist but only one is on the statement
        def get_all_handler(doctype, **kwargs):
            filters = kwargs.get("filters", {})
            if doctype == "Purchase Invoice" and "bill_no" in str(filters):
                return [{"name": "PI-001", "bill_no": "INV-001", "grand_total": 1000.0,
                         "outstanding_amount": 0.0, "status": "Paid"}]
            if doctype == "Purchase Invoice":
                return [
                    {"name": "PI-001", "bill_no": "INV-001", "grand_total": 1000.0,
                     "outstanding_amount": 0.0, "posting_date": "2026-02-10"},
                    {"name": "PI-099", "bill_no": "INV-099", "grand_total": 2000.0,
                     "outstanding_amount": 2000.0, "posting_date": "2026-02-20"},
                ]
            return []

        mock_frappe.get_all.side_effect = get_all_handler

        reconcile_statement(stmt)

        # Original item + 1 reverse-check item
        assert len(stmt.items) == 2
        reverse_item = stmt.items[1]
        assert reverse_item.recon_status == "Not in Statement"
        assert reverse_item.reference == "INV-099"
        assert reverse_item.debit == 2000.0

    def test_updates_summary_counts(self, mock_frappe):
        stmt = _make_statement(
            items=[
                _make_statement_item(reference="INV-001", debit=1000.0),
                _make_statement_item(reference="PMT-001", debit=0.0, credit=500.0),
            ],
        )
        mock_frappe.get_all.return_value = [
            {"name": "PI-001", "bill_no": "INV-001", "grand_total": 1000.0,
             "outstanding_amount": 0.0, "status": "Paid"},
        ]

        reconcile_statement(stmt)

        assert stmt.matched_count == 1
        assert stmt.payment_count == 1
```

- [ ] **Step 2: Implement `reconcile_statement()`**

Create `erpocr_integration/tasks/reconcile.py`:

```python
"""Statement reconciliation logic.

Matches statement transaction lines against ERPNext Purchase Invoices,
then does a reverse check to find PIs not on the statement.
"""

import frappe


def reconcile_statement(ocr_statement) -> None:
    """Reconcile all transaction lines against ERPNext Purchase Invoices.

    Mutates ocr_statement.items in place — sets recon_status, matched_invoice,
    erp_amount, erp_outstanding, and difference on each item. Also adds
    reverse-check rows for PIs not appearing on the statement.
    """
    if not ocr_statement.supplier or not ocr_statement.company:
        return

    # Get all submitted PIs for this supplier in the statement period
    pi_filters = {
        "supplier": ocr_statement.supplier,
        "company": ocr_statement.company,
        "docstatus": 1,
    }
    if ocr_statement.period_from:
        pi_filters["posting_date"] = [">=", ocr_statement.period_from]
    if ocr_statement.period_to:
        if "posting_date" in pi_filters:
            pi_filters["posting_date"] = [
                "between",
                [ocr_statement.period_from, ocr_statement.period_to],
            ]
        else:
            pi_filters["posting_date"] = ["<=", ocr_statement.period_to]

    all_pis = frappe.get_all(
        "Purchase Invoice",
        filters=pi_filters,
        fields=["name", "bill_no", "grand_total", "outstanding_amount", "posting_date"],
        ignore_permissions=True,
    )

    # Build lookup by bill_no for fast matching
    pi_by_bill_no = {}
    for pi in all_pis:
        if pi.get("bill_no"):
            pi_by_bill_no.setdefault(pi["bill_no"], []).append(pi)

    # Track which PIs were matched (for reverse check)
    matched_pi_names = set()

    # Forward reconciliation: match each statement line to a PI
    for item in ocr_statement.items:
        # Credit lines are payments — mark and skip
        if (item.credit or 0) > 0 and (item.debit or 0) == 0:
            item.recon_status = "Payment"
            continue

        # Debit lines are invoices — try to match
        ref = (item.reference or "").strip()
        if not ref:
            item.recon_status = "Unreconciled"
            continue

        candidates = pi_by_bill_no.get(ref, [])
        if not candidates:
            item.recon_status = "Missing from ERPNext"
            continue

        # Take the first match (most common: one PI per invoice number)
        pi = candidates[0]
        item.matched_invoice = pi["name"]
        item.erp_amount = pi["grand_total"]
        item.erp_outstanding = pi["outstanding_amount"]
        matched_pi_names.add(pi["name"])

        # Compare amounts
        stmt_amount = item.debit or 0
        erp_amount = pi["grand_total"] or 0
        diff = abs(stmt_amount - erp_amount)

        if diff < 0.01:  # Float tolerance
            item.recon_status = "Matched"
            item.difference = 0
        else:
            item.recon_status = "Amount Mismatch"
            item.difference = round(stmt_amount - erp_amount, 2)

    # Reverse check: find PIs NOT on the statement
    statement_refs = {(item.reference or "").strip() for item in ocr_statement.items}
    for pi in all_pis:
        if pi["name"] not in matched_pi_names and pi.get("bill_no") not in statement_refs:
            ocr_statement.append("items", {
                "reference": pi.get("bill_no", pi["name"]),
                "transaction_date": pi.get("posting_date"),
                "description": "Not on statement (ERPNext PI exists)",
                "debit": pi["grand_total"],
                "credit": 0,
                "balance": 0,
                "recon_status": "Not in Statement",
                "matched_invoice": pi["name"],
                "erp_amount": pi["grand_total"],
                "erp_outstanding": pi["outstanding_amount"],
                "difference": 0,
            })

    # Update summary counts
    ocr_statement.total_lines = len(ocr_statement.items)
    ocr_statement.matched_count = sum(
        1 for i in ocr_statement.items if i.recon_status == "Matched"
    )
    ocr_statement.mismatch_count = sum(
        1 for i in ocr_statement.items if i.recon_status == "Amount Mismatch"
    )
    ocr_statement.missing_count = sum(
        1 for i in ocr_statement.items if i.recon_status == "Missing from ERPNext"
    )
    ocr_statement.not_in_statement_count = sum(
        1 for i in ocr_statement.items if i.recon_status == "Not in Statement"
    )
    ocr_statement.payment_count = sum(
        1 for i in ocr_statement.items if i.recon_status == "Payment"
    )
```

- [ ] **Step 3: Run tests, lint, commit**

```bash
pytest erpocr_integration/tests/test_reconcile.py -v
ruff check erpocr_integration/tasks/reconcile.py erpocr_integration/tests/test_reconcile.py
ruff format erpocr_integration/tasks/reconcile.py erpocr_integration/tests/test_reconcile.py
git add erpocr_integration/tasks/reconcile.py erpocr_integration/tests/test_reconcile.py
git commit -m "feat: add statement reconciliation logic with reverse check"
```

---

## Task 5: Statement Processing Pipeline

**Files:**
- Create: `erpocr_integration/statement_api.py`
- Create: `erpocr_integration/tests/test_statement_api.py`

- [ ] **Step 1: Write tests for `statement_gemini_process()`**

Create `erpocr_integration/tests/test_statement_api.py`:

```python
"""Tests for statement processing pipeline."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.statement_api import _populate_ocr_statement, _run_statement_matching


class TestPopulateOcrStatement:
    def test_populates_header_fields(self):
        doc = SimpleNamespace(items=[], append=lambda t, r: doc.items.append(SimpleNamespace(**r)))
        extracted = {
            "header_fields": {
                "supplier_name": "Louma Trading",
                "statement_date": "2026-02-28",
                "period_from": "2026-02-01",
                "period_to": "2026-02-28",
                "opening_balance": 5000.0,
                "closing_balance": 12000.0,
                "currency": "ZAR",
            },
            "transactions": [
                {"reference": "INV-001", "date": "2026-02-05", "description": "Tax Invoice",
                 "debit": 1000.0, "credit": 0.0, "balance": 6000.0},
            ],
            "raw_response": "{}",
        }

        _populate_ocr_statement(doc, extracted)

        assert doc.supplier_name_ocr == "Louma Trading"
        assert doc.statement_date == "2026-02-28"
        assert doc.opening_balance == 5000.0
        assert doc.closing_balance == 12000.0
        assert len(doc.items) == 1
        assert doc.items[0].reference == "INV-001"
        assert doc.items[0].debit == 1000.0


class TestRunStatementMatching:
    def test_matches_supplier(self, mock_frappe):
        doc = SimpleNamespace(supplier_name_ocr="Louma Trading", supplier=None, supplier_match_status=None)

        mock_frappe.db.get_value.return_value = "SUP-LOUMA"

        _run_statement_matching(doc)

        assert doc.supplier == "SUP-LOUMA"
        assert doc.supplier_match_status == "Auto Matched"
```

- [ ] **Step 2: Implement `statement_api.py`**

Create `erpocr_integration/statement_api.py`:

```python
"""Statement processing pipeline — background job for statement extraction + reconciliation."""

import frappe
from frappe import _


def statement_gemini_process(
    file_content: bytes,
    filename: str,
    ocr_statement_name: str,
    source_type: str = "Gemini Drive Scan",
    uploaded_by: str | None = None,
    mime_type: str = "application/pdf",
    queue_position: int = 0,
):
    """Background job: extract statement data via Gemini, populate OCR Statement, reconcile."""
    frappe.set_user(uploaded_by or "Administrator")

    try:
        if queue_position > 0:
            import time

            wait_seconds = min(queue_position * 5, 240)
            time.sleep(wait_seconds)

        frappe.db.set_value("OCR Statement", ocr_statement_name, "status", "Extracting")
        frappe.db.commit()  # nosemgrep

        from erpocr_integration.tasks.gemini_extract import extract_statement_data

        extracted = extract_statement_data(file_content, filename, mime_type=mime_type)

        ocr_statement = frappe.get_doc("OCR Statement", ocr_statement_name)

        _populate_ocr_statement(ocr_statement, extracted)
        _run_statement_matching(ocr_statement)
        ocr_statement.save(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep

        # Reconcile against ERPNext PIs (requires supplier to be matched)
        if ocr_statement.supplier:
            from erpocr_integration.tasks.reconcile import reconcile_statement

            reconcile_statement(ocr_statement)
            ocr_statement.status = "Reconciled"
        else:
            ocr_statement.status = "Pending"  # Needs manual supplier match before recon

        ocr_statement.save(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep

        # Move Drive file to archive
        if ocr_statement.drive_file_id:
            try:
                from erpocr_integration.tasks.drive_integration import move_file_to_archive

                drive_result = move_file_to_archive(
                    file_id=ocr_statement.drive_file_id,
                    supplier_name=extracted["header_fields"].get("supplier_name", ""),
                    invoice_date=extracted["header_fields"].get("statement_date"),
                )
                if drive_result.get("folder_path"):
                    ocr_statement.drive_link = drive_result.get("shareable_link")
                    ocr_statement.drive_folder_path = drive_result.get("folder_path")
                    ocr_statement.save(ignore_permissions=True)
                    frappe.db.commit()  # nosemgrep
            except Exception as e:
                frappe.log_error(
                    title="Drive Move Failed",
                    message=f"Failed to move {filename} to archive: {e!s}",
                )

    except Exception as e:
        try:
            error_log = frappe.log_error(
                title="Statement Processing Error",
                message=f"Statement extraction failed for {filename}\n{frappe.get_traceback()}",
            )
            frappe.db.set_value(
                "OCR Statement",
                ocr_statement_name,
                {"status": "Error", "error_log": error_log.name},
            )
            frappe.db.commit()  # nosemgrep
        except Exception:
            frappe.log_error(
                title="Statement Critical Error", message=frappe.get_traceback()
            )


def _populate_ocr_statement(ocr_statement, extracted: dict) -> None:
    """Populate OCR Statement with extracted data."""
    header = extracted.get("header_fields", {})

    ocr_statement.supplier_name_ocr = header.get("supplier_name", "")
    ocr_statement.statement_date = header.get("statement_date")
    ocr_statement.period_from = header.get("period_from")
    ocr_statement.period_to = header.get("period_to")
    ocr_statement.opening_balance = header.get("opening_balance") or 0.0
    ocr_statement.closing_balance = header.get("closing_balance") or 0.0
    ocr_statement.currency = header.get("currency", "")
    ocr_statement.raw_payload = extracted.get("raw_response", "")

    ocr_statement.items = []
    for txn in extracted.get("transactions", []):
        ocr_statement.append("items", {
            "reference": txn.get("reference", ""),
            "transaction_date": txn.get("date"),
            "description": txn.get("description", ""),
            "debit": txn.get("debit") or 0.0,
            "credit": txn.get("credit") or 0.0,
            "balance": txn.get("balance") or 0.0,
        })


def _run_statement_matching(ocr_statement) -> None:
    """Match the supplier name from the statement against ERPNext."""
    from erpocr_integration.tasks.matching import match_supplier, match_supplier_fuzzy

    if not ocr_statement.supplier_name_ocr:
        ocr_statement.supplier_match_status = "Unmatched"
        return

    matched, status = match_supplier(ocr_statement.supplier_name_ocr)
    if matched:
        ocr_statement.supplier = matched
        ocr_statement.supplier_match_status = status
        return

    settings = frappe.get_single("OCR Settings")
    threshold = settings.matching_threshold or 80
    fuzzy, fuzzy_status, _ = match_supplier_fuzzy(ocr_statement.supplier_name_ocr, threshold)
    if fuzzy:
        ocr_statement.supplier = fuzzy
        ocr_statement.supplier_match_status = fuzzy_status
    else:
        ocr_statement.supplier_match_status = "Unmatched"
```

- [ ] **Step 3: Run tests, lint, commit**

```bash
pytest erpocr_integration/tests/test_statement_api.py -v
ruff check erpocr_integration/statement_api.py erpocr_integration/tests/test_statement_api.py
ruff format erpocr_integration/statement_api.py erpocr_integration/tests/test_statement_api.py
git add erpocr_integration/statement_api.py erpocr_integration/tests/test_statement_api.py
git commit -m "feat: add statement processing pipeline with supplier matching"
```

---

## Task 6: Hook Classification into Drive Scan

**Files:**
- Modify: `erpocr_integration/tasks/drive_integration.py` (function `_process_scan_file`)

- [ ] **Step 1: Add classification to `_process_scan_file()`**

In `_process_scan_file()`, after the magic bytes validation (line ~423) and before the OCR Import placeholder creation (line ~425), add document classification:

```python
    # --- existing: magic bytes validation ---

    # Classify document: invoice or statement
    from erpocr_integration.tasks.classify_document import classify_document

    doc_type = classify_document(pdf_content, filename, file_mime_type)

    if doc_type == "statement":
        return _process_statement_file(
            pdf_content, filename, file_mime_type, drive_file_id,
            settings, queue_position, _next_retry_count,
        )

    # --- existing: OCR Import placeholder creation ---
```

- [ ] **Step 2: Add `_process_statement_file()` function**

Add to `drive_integration.py` (after `_process_scan_file`):

```python
def _process_statement_file(
    pdf_content: bytes,
    filename: str,
    mime_type: str,
    drive_file_id: str,
    settings,
    queue_position: int,
    retry_count: int,
) -> bool:
    """Process a file classified as a supplier statement."""
    # Dedup: check OCR Statement for this drive_file_id
    existing = frappe.get_all(
        "OCR Statement",
        filters={"drive_file_id": drive_file_id},
        fields=["name", "status"],
    )
    if existing and not all(row.status == "Error" for row in existing):
        return False  # Already processed

    ocr_statement = frappe.get_doc({
        "doctype": "OCR Statement",
        "status": "Pending",
        "source_filename": filename,
        "source_type": "Gemini Drive Scan",
        "uploaded_by": "Administrator",
        "company": settings.default_company,
        "drive_file_id": drive_file_id,
        "drive_retry_count": retry_count,
    })
    ocr_statement.insert(ignore_permissions=True)
    frappe.db.commit()  # nosemgrep

    try:
        stagger_delay = min(queue_position * 5, 240)
        frappe.enqueue(
            "erpocr_integration.statement_api.statement_gemini_process",
            queue="long",
            timeout=300 + stagger_delay,
            file_content=pdf_content,
            filename=filename,
            ocr_statement_name=ocr_statement.name,
            source_type="Gemini Drive Scan",
            uploaded_by="Administrator",
            mime_type=mime_type,
            queue_position=queue_position,
        )
        frappe.logger().info(f"Drive scan: Queued statement {filename} for processing")
        return True
    except Exception as e:
        frappe.delete_doc("OCR Statement", ocr_statement.name, force=True, ignore_permissions=True)
        frappe.db.commit()  # nosemgrep
        frappe.log_error(
            title="Statement Enqueue Error",
            message=f"Failed to enqueue {filename}: {e!s}",
        )
        return False
```

- [ ] **Step 3: Update dedup check in `_process_scan_file` to also check OCR Statement**

In the existing dedup block (checking `OCR Import` for `drive_file_id`), add a check for `OCR Statement` too:

```python
    # Also check if this file was already processed as a statement
    existing_statements = frappe.get_all(
        "OCR Statement",
        filters={"drive_file_id": drive_file_id},
        fields=["name", "status"],
    )
    if existing_statements:
        non_error = [r for r in existing_statements if r.status != "Error"]
        if non_error:
            return False  # Already processed as statement
```

- [ ] **Step 4: Run full test suite, lint, commit**

```bash
pytest erpocr_integration/tests/ -q
ruff check erpocr_integration/tasks/drive_integration.py
ruff format erpocr_integration/tasks/drive_integration.py
git add erpocr_integration/tasks/drive_integration.py
git commit -m "feat: hook document classification into Drive scan pipeline"
```

---

## Task 7: Client Script + Workspace + Hooks

**Files:**
- Create: `erpocr_integration/public/js/ocr_statement.js`
- Modify: `erpocr_integration/hooks.py`

- [ ] **Step 1: Create client script**

Create `erpocr_integration/public/js/ocr_statement.js`:

```javascript
frappe.ui.form.on('OCR Statement', {
    refresh: function(frm) {
        // Color-code recon status in child table
        if (frm.doc.items) {
            frm.doc.items.forEach(function(item) {
                let color = {
                    'Matched': 'green',
                    'Amount Mismatch': 'orange',
                    'Missing from ERPNext': 'red',
                    'Not in Statement': 'red',
                    'Payment': 'blue',
                    'Unreconciled': 'grey',
                }[item.recon_status] || 'grey';

                // Set indicator on the row
                let $row = frm.fields_dict.items.grid.grid_rows_by_docname[item.name];
                if ($row) {
                    $row.row.find('.indicator-pill').remove();
                }
            });
        }

        // Summary intro
        if (frm.doc.status === 'Reconciled' && frm.doc.total_lines) {
            let summary = [];
            if (frm.doc.matched_count) summary.push(frm.doc.matched_count + ' matched');
            if (frm.doc.mismatch_count) summary.push('<span style="color:var(--orange-500)">' + frm.doc.mismatch_count + ' mismatches</span>');
            if (frm.doc.missing_count) summary.push('<span style="color:var(--red-500)">' + frm.doc.missing_count + ' missing</span>');
            if (frm.doc.not_in_statement_count) summary.push('<span style="color:var(--red-500)">' + frm.doc.not_in_statement_count + ' not in statement</span>');
            if (frm.doc.payment_count) summary.push(frm.doc.payment_count + ' payments');

            frm.set_intro(
                frm.doc.total_lines + ' lines: ' + summary.join(', '),
                frm.doc.mismatch_count || frm.doc.missing_count || frm.doc.not_in_statement_count ? 'orange' : 'green'
            );
        }

        // Mark Reviewed button
        if (frm.doc.status === 'Reconciled') {
            frm.add_custom_button(__('Mark Reviewed'), function() {
                frappe.call({
                    method: 'mark_reviewed',
                    doc: frm.doc,
                    callback: function() {
                        frm.reload_doc();
                    }
                });
            }, __('Actions'));
        }

        // Re-reconcile button (after manual supplier change)
        if (frm.doc.supplier && frm.doc.status !== 'Error') {
            frm.add_custom_button(__('Re-Reconcile'), function() {
                frappe.call({
                    method: 'erpocr_integration.statement_api.rereconcile_statement',
                    args: { statement_name: frm.doc.name },
                    callback: function() {
                        frm.reload_doc();
                        frappe.show_alert({message: __('Reconciliation updated.'), indicator: 'green'});
                    }
                });
            });
        }
    }
});
```

- [ ] **Step 2: Update hooks.py**

Add to `doctype_js`:
```python
doctype_js = {
    "OCR Import": "public/js/ocr_import.js",
    "OCR Delivery Note": "public/js/ocr_delivery_note.js",
    "OCR Fleet Slip": "public/js/ocr_fleet_slip.js",
    "OCR Statement": "public/js/ocr_statement.js",
}
```

- [ ] **Step 3: Add re-reconcile API to statement_api.py**

Add to `erpocr_integration/statement_api.py`:

```python
@frappe.whitelist()
def rereconcile_statement(statement_name: str) -> None:
    """Re-run reconciliation after manual supplier change."""
    doc = frappe.get_doc("OCR Statement", statement_name)
    if not doc.supplier:
        frappe.throw(_("Please select a supplier first."))

    from erpocr_integration.tasks.reconcile import reconcile_statement

    # Clear existing recon data from items
    for item in doc.items:
        if item.recon_status == "Not in Statement":
            continue  # Will be re-added by reconcile
        item.recon_status = ""
        item.matched_invoice = ""
        item.erp_amount = 0
        item.erp_outstanding = 0
        item.difference = 0

    # Remove reverse-check items (will be re-added)
    doc.items = [i for i in doc.items if i.recon_status != "Not in Statement"]

    reconcile_statement(doc)
    doc.status = "Reconciled"
    doc.save()
```

- [ ] **Step 4: Commit**

```bash
git add erpocr_integration/public/js/ocr_statement.js erpocr_integration/hooks.py erpocr_integration/statement_api.py
git commit -m "feat: add OCR Statement client script, hooks, and re-reconcile API"
```

---

## Task 8: Version Bump + Final Validation

**Files:**
- Modify: `erpocr_integration/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

Run: `pytest erpocr_integration/tests/ -v`
Expected: All tests pass (515 existing + ~20 new)

- [ ] **Step 2: Lint entire codebase**

Run: `ruff check erpocr_integration/ && ruff format --check erpocr_integration/`

- [ ] **Step 3: Version bump**

```python
__version__ = "0.8.0"
```

README badge: `version-0.8.0`

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git commit -m "feat: Phase 8 — statement reconciliation (v0.8.0)

Gemini-based document classification routes statements to a new pipeline.
Extracts transaction lines, auto-reconciles against ERPNext PIs (forward
+ reverse check), and presents a color-coded reconciliation view.

Recon statuses: Matched, Amount Mismatch, Missing from ERPNext,
Not in Statement, Payment."

git push
```

---

## Rollout Checklist

1. Deploy: `bench get-app --upgrade` + `bench migrate` + `bench restart`
2. Verify OCR Statement DocType appears in ERPNext
3. Drop a known statement PDF into the Drive scan folder
4. Wait 15 min for poll → check that OCR Statement is created (not OCR Import)
5. Review reconciliation results
6. Test re-reconcile after manual supplier change
