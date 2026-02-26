# ERPNext OCR Integration (erpocr_integration)

## Project Overview
Frappe custom app that integrates Gemini 2.5 Flash API with ERPNext for automatic invoice data extraction and import. Supports PDF, JPEG, and PNG files.

**Repository goal**: A `bench get-app` installable Frappe app that works on both self-hosted and Frappe Cloud ERPNext instances.

**Cost**: ~$0.0001 per invoice (essentially free for small volume) vs Nanonets $0.30-0.50 per invoice

## Architecture

### Frappe Custom App (not standalone middleware)
- Built as a Frappe custom app, NOT a separate FastAPI/Flask service
- Single install via `bench get-app`, config stored in DocTypes (UI-configurable)
- Only external dependency: Gemini API (free tier: 15 RPM; Tier 1 pay-as-you-go: 1,000 RPM)

### Pipeline Flow
```
Manual Upload: User → Upload PDF/Image → Gemini API → Create OCR Import(s) → Match → User Review
Email:         Forward email → Hourly job → Gemini API → Create OCR Import(s) → Match → User Review
Drive Scan:    Drop file in folder → 15-min poll → Gemini API → Create OCR Import(s) → Match → User Review
                                                      ↓
                                      [Multi-invoice: one PDF → multiple OCR Imports]

User Review:   Review matches → Select Document Type → (optional) Link PO/PR → Create Document
               ├─ Purchase Invoice  (with optional PO + PR references)
               ├─ Purchase Receipt  (with optional PO reference)
               └─ Journal Entry     (expense receipts — restaurant bills, tolls, etc.)
```

### Design Philosophy
- **Reduce data entry**: system extracts data and suggests matches
- **Shift focus to review**: user reviews every suggestion before committing
- **No auto-creation**: documents are only created by explicit user action
- **Full override**: user can change any suggestion (supplier, items, document type, PO link)

### Key Components
| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Upload endpoint + `gemini_process()` background job (multi-invoice aware) |
| `erpocr_integration/tasks/gemini_extract.py` | Gemini API — extracts `invoices[]` array from PDF/image (supports multi-invoice PDFs) |
| `erpocr_integration/tasks/process_import.py` | Universal processing pipeline — match supplier/items, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (alias → exact → service mapping → fuzzy) |
| `erpocr_integration/tasks/email_monitor.py` | Email inbox polling — extracts PDF/image attachments from forwarded emails |
| `erpocr_integration/tasks/drive_integration.py` | Google Drive — upload, download, folder scan (PDF + images), move-to-archive |
| `erpocr_integration/public/js/ocr_import.js` | Upload button UI with real-time progress updates |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), create_purchase_receipt(), alias saving, status workflow |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Gemini API key, email/Drive config, default company/warehouse/tax templates, default item, default credit account |
| **OCR Import** | Regular | Main staging record — extracted data, match status, document type (PI/PR/JE), links to PO, PR, created PI/PR/JE |
| **OCR Import Item** | Child Table | Line items — description, qty, rate, matched item_code, PO item ref, PR item ref |
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
- Files sent as base64-encoded inline data with correct MIME type (max 10MB)
- Supported formats: PDF (`application/pdf`), JPEG (`image/jpeg`), PNG (`image/png`)
- Retry logic: 5 attempts with 429-specific long backoff (15s/30s/60s/120s) and shorter 5xx backoff (2s/5s/10s/20s)
- Drive scan staggers enqueue by 5s between files to avoid burst rate limiting
- Extraction time: 3-15 seconds depending on invoice complexity
- **Rate limits**: Free tier = 15 RPM / 1,500 RPD (will hit limits with batch uploads). Tier 1 (pay-as-you-go with billing linked) = 1,000 RPM / 10,000+ RPD. Check limits at https://aistudio.google.com/rate-limit

### Document Creation (PI / PR / JE)
- **Create dropdown** in top menu: Purchase Invoice, Purchase Receipt, Journal Entry — one click sets `document_type`, saves, and creates the draft
- `document_type` field still exists on the form for reference, but users interact via the Create menu
- **No auto-creation** — user clicks a Create menu option after reviewing all matches
- `flags.ignore_mandatory = True` on all created documents (drafts may have incomplete data)
- Set `bill_date` from OCR invoice_date; only set `due_date` if >= posting_date
- `default_item` in OCR Settings: used for unmatched PI items (non-stock item, OCR description set as item description)

### Purchase Order / Purchase Receipt Linking
- Optional: user can link OCR Import to an existing PO via "Find Open POs" button
- When PO selected, "Match PO Items" auto-matches OCR items to PO items by item_code (user reviews before applying)
- If PO has existing PRs, system surfaces them for selection — PR field constrained to PRs against the selected PO only
- PI items get both PO refs (`purchase_order` + `po_detail`) and PR refs (`purchase_receipt` + `pr_detail`) — closes full PO→PR→PI chain
- PR items get PO refs (`purchase_order` + `purchase_order_item`) — different field names from PI (ERPNext v15 schema)
- Stale field clearing: changing supplier clears PO/PR; changing PO clears PR and all item-level refs

### Journal Entry Creation
- For expense receipts (restaurant bills, toll slips, entertainment) that don't need PI/PR
- Requires: expense_account on items + credit_account (from field or OCR Settings default)
- Builds balanced JE: debit lines per item → expense accounts, credit line → bank/payable
- Tax handled as separate debit line to VAT input account (if tax detected)
- Account validation: all accounts must belong to company, not be group or disabled

### Server-Side Guards
- **Document type enforcement**: each create method validates `document_type` matches (prevents API bypass)
- **Cross-document lock**: row-lock checks all three output fields (PI, PR, JE) — only one document per OCR Import
- **PO/PR linkage validation**: at create time, re-verifies PR belongs to selected PO (server-side, not just UI)
- **Row-level permissions**: `match_po_items` and `match_pr_items` check per-document read permission (not just doctype-level)
- **XSS prevention**: all dynamic values in PO/PR/match dialogs escaped via `frappe.utils.escape_html()` and `encodeURIComponent()`
- **Tax ambiguity threshold**: `_detect_tax_inclusive_rates()` returns False (default exclusive) when inclusive vs exclusive difference is < 5% of tax amount
- **Account validation (JE)**: credit/expense/tax accounts checked for company, is_group=0, disabled=0

### Upload Security
- Permission check: User must have "create" permission on OCR Import
- File validation: PDF, JPEG, PNG only; max 10MB; magic bytes verified
- Whitelisted endpoint: `@frappe.whitelist(methods=["POST"])`

### Background Processing
- Upload creates placeholder OCR Import immediately, returns record name
- Uploaded file saved as private Frappe File attachment (enables retry on failure)
- Processing runs on `long` queue (5-minute timeout)
- Real-time progress updates via `frappe.publish_realtime()`
- `frappe.db.commit()` required in enqueued jobs (with `# nosemgrep` comment)
- Failures logged to Error Log, status set to "Error"
- **Retry on error**: "Retry Extraction" button on all Error records — reads from Drive file or local attachment

### Matching System
Matching runs in priority order for both suppliers and items:
1. **Alias table** (exact match — learned from previous confirmations)
2. **ERPNext master data** by name (exact match)
3. **Service mapping** (pattern-based: description substring → item + GL account + cost center)
4. **Fuzzy matching** (difflib SequenceMatcher, configurable threshold, returns "Suggested" status)
5. If no match → status "Unmatched", user resolves manually
6. User confirmations saved as aliases for future auto-matching

Service mappings support supplier-specific patterns (higher priority) and generic patterns.

Both pattern storage and runtime matching use `normalize_for_matching()` (strips punctuation, lowercases, collapses whitespace) so patterns match regardless of formatting differences between invoices.

When saving service mappings, `_extract_service_pattern()` strips dates (DD/MM/YYYY, YYYY-MM-DD with plausible day/month bounds), month names, years (1900-2199), and trailing prepositions from OCR descriptions to produce reusable patterns (e.g., "Pro Plan - Jan 2026 to Feb 2026" → "pro plan"). A quality guard rejects patterns that reduce to only stop words (e.g., "for", "of the") and falls back to the full normalized description.

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
- Wrapped in `invoices[]` array — supports multi-invoice PDFs (one PDF → multiple OCR Imports); images always produce a single invoice
- Gemini returns data in this structure (enforced by response_schema)
- Dates auto-converted to YYYY-MM-DD
- Currency symbols stripped from amounts
- Product codes extracted separately from descriptions

## Deployment

### Installation
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

### Phase 3 (v0.3) — Polish & Enhancements [COMPLETE]
- [x] Fuzzy matching with configurable threshold (difflib SequenceMatcher)
- [x] Tax template mapping (auto-set VAT vs non-VAT based on tax detection)
- [x] Service mapping (OCR Service Mapping doctype — pattern → item + GL + cost center)
- [x] Multi-invoice PDF support (one PDF → multiple OCR Imports)
- [x] Google Drive folder polling (15-min scan inbox + move to archive)
- [x] Google Drive archiving (Year/Month/Supplier folder structure)
- [x] Batch upload (covered by Drive scan folder — drop multiple PDFs, auto-processed)
- [x] OCR confidence scores (Gemini self-reported, color-coded badge on form)
- [x] Dashboard workspace (number cards, status chart, shortcuts, link cards)
- [x] ~~PI vs PR auto-detection~~ (replaced by user-driven selection in Phase 4)
- [x] Default Item for unmatched lines (configurable in OCR Settings)
- [x] Purchase Receipt creation method (create_purchase_receipt)
- [x] OCR Manager role for access control

### Phase 4 (v0.4) — User-Driven Workflow + PO Linking + JE [COMPLETE]
- [x] Blank document_type default (user must select PI/PR/JE)
- [x] Remove auto-creation of documents (no _auto_create_documents, no _detect_document_type)
- [x] Journal Entry creation for expense receipts (with account validation)
- [x] Purchase Order linking (find open POs, match items, apply refs)
- [x] Purchase Receipt linking for PI creation (constrained by PO, closes full chain)
- [x] Hardened server-side guards (document_type enforcement, cross-doc duplicate lock)
- [x] Stale field clearing (supplier/PO/PR cascade)
- [x] Migration patch (normalize document_type on in-flight records)
- [x] Test suite (220 tests — unit tests + integration workflow tests)
- [x] Image support: JPEG and PNG accepted alongside PDF (upload, email, Drive scan)

### Future — Email Monitor Hardening
- [ ] Replace X-GM-LABELS label manipulation with standard IMAP COPY + DELETE for moving emails from "OCR Invoices" to "OCR Processed" (more reliable across Gmail Workspace)
- [ ] Add `\Seen` flag removal guard after Phase 2 read-write operations (prevent accidentally marking emails as read)

## Configuration

### Getting Gemini API Key
1. Visit https://aistudio.google.com/apikey
2. Sign in with Google account
3. Click "Create API key"
4. Copy the key (starts with `AIza...`)

**Important — Free tier rate limits:** The free tier allows only 15 requests/minute and 1,500 requests/day. Batch uploads via Drive scan or email can easily exceed these limits, causing 429 errors and failed extractions. To avoid this, link a billing account to your Google AI project (Google AI Studio > Settings). This upgrades to Tier 1 (1,000 RPM, 10,000+ RPD) at minimal cost (~$0.0001 per invoice). Check your current limits at https://aistudio.google.com/rate-limit.

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
- **Default Item**: Non-stock item used for unmatched line items (OCR description becomes the item description)
- **Default Credit Account**: Default credit account for Journal Entries (e.g., Accounts Payable, Petty Cash, Bank)
- **Matching Threshold**: Minimum similarity score (0-100) for fuzzy matching (default: 80)

### Usage

**Manual Upload**:
1. Go to: OCR Import > New
2. Click "Upload File" button → select PDF, JPEG, or PNG file (max 10MB)
3. Wait 5-30 seconds for extraction
4. Review/confirm supplier and item matches (change status to "Confirmed" to save aliases)
5. **(Optional)** Link to Purchase Order: click "Find Open POs" → select PO → "Match PO Items" → review → apply
6. **(Optional)** If PO has existing PR: select it to link the full PO→PR→PI chain
7. Click **Create** dropdown (top right) → select Purchase Invoice, Purchase Receipt, or Journal Entry
8. For Journal Entry: set expense accounts on items + credit account before clicking Create
9. Document draft created for final review in ERPNext
10. If extraction fails: click **Retry Extraction** button to re-process

**Email Upload**:
1. Forward invoice email to configured email address
2. Hourly job automatically extracts PDF and image attachments
3. Follow steps 3-10 above

**Drive Scan (Batch)**:
1. Drop PDF or image files into the configured Drive scan inbox folder
2. Every 15 minutes, new files (PDF/JPEG/PNG) are automatically downloaded and processed
3. After extraction, files are moved to the archive folder (Year/Month/Supplier)
4. Multi-invoice PDFs (statements) are split into separate OCR Import records
5. Failed extractions are automatically retried on the next poll
6. Follow steps 3-10 from Manual Upload for each created OCR Import

**Supported Workflows**:
| Scenario | Document Type | PO Link? | PR Link? |
|---|---|---|---|
| Raw materials with PO, delivery received, invoice arrives | Purchase Invoice | Yes | Yes (existing PR) |
| Raw materials with PO, creating receipt from delivery note | Purchase Receipt | Yes | N/A |
| Service/subscription invoice, no PO | Purchase Invoice | No | No |
| Restaurant receipt, toll slip, entertainment expense | Journal Entry | No | No |
