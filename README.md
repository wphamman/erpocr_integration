<p align="center">
  <h1 align="center">ERPNext OCR Integration</h1>
  <p align="center">
    Gemini AI-powered document extraction for ERPNext — invoices, delivery notes, and fleet slips
  </p>
</p>

<p align="center">
  <a href="https://github.com/wphamman/erpocr_integration/actions/workflows/ci.yml">
    <img src="https://github.com/wphamman/erpocr_integration/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://github.com/wphamman/erpocr_integration/blob/master/license.txt">
    <img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License: GPLv3">
  </a>
  <img src="https://img.shields.io/badge/version-1.1.5-blue" alt="Version 1.1.5">
  <img src="https://img.shields.io/badge/ERPNext-v15-blue" alt="ERPNext v15">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
</p>

---

A [Frappe](https://frappeframework.com/) custom app that uses Google's **Gemini 2.5 Flash** API to extract structured data from PDFs and images, and create draft documents in ERPNext. Four pipelines: **invoices**, **delivery notes**, **fleet slips**, and **statement reconciliation**. Essentially free at small volume (~$0.0001 per document).

## Features

### Invoice Pipeline (OCR Import)
- **File Upload** — Upload PDF, JPEG, or PNG invoices via the OCR Import form
- **Email Monitoring** — Forward invoice emails to a monitored inbox for automatic processing
- **Google Drive Scanning** — Drop files into a Drive folder for batch processing every 15 minutes
- **Multi-Invoice PDFs** — Handles statements/batch scans with multiple invoices per PDF
- **Smart Matching** — Six-tier pipeline: ERPNext `Item Supplier` lookup (supplier-scoped, highest precision) → alias / exact name → service mapping → fuzzy match → optional `default_item` fallback. The Item Supplier table auto-populates as users confirm matches, so the system gets sharper over time without a manual mapping job.
- **User-Driven Document Creation** — Review extraction, confirm matches, then choose what to create:
  - **Purchase Invoice** — with optional Purchase Order and Purchase Receipt linking
  - **Purchase Receipt** — with optional Purchase Order linking
  - **Journal Entry** — for expense receipts (restaurant bills, entertainment)
- **Purchase Order Linking** — Match OCR items to PO items, link PRs, close the full PO→PR→PI chain

### Delivery Note Pipeline (OCR Delivery Note)
- **Drive Scanning** — Factory staff drop delivery note scans into a dedicated Drive folder
- **Qty-Focused Matching** — Compares DN quantities against PO remaining quantities (no financial fields)
- **Create PO or PR** — Link to existing PO and create Purchase Receipt, or create a draft PO for informal procurement

### Fleet Slip Pipeline (OCR Fleet Slip)
- **Drive Scanning** — Drivers drop fuel and toll slip photos into a dedicated Drive folder
- **Slip Classification** — Gemini identifies Fuel, Toll, or Other (unauthorized purchases flagged)
- **Vehicle Matching** — Registration number matched to Fleet Vehicle for auto-configured posting
- **Per-Vehicle Posting** — Fleet card vehicles use card provider as supplier; direct expense vehicles use a default supplier
- **Two Posting Modes** (v1.2.0) — **Fleet Card** slips close as control records with **no Purchase Invoice** (the provider's monthly fleet-card invoice books the cost; the slip captures litres/odometer/vehicle for cross-check, marked done via **Mark Recorded**); **Direct Expense** slips create a Purchase Invoice. The mode auto-sets from the vehicle but is operator-editable per slip.
- **Optional `fleet_management` Integration** — Works standalone or alongside the [`fleet_management`](https://github.com/wphamman/fleet_management) app. When `fleet_management` is installed, OCR-generated fleet PIs are automatically tagged with `custom_fleet_vehicle` so they appear in vehicle-level cost reports. Pure runtime feature-detect — no hard dependency, no install ordering required, app works identically without `fleet_management`.

### Statement Reconciliation Pipeline (OCR Statement)
- **Drive Scanning + Auto-Classification** — A Gemini classifier routes each Drive scan to the invoice or statement pipeline automatically
- **Supplier Statement Extraction** — Extracts transaction lines (date, reference, debit, credit, running balance) from a supplier statement
- **Automatic Reconciliation** — Matches each statement line against submitted ERPNext Purchase Invoices for that supplier (by reference and amount, within the statement period); credit lines identified as payments
- **Mismatch Flagging** — Lines flagged Matched / Amount Mismatch / Missing from ERPNext / Unreconciled; a reverse check surfaces submitted PIs that are *Not in Statement*
- **Auto-Refresh** — Submitting/cancelling a Purchase Invoice re-runs reconciliation on that supplier's Reconciled statements (out-of-band, never blocks the PI)

### Shared Features
- **Gemini AI Extraction** — Structured JSON output with confidence scoring
- **Tax Template Mapping** — Auto-selects VAT or non-VAT template based on detected tax amounts
- **Optional Auto-Draft** — Off by default; when enabled, high-confidence extractions (supplier + all items exact/alias matched) auto-create a draft document, skipping the manual review-and-click step. Low-confidence records still fall through to manual review.
- **Google Drive Archiving** — Organises processed files into Year/Month/Supplier folders
- **Confidence Scoring** — Gemini self-reports extraction confidence (displayed as colour-coded badge)
- **Stats Dashboard** — Role-gated OCR Stats page: throughput, auto-draft ratio, fallback reasons, per-supplier counts
- **Dashboard** — Workspace with KPI number cards, status chart, and quick links

## How It Works

```
Invoice:       Upload/Email/Drive → Gemini API → OCR Import → Match → Review → PI / PR / JE
Delivery Note: Drive scan → Gemini API → OCR Delivery Note → Match → Review → PO / PR
Fleet Slip:    Drive scan → Gemini API → OCR Fleet Slip → Vehicle Match → Review → PI / Mark Recorded
Statement:     Drive scan → classify → Gemini API → OCR Statement → Reconcile vs Purchase Invoices → Review
```

No documents are created automatically by default — every decision is made by the user. (An optional, off-by-default setting can auto-draft high-confidence matches.)

## Requirements

- ERPNext v15+ (Frappe v15+)
- Python 3.10+
- Gemini API key ([free tier](https://aistudio.google.com/apikey): 10 RPM / 500 RPD; Tier 1 with billing: 1,000 RPM / 10,000+ RPD)
- Google Cloud service account (optional, for Drive integration)

> ⚠️ **Billing mode matters.** New Google AI Studio accounts default to **Tier 1 · Prepay** when billing is linked — you must top up credits up front, and when they hit zero the API returns `429 RESOURCE_EXHAUSTED` ("Your prepayment credits are depleted") regardless of rate. This looks identical to a rate-limit error in the logs but the cause is funding. To avoid surprise stoppages, switch the project to **pay-as-you-go (post-pay)** in Google AI Studio → Billing, or set a calendar reminder to top up. Check active tier at https://aistudio.google.com/rate-limit.

## Installation

```bash
bench get-app https://github.com/wphamman/erpocr_integration
bench --site your-site install-app erpocr_integration
bench --site your-site migrate
bench restart
```

## Configuration

Navigate to **Setup > OCR Settings** in your ERPNext instance:

### Required
| Setting | Description |
|---------|-------------|
| **Gemini API Key** | Get from [Google AI Studio](https://aistudio.google.com/apikey) |
| **Default Company** | Company for document creation |

### Recommended
| Setting | Description |
|---------|-------------|
| **Default Warehouse** | Warehouse for PI/PR line items |
| **Default Expense Account** | Fallback GL account for unmatched items |
| **Default Cost Center** | Cost center for line items |
| **Default Credit Account** | Credit account for Journal Entries (e.g. Accounts Payable, Bank) |
| **Default Item** | Non-stock fallback item for unmatched line items |
| **VAT Tax Template** | Applied when tax is detected (e.g., SA 15% VAT) |
| **Non-VAT Tax Template** | Applied when no tax detected |
| **Matching Threshold** | Minimum fuzzy match score (0-100, default: 80) |

### Optional: Email Monitoring
1. Create or select an ERPNext **Email Account** with valid IMAP credentials (server, port, email, password)
2. **Disable "Enable Incoming"** on the Email Account — the OCR monitor makes its own direct IMAP connection and does not use Frappe's built-in email sync
3. Enable **Email Monitoring** in OCR Settings and select the Email Account
4. Forward invoice emails (with PDF or image attachments) to the monitored address

### Email Security (Required in Production)

Do **not** use an unrestricted mailbox or alias for OCR ingestion.  
Use only invoice addresses where you can enforce a sender allowlist (Google Workspace or equivalent), otherwise external senders can trigger unwanted OCR jobs and API costs.

#### Google Workspace Allowlist Template (example)
1. Create an address list in Admin Console (for example: `OCR Invoice Allowed Senders`)
2. Add approved senders (specific addresses and/or trusted domains)
3. Create a Gmail routing rule with:
   - **Scope**: inbound mail
   - **Recipient condition**: envelope recipient matches your invoice mailbox address(es)
     - Example regex: `^invoices@(yourdomain\.com|subsidiary\.com)$`
   - **Action for sender NOT in allowlist**: reject message
   - **Action for sender in allowlist**: allow normal delivery
4. Test with one approved and one unapproved sender before going live

If your current alias setup cannot enforce sender restrictions cleanly, route `invoices@...` to a dedicated Google Group and restrict posting permissions there.

### Optional: Google Drive Integration
1. Create a [Google Cloud service account](https://cloud.google.com/iam/docs/service-accounts-create)
2. Share your Drive folders with the service account email
3. Enable **Drive Integration** in OCR Settings
4. Paste the service account JSON (stored encrypted)
5. Set the **Archive Folder ID** and optionally a **Scan Inbox Folder ID**
6. Optionally set **DN Scan Folder ID** and **DN Archive Folder ID** for delivery note scanning
7. Optionally set **Fleet Scan Folder ID** for fleet slip scanning (reuses the main archive folder)

## Usage

### Invoices (OCR Import)
1. Go to **OCR Import > New** and click **Upload File**, or forward to email, or drop in Drive scan folder
2. Wait 5-30 seconds for Gemini extraction
3. Review and confirm supplier and item matches
4. Optionally link to a Purchase Order (and Purchase Receipt)
5. Click the **Create** dropdown → select Purchase Invoice, Purchase Receipt, or Journal Entry
6. Draft document is created → submit in ERPNext to complete

### Delivery Notes (OCR Delivery Note)
1. Factory staff drop delivery note scans into the DN Drive scan folder
2. System extracts supplier, items, and quantities (no financial data)
3. Accounting team links to existing PO → creates Purchase Receipt, or creates draft PO if no PO exists

### Fleet Slips (OCR Fleet Slip)
1. Drivers drop fuel/toll slip scans into the Fleet Drive scan folder
2. System classifies as Fuel/Toll/Other, matches vehicle registration
3. Accounting team reviews and clicks **Create > Purchase Invoice**

See the user guides in the Documentation section below for detailed instructions.

## DocTypes

| DocType | Purpose |
|---------|---------|
| **OCR Settings** | App configuration (API keys, defaults, thresholds) |
| **OCR Import** | Invoice staging — extracted data, match status, PO/PR links, created PI/PR/JE |
| **OCR Import Item** | Line items on OCR Import (with PO item and PR item references) |
| **OCR Delivery Note** | DN staging — extracted supplier, items, quantities; creates PO or PR |
| **OCR Delivery Note Item** | Line items on OCR DN (description, qty, UOM, item match) |
| **OCR Fleet Slip** | Fleet slip staging — fuel/toll classification, vehicle matching; Fleet Card (control record) or Direct Expense (creates PI) |
| **OCR Statement** | Supplier statement staging — period, balances, reconciliation status |
| **OCR Statement Item** | Statement transaction lines (date, reference, debit, credit, balance, reconciliation status) |
| **OCR Supplier Alias** | Learned mapping: OCR text &rarr; ERPNext Supplier |
| **OCR Item Alias** | Learned mapping: OCR text &rarr; ERPNext Item |
| **OCR Service Mapping** | Pattern-based mapping: description &rarr; Item + GL account |

## Status Workflow

```
Pending → Needs Review → Matched → Draft Created → Completed
                ↓                        ↑
              Error            (Unlink & Reset)
```

- **Pending** — File uploaded, waiting for extraction
- **Needs Review** — Extracted, but supplier or items need review
- **Matched** — All suppliers and items matched; ready for document creation
- **Draft Created** — Document draft (PI/PR/JE) created but not yet submitted
- **Completed** — Draft document has been submitted in ERPNext
- **Error** — Extraction or processing failed (check Error Log)

From **Draft Created**, you can:
- **Submit** the draft in ERPNext → OCR Import automatically moves to Completed
- **Unlink & Reset** (Actions menu) → deletes the draft and resets to Matched for re-creation
- If you **cancel** a submitted document → OCR Import resets to Matched automatically

## Architecture

This is a standard Frappe custom app — no external middleware or separate services.

```
erpocr_integration/
├── api.py                          # Invoice upload, PO/PR matching, background processing
├── dn_api.py                       # DN background processing, PO matching, doc_events
├── fleet_api.py                    # Fleet background processing, vehicle matching, doc_events
├── hooks.py                        # Scheduled jobs, doc_events, fixtures
├── tasks/
│   ├── gemini_extract.py           # Gemini API (invoices, DNs, fleet slips)
│   ├── matching.py                 # Supplier + item matching (exact, fuzzy, service)
│   ├── process_import.py           # OCR text cleaning + parsing utilities
│   ├── email_monitor.py            # IMAP email polling (PDF + image attachments)
│   └── drive_integration.py        # Google Drive scan/download/archive (all pipelines)
├── erpnext_ocr/
│   └── doctype/
│       ├── ocr_import/             # Invoice staging + PI/PR/JE creation
│       ├── ocr_delivery_note/      # DN staging + PO/PR creation
│       ├── ocr_fleet_slip/         # Fleet slip staging + PI creation
│       └── ...                     # Settings, aliases, service mappings
├── public/js/
│   ├── ocr_import.js               # Invoice UI: upload, PO matching, real-time progress
│   ├── ocr_delivery_note.js        # DN UI: PO matching (qty-focused), Create PO/PR
│   └── ocr_fleet_slip.js           # Fleet UI: Create PI, vehicle config, unauthorized warning
├── patches/                        # Migration patches
└── fixtures/                       # Dashboard, custom fields on Fleet Vehicle
```

## Documentation

- [Invoice Uploader Guide](OCR_Quick_Start_Guide.md) — For anyone sending invoices into the system
- [Delivery Note Guide](OCR_Delivery_Note_Guide.md) — For factory staff scanning delivery notes
- [Fleet Slip Guide](OCR_Fleet_Slip_Guide.md) — For drivers scanning fuel and toll slips
- [Accountant Guide](OCR_User_Guide.md) — For accounting team: setup, review, and document creation

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run `ruff check .` and `ruff format .`
5. Commit and push
6. Open a Pull Request

## License

[GNU General Public License v3.0](license.txt)
