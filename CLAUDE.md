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

DN Pipeline:   Drop DN scan in Drive folder → 15-min poll → Gemini API → Create OCR Delivery Note → Match → Review
               ├─ Link PO → Create Purchase Receipt (rates from PO)
               └─ No PO → Create Purchase Order (draft, rates filled by accounts team)

Fleet Pipeline: Drop fleet slip scan in Drive folder → 15-min poll → Gemini API → Create OCR Fleet Slip → Vehicle Match → Review
                ├─ Fleet Card vehicle → Create Purchase Invoice (supplier = fleet card provider)
                └─ Direct Expense vehicle → Create Purchase Invoice (supplier = default from OCR Settings)
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
| `erpocr_integration/dn_api.py` | DN background processing (`dn_gemini_process`), PO matching, doc_event hooks, retry |
| `erpocr_integration/erpnext_ocr/doctype/ocr_delivery_note/ocr_delivery_note.py` | OCR DN controller — create_purchase_order(), create_purchase_receipt(), unlink, no_action |
| `erpocr_integration/public/js/ocr_delivery_note.js` | DN client: PO matching dialogs (qty-focused), Create dropdown (PO/PR), real-time status |
| `erpocr_integration/fleet_api.py` | Fleet background processing (`fleet_gemini_process`), vehicle matching, doc_event hooks, retry |
| `erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py` | OCR Fleet Slip controller — create_purchase_invoice(), unlink, no_action, status workflow |
| `erpocr_integration/public/js/ocr_fleet_slip.js` | Fleet client: Create PI button, vehicle config display, status intro, unauthorized warning |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Gemini API key, email/Drive config, default company/warehouse/tax templates, default item, default credit account |
| **OCR Import** | Regular | Main staging record — extracted data, match status, document type (PI/PR/JE), links to PO, PR, created PI/PR/JE |
| **OCR Import Item** | Child Table | Line items — description, qty, rate, matched item_code, PO item ref, PR item ref |
| **OCR Supplier Alias** | Regular | Learning: OCR text → ERPNext Supplier |
| **OCR Item Alias** | Regular | Learning: OCR text → ERPNext Item |
| **OCR Service Mapping** | Regular | Pattern → item + GL account + cost center (supplier-specific or generic) |
| **OCR Delivery Note** | Regular | DN staging record — extracted qty/item data, PO matching, creates PO or PR |
| **OCR Delivery Note Item** | Child Table | DN line items — description, qty, uom, matched item_code, PO item ref |
| **OCR Fleet Slip** | Regular | Fleet slip staging — fuel/toll/other classification, vehicle matching, always creates Purchase Invoice |

### OCR Import Status Workflow
Pending → Needs Review → Matched → Draft Created → Completed / Error

- **Draft Created**: set when a PI/PR/JE draft is created; user can still Unlink & Reset back to Matched
- **Completed**: set automatically when the linked PI/PR/JE is submitted (via doc_events hook)
- **on_cancel**: if the submitted document is cancelled, OCR Import reverts to Matched (link cleared, ready for re-creation)

### OCR Delivery Note Workflow
Pending → Needs Review → Matched → Draft Created → Completed / No Action / Error

- **Separate DocType** from OCR Import — different data shape (no financials), different outputs (PO/PR not PI/JE)
- **No financial fields** — no rate, amount, subtotal, tax, currency on DN or DN items
- **Single DN per scan** — unlike multi-invoice PDFs, each scan produces one OCR Delivery Note
- **Drive-only input** — factory staff drop scans in a shared Drive folder; no manual upload or email ingestion
- **OCR Manager handles review** — no new role needed; factory staff never touch ERPNext
- **Rate resolution** for PR creation: PO item rate → `last_purchase_rate` → `standard_rate` → 0
- **Create PO** option for informal procurement (goods arrived without prior PO) — draft PO with rate=0
- **PO matching is qty-focused** — shows DN qty vs PO remaining qty (qty - received_qty), no rate column
- **Scan attachment copied** to created PO/PR for reference
- **doc_events**: PO/PR submit → Completed; PO/PR cancel → reset to Matched (same pattern as OCR Import)

### OCR Fleet Slip Workflow
Pending → Needs Review → Matched → Draft Created → Completed / No Action / Error

- **Single transaction per slip** — no child table; fuel fill-up or toll charge lives directly on the main doc
- **Always creates Purchase Invoice** — no Journal Entry path; both fleet card and direct expense vehicles create PIs
- **Slip classification**: `slip_type` (Fuel / Toll / Other) determines item and form sections
- **Unauthorized flag**: `slip_type = Other` auto-sets `unauthorized_flag` — orange warning on form, review and mark No Action
- **Drive-only input** — drivers drop scans in a shared Drive folder; no manual upload or email
- **Per-vehicle posting mode** via Fleet Vehicle custom fields (`custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`):
  - Fleet card provider set → **Fleet Card** mode → PI supplier = fleet card provider, expense = control account
  - Fleet card provider blank → **Direct Expense** mode → PI supplier = `fleet_default_supplier` from OCR Settings, expense = `fleet_expense_account`
- **Supplier resolution**: fleet card provider (from vehicle) → `fleet_default_supplier` (from OCR Settings) → user fills manually
- **Status readiness**: Matched requires data + `fleet_vehicle` link (not just registration string) + supplier (`fleet_card_supplier` must be set)
- **PI creation guard**: `create_purchase_invoice()` requires `fleet_vehicle` to be set (prevents unverified vehicle traceability)
- **Merchant ≠ Supplier**: merchant name (Shell, Engen) is informational; PI supplier comes from vehicle config or default
- **Item from slip_type**: Fuel → `fleet_fuel_item`, Toll → `fleet_toll_item` from OCR Settings (no item matching needed)
- **Soft dependency on fleet_management**: vehicle matching only works if Fleet Vehicle DocType exists; graceful degradation otherwise
- **Custom fields on Fleet Vehicle**: installed via fixtures (`custom_field.json`) — `custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`
- **doc_events**: PI submit → Completed; PI cancel → reset to Matched

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
- `document_type` field is hidden from the form — users interact only via the Create menu
- **No auto-creation** — user clicks a Create menu option after reviewing all matches
- After creation, status becomes **Draft Created** (not Completed) — user can Unlink & Reset if needed
- **Unlink & Reset**: deletes the draft document and resets OCR Import to Matched for re-creation
  - Clears link via `db_set()` BEFORE calling `frappe.delete_doc()` (Frappe blocks deletion of documents with incoming Link references)
  - Works on drafts (docstatus=0) and cancelled documents (docstatus=2); blocks on submitted (docstatus=1)
- **doc_events hooks** (hooks.py): `on_submit` → marks OCR Import as Completed; `on_cancel` → clears link + resets to Matched
- `flags.ignore_mandatory = True` on all created documents (drafts may have incomplete data)
- Set `bill_date` from OCR invoice_date; only set `due_date` if >= posting_date
- `default_item` in OCR Settings: used for unmatched PI items (non-stock item, OCR description set as item description)
- **Tax template**: `_build_taxes_from_template()` shared helper handles template validation, company check, tax-inclusive detection, and taxes list building for both PI and PR creation

### Purchase Order / Purchase Receipt Linking
- Optional: user can link OCR Import to an existing PO via "Find Open POs" button
- When PO selected, "Match PO Items" auto-matches OCR items to PO items by item_code (user reviews before applying)
- If PO has existing PRs, system surfaces them for selection — PR field constrained to PRs against the selected PO only
- PI items get both PO refs (`purchase_order` + `po_detail`) and PR refs (`purchase_receipt` + `pr_detail`) — closes full PO→PR→PI chain
- PR items get PO refs (`purchase_order` + `purchase_order_item`) — different field names from PI (ERPNext v15 schema)
- **PO item auto-match fallback**: if user selects PO but skips "Match PO Items" dialog, PI/PR/DN creation auto-matches by `item_code` (FIFO) — prevents orphaned PO linkage
- Stale field clearing: changing supplier clears PO/PR; changing PO clears PR and all item-level refs

### Journal Entry Creation
- For expense receipts (restaurant bills, toll slips, entertainment) that don't need PI/PR
- Requires: expense_account on items + credit_account (from field or OCR Settings default)
- Builds balanced JE: debit lines per item → expense accounts, credit line → bank/payable
- Tax handled as separate debit line to VAT input account (if tax detected)
- Account validation: all accounts must belong to company, not be group or disabled

### Server-Side Guards
- **Status guards**: PI/JE require Matched or Needs Review; PR requires Matched only; Draft Created blocks all creation (matches UI gating, prevents API bypass)
- **Document type enforcement**: each create method validates `document_type` matches (prevents API bypass)
- **Cross-document lock**: row-lock checks all three output fields (PI, PR, JE) — only one document per OCR Import
- **PO/PR linkage validation**: at create time, re-verifies PR belongs to selected PO (server-side, not just UI)
- **Row-level permissions**: `match_po_items` and `match_pr_items` check per-document read permission (not just doctype-level)
- **XSS prevention**: all dynamic values in PO/PR/match dialogs escaped via `frappe.utils.escape_html()` and `encodeURIComponent()`
- **Tax ambiguity threshold**: `_detect_tax_inclusive_rates()` returns False (default exclusive) when inclusive vs exclusive difference is < 5% of tax amount
- **Account validation (JE)**: credit/expense/tax accounts checked for company, is_group=0, disabled=0
- **Drive retry cap**: `MAX_DRIVE_RETRIES=3` prevents infinite Gemini calls on permanently bad Drive files

### Upload Security
- Permission check: User must have "create" permission on OCR Import
- File validation: PDF, JPEG, PNG only; max 10MB; magic bytes verified
- Whitelisted endpoint: `@frappe.whitelist(methods=["POST"])`

### Background Processing
- Upload creates placeholder OCR Import immediately, returns record name
- Uploaded file saved as private Frappe File attachment (enables retry on failure)
- Processing runs on `long` queue with dynamic timeout (base 300s + stagger delay)
- **Rate-limit stagger**: all ingestion paths (manual upload, email, Drive scan) pass `queue_position` to `gemini_process()`, which sleeps `position * 5s` (capped at 240s) before hitting Gemini API
- Real-time progress updates via `frappe.publish_realtime()`
- `frappe.db.commit()` required in enqueued jobs (with `# nosemgrep` comment)
- Failures logged to Error Log, status set to "Error"
- **Retry on error**: "Retry Extraction" button on all Error records — reads from Drive file or local attachment
- **Retry clears stale links**: retry endpoints reset supplier/vehicle/item links and child tables before re-extraction (prevents stale data from previous failed runs persisting)
- **Email attachments saved**: email monitor saves PDF/image as Frappe File attachment on the OCR Import, enabling retry even after the email is deleted

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
- [x] Test suite (251 tests — unit tests + integration workflow tests)
- [x] Image support: JPEG and PNG accepted alongside PDF (upload, email, Drive scan)

### Phase 5 (v0.5) — OCR Delivery Note [COMPLETE]
- [x] OCR Delivery Note + OCR Delivery Note Item DocTypes (no financial fields)
- [x] Gemini DN extraction (delivery-note-specific prompt, schema, transform)
- [x] Drive integration: separate `dn_scan_folder_id` / `dn_archive_folder_id` in OCR Settings
- [x] DN processing pipeline: `dn_gemini_process()`, `_populate_ocr_dn()`, `_run_dn_matching()`
- [x] Create Purchase Order from DN (draft, rates filled by accounts team)
- [x] Create Purchase Receipt from DN (rates from linked PO or item master)
- [x] PO matching (qty-focused: DN qty vs PO remaining qty)
- [x] No Action workflow for non-DN scans
- [x] Unlink & Reset for draft PO/PR
- [x] doc_events hooks for PO/PR submit/cancel
- [x] Client script: Create dropdown (PO/PR), Find Open POs, Match PO Items, No Action
- [x] Scan attachment copied to created PO/PR
- [x] Test suite (87 new tests — unit + integration + workflow)

### Phase 6 (v0.6) — OCR Fleet Slip [COMPLETE]
- [x] OCR Fleet Slip DocType (single transaction — no child table)
- [x] Gemini fleet slip extraction (fuel/toll/other classification, vehicle registration)
- [x] Drive integration: `fleet_scan_folder_id` in OCR Settings, reuses existing archive folder
- [x] Fleet processing pipeline: `fleet_gemini_process()`, `_populate_ocr_fleet()`, `_run_fleet_matching()`
- [x] Vehicle matching: registration → Fleet Vehicle → auto-set posting mode + accounts
- [x] Per-vehicle posting mode: Fleet Card (supplier from vehicle) vs Direct Expense (default supplier from settings)
- [x] Custom fields on Fleet Vehicle via fixtures (`custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`)
- [x] Create Purchase Invoice (always — supplier from fleet card provider or default supplier)
- [x] Unauthorized purchase flagging (slip_type = Other → orange warning)
- [x] No Action workflow, Unlink & Reset, Retry Extraction
- [x] doc_events hooks for PI submit/cancel
- [x] Client script: Create PI button, vehicle config, status intro, unauthorized warning
- [x] OCR Settings: fleet_scan_folder_id, fleet_fuel_item, fleet_toll_item, fleet_default_supplier, fleet_expense_account
- [x] Workspace: OCR Fleet Slip shortcut + link
- [x] Test suite (124 new tests — unit + integration + workflow; 481 total)

### Phase 7 (v0.7) — Auto-Draft + Stats Dashboard [COMPLETE]
- [x] `tasks/auto_draft.py` — confidence check (alias/exact matches only), doc type detection, PO auto-link, orchestration
- [x] `enable_auto_draft` checkbox in OCR Settings (opt-in, defaults off)
- [x] `auto_drafted` + `auto_draft_skipped_reason` fields on OCR Import for audit trail
- [x] Hooked into `gemini_process()` after matching — low-confidence records fall through to "Needs Review" unchanged
- [x] `stats_api.py` — whitelisted aggregation endpoint gated to owner/finance roles
- [x] `erpnext_ocr/page/ocr_stats/` — Frappe page with counts, auto-draft ratio, fallback reasons, per-supplier throughput

### Phase 8 (v0.8) — Statement Reconciliation [COMPLETE]
- [x] `tasks/classify_document.py` — Gemini-based classifier routes each Drive scan to invoice or statement pipeline (defaults to invoice on error)
- [x] `extract_statement_data()` in `gemini_extract.py` with statement-specific prompt and schema
- [x] `statement_api.py` — `statement_gemini_process()` background job (extraction + matching + reconciliation)
- [x] `tasks/reconcile.py` — matches statement lines to Purchase Invoices by supplier + `bill_no`, using `normalize_for_matching()` for reference variations (INV/00123 vs INV-00123)
- [x] OCR Statement + OCR Statement Item DocTypes (period, opening/closing balance, transaction lines with reconciliation status)
- [x] Reverse check — flags ERPNext PIs in the statement period that aren't on the statement (gated on both `period_from` and `period_to` being present)
- [x] `classification_result` + `classification_confidence` fields on both OCR Import and OCR Statement for audit
- [x] `MAX_DRIVE_RETRIES=3` retry cap applied to statements
- [x] Color-coded reconciliation view — user handles only the mismatches/missing rows

### Phase 9 (v0.9) — Reliability & Role Polish [COMPLETE]
- [x] Email move: standard IMAP `COPY` + `STORE \Deleted` is now the primary path; X-GM-LABELS kept as a fallback for label-only Gmail setups (more reliable across Gmail Workspace)
- [x] Statement auto-refresh: Purchase Invoice `on_submit`/`on_cancel` re-runs reconciliation on any OCR Statement in status "Reconciled" for that supplier (Reviewed statements untouched); failures never block PI submit
- [x] Stats role widened: `System Manager` + `Accounts Manager` (owner/finance) — OCR Manager (operations) stays off the dashboard
- [x] `\Seen` removal guard: any email fetched in Phase 1 but not successfully moved gets `STORE -FLAGS \Seen` in Phase 2, so misbehaving IMAP proxies can't strand failed emails out of the UNSEEN search

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
- **DN Scan Folder ID**: Google Drive folder ID for delivery note scans (separate from invoice scan folder)
- **DN Archive Folder ID**: Google Drive folder ID for archived DN scans
- **DN Default Warehouse**: Default warehouse for DN-created Purchase Receipts
- **Fleet Scan Folder ID**: Google Drive folder ID polled for fleet slip scans
- **Fleet Fuel Item**: Default non-stock item for fuel slips
- **Fleet Toll Item**: Default non-stock item for toll slips
- **Fleet Default Supplier**: Default supplier for fleet slip PIs when vehicle has no fleet card provider
- **Fleet Expense Account**: Default expense account for non-fleet-card vehicle PIs

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
9. Document draft created for final review in ERPNext (OCR Import status → "Draft Created")
10. Submit the draft in ERPNext → OCR Import status automatically moves to "Completed"
11. If you need to change document type: click **Unlink & Reset** (Actions menu) to delete the draft and try again
12. If extraction fails: click **Retry Extraction** button to re-process

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

**Delivery Note Scan** (via OCR Delivery Note):
1. Factory staff photograph/scan delivery note → drop in DN Drive scan folder
2. Every 15 minutes, new files are automatically downloaded and processed (single DN per scan)
3. Accounts team reviews OCR Delivery Note — checks supplier, items, quantities
4. **(If PO exists)** Click "Find Open POs" → select PO → "Match PO Items" (qty comparison) → Create Purchase Receipt
5. **(If no PO)** Click **Create → Purchase Order** (draft with rate=0) → fill in rates → submit PO → return and Create PR
6. **(Not a DN)** Click **Actions → No Action Required** with reason → status set to No Action
7. Submit created PO/PR in ERPNext → OCR DN status automatically moves to Completed

**Fleet Slip Scan** (via OCR Fleet Slip):
1. Driver scans fuel/toll slip → drops in Fleet Drive scan folder
2. Every 15 minutes, new files are automatically downloaded and processed (single slip per scan)
3. Gemini classifies as Fuel, Toll, or Other and extracts vehicle registration
4. Vehicle matched → supplier auto-set (fleet card provider or default supplier from OCR Settings)
5. Accounts team reviews OCR Fleet Slip — verifies vehicle, amounts, supplier
6. Click **Create → Purchase Invoice** (supplier from vehicle config or default)
7. **(Unauthorized)** slip_type=Other → orange warning → **Actions → No Action Required** with reason
8. Submit created PI in ERPNext → OCR Fleet Slip status automatically moves to Completed
