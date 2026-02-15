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
Manual Upload: User → Upload PDF → Gemini API → Create OCR Import → Match → PI Draft
Email: Forward email → Hourly job → Gemini API → Create OCR Import → Match → PI Draft
                                        ↓
                        [Existing matching/PI creation pipeline]
```

### Key Components
| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Upload endpoint — validates user, creates OCR Import, enqueues processing |
| `erpocr_integration/tasks/gemini_extract.py` | Gemini API integration — extracts structured invoice data from PDF |
| `erpocr_integration/tasks/process_import.py` | Universal processing pipeline — match supplier/items, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (alias table → exact name) |
| `erpocr_integration/tasks/email_monitor.py` | Email inbox polling — extracts PDFs from forwarded emails |
| `erpocr_integration/public/js/ocr_import.js` | Upload button UI with realtime progress updates |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), alias saving, status workflow |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Gemini API key, email monitoring config, default company/warehouse/expense account |
| **OCR Import** | Regular | Main staging record — extracted data, match status, link to created PI |
| **OCR Import Item** | Child Table | Line items on OCR Import — description, qty, rate, matched item_code |
| **OCR Supplier Alias** | Regular | Learning: OCR text → ERPNext Supplier |
| **OCR Item Alias** | Regular | Learning: OCR text → ERPNext Item |

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
1. Check alias table (exact match — learned from previous confirmations)
2. Check ERPNext master data by name (exact match)
3. If no match → status "Unmatched", user resolves manually
4. User confirmations saved as aliases for future auto-matching

## Gemini Structured Output Schema
```json
{
  "supplier_name": "string (required)",
  "supplier_tax_id": "string | null",
  "invoice_number": "string (required)",
  "invoice_date": "YYYY-MM-DD (required)",
  "due_date": "YYYY-MM-DD | null",
  "subtotal": "number | null",
  "tax_amount": "number | null",
  "total_amount": "number (required)",
  "line_items": [
    {
      "description": "string (required)",
      "product_code": "string | null",
      "quantity": "number (required)",
      "unit_price": "number (required)",
      "amount": "number (required)"
    }
  ]
}
```
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
# 5. Optional: Enable email monitoring and select Email Account
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

### Phase 3 (v0.3) — Polish & Future Enhancements
- [ ] Fuzzy matching with ranked suggestions (difflib)
- [ ] Tax template mapping (SA VAT 15% via Purchase Taxes and Charges Template)
- [ ] Batch upload (multiple PDFs at once)
- [ ] OCR confidence scores from Gemini metadata
- [ ] Custom prompt per company
- [ ] Google Drive folder polling
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
- **Gemini Model**: gemini-2.5-flash (recommended), gemini-2.0-flash, or gemini-1.5-flash
- **Enable Email Monitoring**: Check to enable automatic email processing
- **Email Account**: Select ERPNext Email Account to monitor for invoice PDFs

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

## Open Questions
- Tax handling: Map OCR tax amounts to ERPNext Purchase Taxes and Charges Templates (SA VAT = 15%)
- Should OCR Import be submittable (lock after PI created)?
- Retry mechanism: Store original PDF for failed extractions?
