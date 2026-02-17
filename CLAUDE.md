# ERPNext OCR Integration (erpocr_integration)

## Project Overview
Frappe custom app that integrates Gemini 2.5 Flash API with ERPNext for automatic invoice data extraction and import.

**Repository goal**: A `bench get-app` installable Frappe app that works on both self-hosted and Frappe Cloud ERPNext instances.

**Cost**: ~$0.0001 per invoice (essentially free for small volume) vs Nanonets $0.30-0.50 per invoice

## Architecture

### Frappe Custom App (not standalone middleware)
- Built as a Frappe custom app, NOT a separate FastAPI/Flask service
- Single install via `bench get-app`, config stored in DocTypes (UI-configurable)
- Only external dependency: Gemini API (free tier: 15 RPM)

### Pipeline Flow
```
Manual Upload: User → Upload PDF → Gemini API → Create OCR Import(s) → Match → PI Draft
Email:         Forward email → Hourly job → Gemini API → Create OCR Import(s) → Match → PI Draft
Drive Scan:    Drop PDF in folder → 15-min poll → Gemini API → Create OCR Import(s) → Match → PI Draft
                                                      ↓
                                      [Multi-invoice: one PDF → multiple OCR Imports]
```

### Key Components
| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Upload endpoint + `gemini_process()` background job (multi-invoice aware) |
| `erpocr_integration/tasks/gemini_extract.py` | Gemini API — extracts `invoices[]` array from PDF (supports multi-invoice) |
| `erpocr_integration/tasks/process_import.py` | Universal processing pipeline — match supplier/items, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (alias → exact → service mapping → fuzzy) |
| `erpocr_integration/tasks/email_monitor.py` | Email inbox polling — extracts PDFs from forwarded emails |
| `erpocr_integration/tasks/drive_integration.py` | Google Drive — upload, download, folder scan, move-to-archive |
| `erpocr_integration/public/js/ocr_import.js` | Upload button UI with real-time progress updates |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), alias saving, status workflow |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Gemini API key, email/Drive config, default company/warehouse/tax templates |
| **OCR Import** | Regular | Main staging record — extracted data, match status, link to created PI |
| **OCR Import Item** | Child Table | Line items on OCR Import — description, qty, rate, matched item_code |
| **OCR Supplier Alias** | Regular | Learning: OCR text → ERPNext Supplier |
| **OCR Item Alias** | Regular | Learning: OCR text → ERPNext Item |
| **OCR Service Mapping** | Regular | Pattern → item + GL account + cost center (supplier-specific or generic) |

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

### Gemini API Integration
- Uses Gemini 2.5 Flash API with structured output (JSON schema)
- PDF sent as base64-encoded inline data (max 10MB currently, can increase to 20MB)
- Retry logic with exponential backoff for rate limits (429 errors)
- Extraction time: 3-15 seconds depending on invoice complexity

### PI Creation
- `pi.flags.ignore_mandatory = True` for partial data
- `pi.insert(ignore_permissions=True)` — background job runs as Administrator
- Set `bill_date` from OCR invoice_date; only set `due_date` if >= posting_date
- ERPNext overrides `posting_date` to today unless `set_posting_time=1`

### Upload Security
- Permission check: User must have "create" permission on OCR Import
- File validation: PDF only, max 10MB
- Whitelisted endpoint: `@frappe.whitelist(methods=["POST"])`

### Background Processing
- Upload creates placeholder OCR Import immediately, returns record name
- Processing runs on `long` queue (5-minute timeout)
- Real-time progress updates via `frappe.publish_realtime()`
- `frappe.db.commit()` required in enqueued jobs (with `# nosemgrep` comment)
- Failures logged to Error Log, status set to "Error"

### Matching System
Matching runs in priority order for both suppliers and items:
1. **Alias table** (exact match — learned from previous confirmations)
2. **ERPNext master data** by name (exact match)
3. **Service mapping** (pattern-based: description substring → item + GL account + cost center)
4. **Fuzzy matching** (difflib SequenceMatcher, configurable threshold, returns "Suggested" status)
5. If no match → status "Unmatched", user resolves manually
6. User confirmations saved as aliases for future auto-matching

Service mappings support supplier-specific patterns (higher priority) and generic patterns.

## Gemini Structured Output Schema
```json
{
  "invoices": [
    {
      "supplier_name": "string (required)",
      "supplier_tax_id": "string (empty if not present)",
      "invoice_number": "string (required)",
      "invoice_date": "YYYY-MM-DD (required)",
      "due_date": "YYYY-MM-DD (empty if not present)",
      "subtotal": "number (0 if not shown)",
      "tax_amount": "number (0 if not shown)",
      "total_amount": "number (required)",
      "currency": "string (e.g. USD, ZAR, EUR)",
      "line_items": [
        {
          "description": "string (required)",
          "product_code": "string (empty if not present)",
          "quantity": "number (required)",
          "unit_price": "number (required)",
          "amount": "number (required)"
        }
      ]
    }
  ]
}
```
- Wrapped in `invoices[]` array — supports multi-invoice PDFs (one PDF → multiple OCR Imports)
- Gemini returns data in this structure (enforced by response_schema)
- Dates auto-converted to YYYY-MM-DD
- Currency symbols stripped from amounts
- Product codes extracted separately from descriptions

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
bench get-app https://github.com/wphamman/erpocr_integration
bench --site <site> install-app erpocr_integration
bench --site <site> migrate
bench restart

# Configure OCR Settings in ERPNext UI:
# 1. Navigate to: Setup > OCR Settings
# 2. Enter Gemini API Key (get from https://aistudio.google.com/apikey)
# 3. Select Model: gemini-2.5-flash
# 4. Configure ERPNext defaults (company, warehouse, expense account, cost center)
# 5. Set VAT Tax Template and Non-VAT Tax Template
# 6. Optional: Enable email monitoring and select Email Account
# 7. Optional: Enable Google Drive Integration with service account JSON and folder IDs
```

## Implementation Phases

### Phase 1 (v0.1) — Nanonets Pipeline [COMPLETE - DEPRECATED]
- [x] Webhook-based Nanonets integration (replaced by Gemini)

### Phase 2 (v0.2) — Gemini Integration [COMPLETE]
- [x] Gemini 2.5 Flash API integration (gemini_extract.py)
- [x] Manual PDF upload via OCR Import form
- [x] Upload endpoint with file validation
- [x] Background processing with real-time progress
- [x] Email monitoring (hourly scheduled job)
- [x] Supplier + item matching (reused from Phase 1)
- [x] Purchase Invoice draft auto-creation (reused from Phase 1)
- [x] Error handling + logging
- [x] Removed Nanonets code

### Phase 3 (v0.3) — Polish & Enhancements [IN PROGRESS]
- [x] Fuzzy matching with configurable threshold (difflib SequenceMatcher)
- [x] Tax template mapping (auto-set VAT vs non-VAT based on tax detection)
- [x] Service mapping (OCR Service Mapping doctype — pattern → item + GL + cost center)
- [x] Multi-invoice PDF support (one PDF → multiple OCR Imports)
- [x] Google Drive folder polling (15-min scan inbox + move to archive)
- [x] Google Drive archiving (Year/Month/Supplier folder structure)
- [ ] Multi-file upload in UI (drag & drop multiple PDFs)
- [ ] OCR confidence scores from Gemini metadata
- [ ] Custom prompt per company
- [ ] Dashboard / statistics
- [ ] Test suite

## Configuration

### Getting Gemini API Key
1. Visit https://aistudio.google.com/apikey
2. Sign in with Google account
3. Click "Create API key"
4. Copy the key (starts with `AIza...`)

### OCR Settings
- **Gemini API Key**: Your API key from Google AI Studio
- **Gemini Model**: gemini-2.5-flash (recommended), gemini-2.5-pro, gemini-2.0-flash
- **Enable Email Monitoring**: Check to enable automatic email processing
- **Email Account**: Select ERPNext Email Account to monitor for invoice PDFs
- **Enable Drive Integration**: Archive processed invoices to Google Drive
- **Service Account JSON**: Paste Google Cloud service account key JSON
- **Archive Folder ID**: Google Drive folder ID for organized archive
- **Scan Inbox Folder ID**: Google Drive folder ID polled every 15 minutes for new PDFs
- **VAT Tax Template**: Applied when OCR detects tax on the invoice
- **Non-VAT Tax Template**: Applied when no tax detected (foreign/non-VAT suppliers)
- **Matching Threshold**: Minimum similarity score (0-100) for fuzzy matching (default: 80)

### Usage
**Manual Upload**:
1. Go to: OCR Import > New
2. Click "Upload PDF" button
3. Select PDF file (max 10MB)
4. Wait 5-30 seconds for extraction
5. Review/confirm supplier and item matches
6. PI draft auto-created if all matched

**Email Upload**:
1. Forward invoice email to configured email address
2. Hourly job automatically extracts PDFs
3. Follow steps 4-6 above

**Drive Scan (Batch)**:
1. Drop PDF(s) into the configured Drive scan inbox folder
2. Every 15 minutes, new PDFs are automatically downloaded and processed
3. After extraction, PDFs are moved to the archive folder (Year/Month/Supplier)
4. Multi-invoice PDFs (statements) are split into separate OCR Import records
5. Failed extractions are automatically retried on the next poll

## Open Questions
- Should OCR Import be submittable (lock after PI created)?
