# ERPNext OCR Integration (erpocr_integration)

## Project Overview
Frappe custom app that integrates Nanonets OCR with ERPNext for automatic invoice data extraction and import. Designed to be generic and open-sourceable.

**Repository goal**: A `bench get-app` installable Frappe app that works on both self-hosted and Frappe Cloud ERPNext instances.

**Reference project**: [woocommerce_fusion](https://github.com/Starktail/woocommerce_fusion) — we follow its patterns for app structure, logging, error handling, background jobs, and hooks.

## Architecture Decisions

### Frappe Custom App (not standalone middleware)
- **Decided**: Build as a Frappe custom app, NOT a separate FastAPI/Flask service
- **Reasons**: Single install via `bench get-app`, config stored in DocTypes (UI-configurable), direct DB access for supplier/item matching, works on Frappe Cloud without extra infrastructure
- No external dependencies beyond ERPNext itself (fuzzy matching via difflib, no extra pip packages)

### Nanonets Webhook Flow
- Nanonets webhook configured to fire **"On Approval"** — human verifies OCR accuracy in Nanonets before data reaches ERPNext
- Webhook endpoint exposed via `@frappe.whitelist(allow_guest=True)` at `/api/method/erpocr_integration.api.webhook`
- Nanonets auth: Basic Auth (API key as username, empty password)
- Nanonets prediction payload contains `prediction[]` with `label`, `ocr_text`, `score`, `type` ("field" or "table" with `cells[]`)
- Webhook security: Token-based (secret in URL query param), since Nanonets doesn't support HMAC payload signing

### Document Creation
- **Purchase Invoice** as first supported document type (v1)
- Always created as **Draft** — user reviews in ERPNext before submitting
- Journal Entry support planned for later

### Smart Matching with Learning (Alias System)
- **OCR Supplier Alias**: Maps OCR extracted text → ERPNext Supplier. Grows over time as users confirm matches.
- **OCR Item Alias**: Maps OCR extracted text → ERPNext Item. Same learning pattern.
- Matching priority:
  1. Exact match in alias table (instant, learned from previous approvals)
  2. Fuzzy match against ERPNext master data (suggests to user)
  3. No match → user manually links or creates entity first
- Fuzzy matching uses Python `difflib.SequenceMatcher` (no external deps)

### OCR Import Review Workflow (in ERPNext)
- **OCR Import** DocType is the central review/staging screen
- Shows: extracted data, match status (auto-matched / suggested / unmatched)
- Supplier & item fields with suggestion dropdowns + "Create New" option
- "Create Purchase Invoice" action once all matches resolved
- Stores link to created PI for audit trail
- Match confirmations automatically saved as aliases for future use

### Google Drive Companion (Optional, Phase 3+)
- **NOT part of the Frappe app** — separate optional component
- Manages file lifecycle: shared scan folder → archive after Nanonets ingests → supplier folder after approval
- Likely a Google Apps Script or small Python script
- Keeps the Frappe app focused and decoupled from Google

## Document Input Methods (Nanonets side)
| Method | How it works | Best for |
|---|---|---|
| Email | Forward invoices to Nanonets email address | Emailed supplier invoices |
| Google Drive | Drop files into connected folder | Batch uploads, shared scanning |
| Nanonets web UI | Upload directly in app.nanonets.com | Manual one-offs |
| Mobile phone | Take photo → email to Nanonets address | Physical invoices/slips in the field |
| ERPNext upload | Future: button in ERPNext sends to Nanonets API | Keeping everything in one UI (Phase 3) |

## Patterns Adopted from woocommerce_fusion

### App Structure
```
erpocr_integration/
├── erpocr_integration/
│   ├── __init__.py
│   ├── hooks.py                    # Scheduler events, fixtures, doc_events
│   ├── modules.txt                 # "ERPNext OCR"
│   ├── patches.txt                 # Data migration patches
│   ├── api.py                      # Webhook endpoint (@frappe.whitelist)
│   ├── exceptions.py               # Custom exception classes
│   │
│   ├── tasks/                      # Background processing logic
│   │   ├── __init__.py
│   │   ├── process_import.py       # Core: parse payload → match → create PI
│   │   ├── matching.py             # Supplier & item matching (alias + fuzzy)
│   │   └── utils.py                # Shared utilities
│   │
│   ├── erpnext_ocr/                # Module directory (DocTypes live here)
│   │   ├── __init__.py
│   │   └── doctype/
│   │       ├── ocr_settings/       # Single DocType — global config
│   │       ├── ocr_import/         # Main import/review record
│   │       ├── ocr_import_item/    # Child table — line items on OCR Import
│   │       ├── ocr_supplier_alias/ # Learning: OCR text → Supplier
│   │       ├── ocr_item_alias/     # Learning: OCR text → Item
│   │       ├── ocr_field_mapping/  # Nanonets label → ERPNext field config
│   │       └── ocr_request_log/    # API request/webhook logging
│   │
│   ├── public/js/                  # Client-side customizations (if needed)
│   ├── fixtures/                   # Custom fields on existing doctypes
│   └── tests/
│
├── pyproject.toml                  # flit_core, ruff config, deps
├── .github/workflows/ci.yml
├── LICENSE
└── README.md
```

### Error Handling (from woocommerce_fusion)
```python
# Pattern: log full context, give user a clickable link
def log_and_raise_error(exception=None, error_text=None):
    error_message = frappe.get_traceback() if exception else ""
    error_message += f"\n{error_text}" if error_text else ""
    log = frappe.log_error("OCR Integration Error", error_message)
    log_link = frappe.utils.get_link_to_form("Error Log", log.name)
    frappe.throw(msg=_("OCR processing failed. See Error Log {0}").format(log_link))
```

### Request/Webhook Logging (from woocommerce_fusion)
- **OCR Request Log** DocType records every webhook received + any outbound API calls
- Logged asynchronously via `frappe.enqueue()` to avoid blocking
- Auto-cleared after 7 days via `default_log_clearing_doctypes` in hooks.py
- Fields: timestamp, endpoint, method, request_data, response_data, status, error, time_elapsed

### Background Processing (from woocommerce_fusion)
- Webhook endpoint receives payload → immediately enqueues processing on `long` queue
- Returns HTTP 200 quickly (don't make Nanonets wait)
- Pattern: `frappe.enqueue("erpocr_integration.tasks.process_import.process", queue="long", ...)`
- Individual record failures caught and logged, don't block other records

### Build Configuration (from woocommerce_fusion)
- `pyproject.toml` with `flit_core` build system
- Ruff linting: F, E, W, I, UP, B, RUF rule sets
- Line length: 110, double quotes, tab indentation
- Python >= 3.10

### Hooks Pattern (from woocommerce_fusion)
```python
# hooks.py
default_log_clearing_doctypes = {
    "OCR Request Log": 7  # Auto-clear after 7 days
}
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "ERPNext OCR"]]}
]
```

## Planned DocTypes

| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Nanonets API key, default model ID, webhook secret/token, default expense account, matching confidence threshold, default company/warehouse |
| **OCR Import** | Regular | Main import/review record — extracted data, match status, link to created PI, approval workflow |
| **OCR Import Item** | Child Table | Line items on OCR Import — extracted description, qty, rate, matched item_code, match status |
| **OCR Field Mapping** | Regular | Maps Nanonets labels → ERPNext fields, per model/document type |
| **OCR Supplier Alias** | Regular | Maps OCR text variations → ERPNext Supplier (learning system) |
| **OCR Item Alias** | Regular | Maps OCR text variations → ERPNext Item (learning system) |
| **OCR Request Log** | Regular | Webhook/API request logging with auto-clear |

### OCR Import Statuses (workflow)
| Status | Meaning |
|---|---|
| **Pending** | Webhook received, processing queued |
| **Needs Review** | Parsed but has unmatched suppliers or items — user action needed |
| **Matched** | All suppliers/items resolved — ready to create PI |
| **Completed** | Purchase Invoice draft created successfully |
| **Error** | Processing failed — see linked Error Log |

## Nanonets API Reference (for this project)

### Authentication
- Basic Auth: API key as username, empty password
- Header: `Authorization: Basic base64(api_key:)`

### Key Endpoints
- **Upload (sync)**: `POST https://app.nanonets.com/api/v2/OCR/Model/{model_id}/LabelFile/`
- **Upload (async, >3 pages)**: Same URL with `?async=true`
- **Get prediction**: `GET https://app.nanonets.com/api/v2/Inferences/Model/{model_id}/InferenceRequestFiles/GetPredictions/{request_file_id}`

### Webhook Payload Structure (On Approval)
```json
{
  "message": "Success",
  "result": [{
    "message": "Success",
    "input": "filename.pdf",
    "prediction": [
      {"label": "field_name", "ocr_text": "value", "score": 0.95, "type": "field"},
      {"label": "table_name", "type": "table", "cells": [
        {"row": 1, "col": 1, "label": "col_header", "text": "cell_value", "score": 99.9}
      ]}
    ],
    "moderated_boxes": [...],
    "is_moderated": true,
    "approval_status": "approved",
    "model_id": "xxx",
    "id": "file_id"
  }]
}
```

### Webhook Triggers Available
- On Inference, On Approval, On All Validations Passing, On Assignment, On Rejection
- We use **On Approval** for the PI workflow

### Key Prediction Labels (Nanonets Invoice Model)
- Fields: `supplier_name`, `invoice_number`, `invoice_date`, `due_date`, `total_amount`, `tax_amount`, `subtotal`
- Table: `line_items` with cells for `description`, `quantity`, `unit_price`, `amount`
- Exact labels depend on the user's Nanonets model config — hence the Field Mapping DocType

## ERPNext API Notes
- See global CLAUDE.md for full ERPNext API reference
- Purchase Invoice: `POST /api/resource/Purchase Invoice` with `supplier`, `posting_date`, `items[]`
- PI supports line items without `item_code` (description + expense_account + qty + rate only)
- `docstatus=0` for draft
- Use `flags.ignore_mandatory = True` pattern from woocommerce_fusion for partial data

## Environment & Deployment

### Development
- **Code location**: `c:\Users\wpham\ERPNextProjects\OCRIntegration`
- **GitHub repo**: https://github.com/wphamman/erpocr_integration
- **Live ERPNext**: Remote server (production — install after testing)

### Local Docker ERPNext (for testing)
- **Docker setup**: `c:\Users\wpham\erpnext-docker\frappe_docker`
- **Existing custom app**: `cactuscraft_custom` (uses custom Dockerfile pattern)
- **Custom image Dockerfile**: `c:\Users\wpham\erpnext-docker\custom-image\Dockerfile.txt`
- **Pattern**: Build custom Docker image that copies app into `/home/frappe/frappe-bench/apps/`

### Production Deployment
```bash
bench get-app https://github.com/<user>/erpocr_integration
bench --site <site> install-app erpocr_integration
# Configure OCR Settings in ERPNext UI
# Copy webhook URL + token → paste into Nanonets webhook export config
```

## Implementation Phases

### Phase 1 (v0.1) — Core Pipeline [COMPLETE — code written, needs testing]
- [x] Frappe app scaffold (pyproject.toml, hooks.py, modules.txt)
- [x] OCR Settings DocType (Single)
- [x] OCR Request Log DocType + auto-clear
- [x] Webhook endpoint — receive, validate token, enqueue
- [x] OCR Import DocType with basic fields + OCR Import Item child table
- [x] OCR Supplier Alias + OCR Item Alias DocTypes (learning system)
- [x] Nanonets payload parser (fields + table extraction)
- [x] Basic supplier matching (alias → exact name match)
- [x] Basic item matching (alias → exact name/description match)
- [x] Purchase Invoice draft creation from matched OCR Import
- [x] OCR Import status workflow (Pending → Needs Review → Matched → Completed)
- [x] Error handling + logging patterns
- [ ] **Local Docker ERPNext setup for testing**
- [ ] **Test with sample Nanonets payload**

### Phase 2 (v0.2) — Smart Matching UI + Fuzzy Matching
- [ ] Fuzzy matching with ranked suggestions (difflib)
- [ ] OCR Import review UI — suggestion dropdowns, "Create New" links
- [ ] OCR Field Mapping DocType (configurable per Nanonets model)
- [ ] Matching confidence threshold in settings (currently stored, not yet used)

### Phase 3 (v0.3) — Polish & Extend
- [ ] Journal Entry support
- [ ] Upload from ERPNext button (send file to Nanonets API)
- [ ] Google Drive companion script (optional, separate repo/folder)
- [ ] Bulk import review
- [ ] Dashboard / statistics
- [ ] Documentation for open-source release
- [ ] Frappe Cloud compatibility testing
- [ ] Test suite (unit + integration, following woocommerce_fusion patterns)

## Key Implementation Files

| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Webhook endpoint — validates token, logs, enqueues |
| `erpocr_integration/tasks/process_import.py` | Core pipeline — parse payload, create OCR Import, match, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (alias table → exact name) |
| `erpocr_integration/tasks/utils.py` | Error logging helper (woocommerce_fusion pattern) |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), alias saving, status workflow |
| `erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.py` | Auto-generates webhook token on first save |

## Open Questions
- How to handle multi-page invoices (Nanonets sends page-level or document-level)? → Configure as document-level in Nanonets webhook
- Should OCR Import be submittable (lock after PI created)? → Probably yes, prevents re-processing
- Tax handling: How to map OCR-extracted tax amounts to ERPNext tax templates?
- Do we need multi-Nanonets-model support (like woocommerce_fusion's multi-server)? → Probably not for v1
- Duplicate detection: Currently uses nanonets_file_id uniqueness check. Also consider invoice_number + supplier combo.
