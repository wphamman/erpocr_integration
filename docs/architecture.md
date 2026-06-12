# Architecture — ERPNext OCR Integration

> Knowledge file imported by [CLAUDE.md](../CLAUDE.md). The system map: pipelines, component
> layout, DocType catalog, status workflows, configuration, deployment. Coding invariants are
> in [implementation-patterns.md](implementation-patterns.md). The cross-app API + integration
> contract is in [CROSS_APP_SURFACE.md](../CROSS_APP_SURFACE.md). Version history is in
> [CHANGELOG.md](../CHANGELOG.md). End-user usage lives in the `OCR_*_Guide.md` files.

## Project Overview

Frappe custom app that integrates Gemini 2.5 Flash API with ERPNext for automatic invoice
data extraction and import. Supports PDF, JPEG, and PNG files.

**Repository goal**: A `bench get-app` installable Frappe app that works on both self-hosted
and Frappe Cloud ERPNext instances.

**Cost**: ~$0.0001 per invoice (essentially free for small volume) vs Nanonets $0.30-0.50
per invoice.

## Architecture

### Frappe Custom App (not standalone middleware)
- Built as a Frappe custom app, NOT a separate FastAPI/Flask service
- Single install via `bench get-app`, config stored in DocTypes (UI-configurable)
- Only external dependency: Gemini API (free tier: 10 RPM / 500 RPD; Tier 1 pay-as-you-go: 1,000 RPM)

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

Fleet Pipeline: Fleet slip → Create OCR Fleet Slip → Vehicle Match → Review
                ├─ INGEST (a): Drop scan in Drive folder → 15-min poll (back-office/bulk)
                ├─ INGEST (b): driver-shell phone upload → upload_fleet_slip (P4; idempotent, recon-only, fail-safe)
                ├─ Fleet card supplier (Wesbank etc.) → captured for reconciliation against monthly fleet card invoice (Matched is the terminal state — no PI from this app)
                └─ Unauthorized / non-card purchase → optional Create Purchase Invoice (rare; supplier = default from OCR Settings)

Statement Pipeline: Drive scan → classifier routes to statement → Gemini API → Create OCR Statement → reconcile lines vs submitted Purchase Invoices → Review mismatches
```

### Design Philosophy
- **Reduce data entry**: system extracts data and suggests matches
- **Shift focus to review**: user reviews every suggestion before committing
- **No auto-creation**: documents are only created by explicit user action (the opt-in
  `enable_auto_draft` setting, off by default, can auto-draft high-confidence matches — see
  [implementation-patterns.md](implementation-patterns.md))
- **Full override**: user can change any suggestion (supplier, items, document type, PO link)

### Key Components
| File | Purpose |
|---|---|
| `erpocr_integration/api.py` | Upload endpoint + `gemini_process()` background job (multi-invoice aware) |
| `erpocr_integration/tasks/gemini_extract.py` | Gemini API — extracts `invoices[]` array from PDF/image (supports multi-invoice PDFs) |
| `erpocr_integration/tasks/process_import.py` | Universal processing pipeline — match supplier/items, create PI |
| `erpocr_integration/tasks/matching.py` | Supplier + item matching (Item Supplier → alias → exact → service mapping → fuzzy → default_item) |
| `erpocr_integration/tasks/learn_item_supplier.py` | Background job — appends (supplier, product_code) → item_code rows to ERPNext's `Item Supplier` child table on user confirm (v1.1.0+) |
| `erpocr_integration/tasks/classify_document.py` | Gemini-based classifier — routes each Drive scan to invoice or statement pipeline (defaults to invoice on error) |
| `erpocr_integration/tasks/reconcile.py` | Statement reconciliation — matches statement lines to Purchase Invoices by supplier + `bill_no` |
| `erpocr_integration/tasks/email_monitor.py` | Email inbox polling — extracts PDF/image attachments from forwarded emails |
| `erpocr_integration/tasks/drive_integration.py` | Google Drive — upload, download, folder scan (PDF + images), move-to-archive |
| `erpocr_integration/public/js/ocr_import.js` | Upload button UI with real-time progress updates |
| `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` | OCR Import class — create_purchase_invoice(), create_purchase_receipt(), alias saving, status workflow |
| `erpocr_integration/dn_api.py` | DN background processing (`dn_gemini_process`), PO matching, doc_event hooks, retry |
| `erpocr_integration/erpnext_ocr/doctype/ocr_delivery_note/ocr_delivery_note.py` | OCR DN controller — create_purchase_order(), create_purchase_receipt(), unlink, no_action |
| `erpocr_integration/public/js/ocr_delivery_note.js` | DN client: PO matching dialogs (qty-focused), Create dropdown (PO/PR), real-time status |
| `erpocr_integration/fleet_api.py` | Fleet background processing (`fleet_gemini_process`), vehicle matching (exact + normalized + fuzzy `_fuzzy_match_vehicle`), doc_event hooks, retry, `route_to_invoice_pipeline` (re-route mis-folder slips to invoice pipeline), `upload_fleet_slip` (driver-shell idempotent phone-capture upload contract, P4 — see [CROSS_APP_SURFACE.md §2c](../CROSS_APP_SURFACE.md)) |
| `erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py` | OCR Fleet Slip controller — create_purchase_invoice(), mark_recorded(), unlink, no_action, status workflow |
| `erpocr_integration/public/js/ocr_fleet_slip.js` | Fleet client: Create PI button, vehicle config display, status intro, unauthorized warning |
| `erpocr_integration/statement_api.py` | Statement background processing (`statement_gemini_process`), reconciliation orchestration, `rereconcile_statement` |
| `erpocr_integration/stats_api.py` | Role-gated aggregation endpoint (`get_ocr_stats`) backing the OCR Stats page |

### DocTypes
| DocType | Type | Purpose |
|---|---|---|
| **OCR Settings** | Single | Gemini API key, email/Drive config, default company/warehouse/tax templates, default item, default credit account |
| **OCR Import** | Regular | Main staging record — extracted data, match status, document type (PI/PR/JE), links to PO, PR, created PI/PR/JE |
| **OCR Import Item** | Child Table | Line items — description, supplier `product_code` (v1.1.0+), qty, rate, matched item_code, PO item ref, PR item ref |
| **OCR Supplier Alias** | Regular | Learning: OCR text → ERPNext Supplier |
| **OCR Item Alias** | Regular | Learning: OCR text → ERPNext Item |
| **OCR Service Mapping** | Regular | Pattern → item + GL account + cost center (supplier-specific or generic) |
| **OCR Delivery Note** | Regular | DN staging record — extracted qty/item data, PO matching, creates PO or PR |
| **OCR Delivery Note Item** | Child Table | DN line items — description, qty, uom, matched item_code, PO item ref |
| **OCR Fleet Slip** | Regular | Fleet slip staging — fuel/toll/other classification, vehicle matching; primary path is capture for fleet-card reconciliation, PI creation is the exception |
| **OCR Statement** | Regular | Supplier statement staging — period, opening/closing balance, reconciliation status |
| **OCR Statement Item** | Child Table | Statement transaction lines — date, reference, debit, credit, balance, recon status, matched invoice |

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
Pending → Needs Review → Matched → Draft Created (Direct Expense) / Completed (Fleet Card) → Completed / No Action / Error

- **Two disposition paths, branched by `posting_mode`** (v1.2.0+):
  - **`posting_mode = "Fleet Card"`** → slip closes as a **control record**, no Purchase Invoice. The fleet card provider's monthly invoice (handled in `fleet_management`) books the cost; this slip captures litres / odometer / vehicle / date for cross-check. Reaches `Completed` via `mark_recorded()` (whitelisted, user-clicked **Mark Recorded** button). `purchase_invoice` stays NULL.
  - **`posting_mode = "Direct Expense"`** → slip becomes the source document for an AP entry via `create_purchase_invoice()`. Standard PI draft → submit → Completed flow.
- **`Completed` no longer implies `purchase_invoice IS NOT NULL`** (v1.2.0). A Fleet Card slip in `Completed` has the link NULL by design — the cost lives in the provider's invoice. Code that queries Fleet Slips for downstream document linkage must check both `posting_mode` and `purchase_invoice`, not just status.
- **PI guard: `posting_mode != "Direct Expense"` raises.** Server-side enforcement in `create_purchase_invoice()` (defence in depth — UI also hides the button for Fleet Card mode). Prevents the v1.2.0 invariant from being bypassed by API call or stale client.
- **`posting_mode` is operator-editable.** Auto-set from `Fleet Vehicle.custom_fleet_card_provider` on vehicle match, but no longer `read_only`. Accounting can flip per-slip during review for the edge case where a fleet-card vehicle was filled on a business card (or vice-versa).
- **Single transaction per slip** — no child table; fuel fill-up or toll charge lives directly on the main doc
- **Slip classification**: `slip_type` (Fuel / Toll / Other) determines item and form sections
- **Unauthorized flag**: `slip_type = Other` auto-sets `unauthorized_flag` — orange warning on form; review then either Mark Recorded (Fleet Card) / Create PI (Direct Expense) / Mark No Action (genuinely unauthorized)
- **Two ingest paths** (P4): (1) the Drive folder poll (back-office/bulk; `source_type = "Gemini Drive Scan"`), and (2) the **driver-shell phone upload** — `upload_fleet_slip` (`source_type = "Gemini Shell Upload"`): idempotent (R-B `client_request_id`), recon-only (never a PI), fail-safe (provider-less vehicle → Needs Review, never the invoice path), via the `OCR Fleet Driver` role. Both land the same OCR Fleet Slip, indistinguishable downstream except `source_type`. No email ingestion. See [CROSS_APP_SURFACE.md §2c](../CROSS_APP_SURFACE.md).
- **Per-vehicle posting mode** auto-set from Fleet Vehicle custom fields (`custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`):
  - Fleet card provider set → **Fleet Card** mode (operator runs Mark Recorded once verified)
  - Fleet card provider blank → **Direct Expense** mode → PI supplier = `fleet_default_supplier` from OCR Settings, expense = `fleet_expense_account`
- **Vestigial on Fleet Card path** (v1.2.0): `custom_fleet_control_account` is still captured on each slip (via `_apply_vehicle_config`) but no longer flows into a PI on Fleet Card slips since no PI is created. The expense_account field on the slip is informational only on this path; cleanup TBD in a future release.
- **`fleet_management` reads OCR Fleet Slips** (`monthly_summary.py`) for `status in [Completed, Draft Created, Matched, Needs Review]` regardless of `posting_mode` — Fleet Card slips in `Completed` continue to feed LITRES_MISMATCH and the Fuel Efficiency Tracker.
- **Supplier resolution**: fleet card provider (from vehicle) → `fleet_default_supplier` (from OCR Settings) → user fills manually
- **Status readiness**: Matched requires data + `fleet_vehicle` link (not just registration string) + supplier (`fleet_card_supplier` must be set)
- **PI creation guard**: `create_purchase_invoice()` requires `fleet_vehicle` to be set (prevents unverified vehicle traceability)
- **Merchant ≠ Supplier**: merchant name (Shell, Engen) is informational; PI supplier (if a PI is ever created) comes from vehicle config or default
- **Item from slip_type**: Fuel → `fleet_fuel_item`, Toll → `fleet_toll_item` from OCR Settings (no item matching needed)
- **Vehicle matching tiers**: exact registration → punctuation-stripped exact (Suggested) → OCR-aware fuzzy via `_fuzzy_match_vehicle` with both raw and canonicalized (`S↔5`, `L↔1`, `B↔8`, `O↔0`, `Z↔2`, `G↔6`, `I↔1`, `Q↔0`) scoring + length/plausibility-band/tight-ambiguity guards (Suggested). See `_canonicalize_plate` in [fleet_api.py](../erpocr_integration/fleet_api.py).
- **doc_events**: PI submit → Completed; PI cancel → reset to Matched

### OCR Statement Workflow
Pending → Extracting → Reconciled → Reviewed / Error

- **Drive-classified ingestion** — `classify_document.py` routes each Drive scan to the invoice or statement pipeline; statements run `statement_gemini_process()` (extract → match supplier → reconcile).
- **Reconciliation** (`tasks/reconcile.py`): each statement debit line is matched against submitted Purchase Invoices for the supplier by `bill_no` (normalized for INV/00123 vs INV-00123), within the statement period. Credit lines are flagged as payments.
- **Reverse check** — flags submitted PIs in the statement period that are NOT on the statement (gated on both `period_from` and `period_to` present). Adds `Not in Statement` rows.
- **Per-line recon status**: Matched / Amount Mismatch / Missing from ERPNext / Not in Statement / Payment / Unreconciled.
- **Review actions** (`ocr_statement.js`): **Re-Reconcile** (after correcting the matched supplier) → `rereconcile_statement`; **Mark Reviewed** → `mark_reviewed`.
- **Statement auto-refresh**: Purchase Invoice `on_submit`/`on_cancel` re-runs reconciliation (on the `short` queue) for any OCR Statement in status "Reconciled" for that supplier; Reviewed statements untouched; failures never block PI submit.

### Cross-app integration (fleet_management) — summary
OCR runs **standalone or alongside `fleet_management`** — neither app imports nor depends on the
other; integration is bidirectional via ERPNext Custom Fields, runtime feature-detected, so
install order doesn't matter. **The canonical contract (fields planted/read, who owns what, the
conditional-Custom-Field rule for `OCR Import.fleet_vehicle`) lives in
[CROSS_APP_SURFACE.md §4](../CROSS_APP_SURFACE.md).** Load-bearing rule: a `Link → <doctype owned
by an optional app>` declared in doctype JSON breaks meta resolution on sites without that app —
use the conditional Custom Field install pattern ([install.py](../erpocr_integration/install.py)
`setup_optional_custom_fields()`).

## Configuration

### Getting Gemini API Key
1. Visit https://aistudio.google.com/apikey
2. Sign in with Google account
3. Click "Create API key"
4. Copy the key (starts with `AIza...`)

**Important — Free tier rate limits:** The free tier allows only 10 requests/minute and 500
requests/day. Batch uploads via Drive scan or email can easily exceed these limits, causing
429 errors and failed extractions. To avoid this, link a billing account to your Google AI
project (Google AI Studio > Settings). This upgrades to Tier 1 (1,000 RPM, 10,000+ RPD) at
minimal cost (~$0.0001 per invoice). Check your current limits at
https://aistudio.google.com/rate-limit.

**Watch out — Prepay credit depletion looks like a rate limit.** New Tier 1 accounts default
to **Prepay** mode (credits topped up manually). When the prepay balance hits zero, Gemini
returns `HTTP 429` with status `RESOURCE_EXHAUSTED` and message *"Your prepayment credits are
depleted"* — same traceback as a rate-limit error, but the fix is funding, not throttling.
Diagnose by reading the response body from the "Gemini API Rate Limit" / "Gemini API Error"
entries in Error Log. Prevention: switch the project to **pay-as-you-go (post-pay)** in AI
Studio → Billing, or set a low-balance alert on the billing account.

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
- **Enable Auto-Draft**: Opt-in (off by default) — auto-draft high-confidence matches (see [implementation-patterns.md](implementation-patterns.md))

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

## Usage
End-user, step-by-step usage lives in the dedicated guides — not duplicated here:
- [OCR_Quick_Start_Guide.md](../OCR_Quick_Start_Guide.md) — invoice uploader
- [OCR_User_Guide.md](../OCR_User_Guide.md) — accountant (review + document creation)
- [OCR_Delivery_Note_Guide.md](../OCR_Delivery_Note_Guide.md) — factory staff (delivery notes)
- [OCR_Fleet_Slip_Guide.md](../OCR_Fleet_Slip_Guide.md) — drivers (fuel/toll slips)
- [OCR_Statement_Guide.md](../OCR_Statement_Guide.md) — accountant (statement reconciliation)
