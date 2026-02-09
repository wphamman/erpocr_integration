# ERPNext OCR Integration (erpocr_integration)

## Project Overview
Frappe custom app that integrates Nanonets OCR with ERPNext for automatic invoice data extraction and import.

**Repository goal**: A `bench get-app` installable Frappe app that works on both self-hosted and Frappe Cloud ERPNext instances.

## Architecture

### Frappe Custom App (not standalone middleware)
- Built as a Frappe custom app, NOT a separate FastAPI/Flask service
- Single install via `bench get-app`, config stored in DocTypes (UI-configurable)
- No external dependencies beyond ERPNext itself

### Pipeline Flow
```
Nanonets (On Approval) → Webhook (api.py) → Background Job (process_import.py)
  → Parse payload → Create OCR Import → Match supplier/items → Auto-create PI draft
```

### Key Components
| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Webhook endpoint — validates token, logs, enqueues |
| `erpocr_integration/tasks/process_import.py` | Core pipeline — parse payload, create OCR Import, match, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (alias table → exact name) |
| `erpocr_integration/tasks/utils.py` | Error logging helper |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), alias saving, status workflow |
| `erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.py` | Auto-generates webhook token on first save |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Nanonets API key, webhook token, default company/warehouse/expense account |
| **OCR Import** | Regular | Main staging record — extracted data, match status, link to created PI |
| **OCR Import Item** | Child Table | Line items on OCR Import — description, qty, rate, matched item_code |
| **OCR Supplier Alias** | Regular | Learning: OCR text → ERPNext Supplier |
| **OCR Item Alias** | Regular | Learning: OCR text → ERPNext Item |
| **OCR Request Log** | Regular | Webhook logging with 7-day auto-clear |

### OCR Import Status Workflow
Pending → Needs Review → Matched → Completed / Error

## Critical Implementation Patterns

### Permission Elevation in Background Jobs
```python
# frappe.enqueue() does NOT support user= kwarg — passes it to target function
# Instead, set user inside the enqueued function:
def process(raw_payload: str):
    frappe.set_user("Administrator")
    # ... rest of processing
```

### PI Creation
- `pi.flags.ignore_mandatory = True` for partial data
- `pi.insert(ignore_permissions=True)` — background job runs as Guest
- Set `bill_date` from OCR invoice_date; only set `due_date` if >= posting_date
- ERPNext overrides `posting_date` to today unless `set_posting_time=1`

### Webhook Security
- Token-based auth via URL query param: `?token=YOUR_TOKEN`
- Token auto-generated in OCR Settings via `frappe.generate_hash()`
- `@frappe.whitelist(allow_guest=True, methods=["POST"])`

### Background Processing
- Webhook returns HTTP 200 immediately, processing runs on `long` queue
- `frappe.db.commit()` required in enqueued jobs (with `# nosemgrep` comment)
- Failures logged to Error Log, don't break the import

### Matching System
1. Check alias table (exact match — learned from previous confirmations)
2. Check ERPNext master data by name (exact match)
3. If no match → status "Unmatched", user resolves manually
4. User confirmations saved as aliases for future auto-matching

## Nanonets Webhook Payload Structure
```json
{
  "result": [{
    "id": "file_id",
    "model_id": "xxx",
    "input": "filename.pdf",
    "approval_status": "approved",
    "moderated_boxes": [
      {"type": "field", "label": "supplier_name", "ocr_text": "Supplier Co"},
      {"type": "table", "label": "line_items", "cells": [
        {"row": 1, "col": 0, "label": "description", "text": "Widget A"}
      ]}
    ]
  }]
}
```
- Uses `moderated_boxes` (reviewed data) over `prediction`/`predicted_boxes`
- Fields: supplier_name, invoice_number, invoice_date, due_date, subtotal, tax_amount, total_amount
- Tables: cells grouped by row, row 0 is header (skipped)

## Environment & Deployment

### Development
- **Code location**: `c:\Users\wpham\ERPNextProjects\OCRIntegration`
- **Live ERPNext**: Remote server (production — install after testing)

### Local Docker Testing
- **Docker dir**: `c:\Users\wpham\erpnext-docker\frappe_docker`
- **Build context**: `c:\Users\wpham\erpnext-docker\custom-image\`
- **Site**: `ocr-test.local` (admin/admin)
- **Base image**: `frappe/erpnext:v15.71.1`
- **Rebuild workflow**:
  ```bash
  # Copy app to build context
  cp -r "c:/Users/wpham/ERPNextProjects/OCRIntegration/." "c:/Users/wpham/erpnext-docker/custom-image/erpocr_integration/"
  # Build image
  cd c:/Users/wpham/erpnext-docker/custom-image && docker build -t custom-erpnext:v15 -f Dockerfile.txt .
  # Restart containers
  cd c:/Users/wpham/erpnext-docker/frappe_docker && docker compose down && docker compose up -d
  ```

### Production Deployment
```bash
bench get-app https://github.com/<user>/erpocr_integration
bench --site <site> install-app erpocr_integration
# Configure OCR Settings in ERPNext UI
# Copy webhook URL + token → paste into Nanonets webhook export config
```

## Implementation Phases

### Phase 1 (v0.1) — Core Pipeline [COMPLETE AND TESTED]
- [x] Frappe app scaffold
- [x] All DocTypes (Settings, Import, Import Item, Aliases, Request Log)
- [x] Webhook endpoint — receive, validate, enqueue
- [x] Payload parser (header fields + table line items)
- [x] Supplier + item matching (alias → exact name)
- [x] Purchase Invoice draft auto-creation
- [x] OCR Import status workflow
- [x] Error handling + logging
- [x] Local Docker testing — full pipeline verified

### Phase 2 (v0.2) — Smart Matching + Tax Handling
- [ ] Fuzzy matching with ranked suggestions (difflib)
- [ ] OCR Import review UI improvements
- [ ] OCR Field Mapping DocType (configurable per Nanonets model)
- [ ] Tax template mapping (SA VAT 15% via Purchase Taxes and Charges Template)
- [ ] Git init + push to GitHub

### Phase 3 (v0.3) — Polish & Extend
- [ ] Journal Entry support
- [ ] Upload from ERPNext → Nanonets API
- [ ] Bulk import review
- [ ] Dashboard / statistics
- [ ] Test suite
- [ ] Production deployment on live server

## Open Questions
- Tax handling: Map OCR tax amounts to ERPNext Purchase Taxes and Charges Templates (SA VAT = 15%)
- Should OCR Import be submittable (lock after PI created)?
- Duplicate detection: Currently uses nanonets_file_id. Also consider invoice_number + supplier combo.
