<p align="center">
  <h1 align="center">ERPNext OCR Integration</h1>
  <p align="center">
    Gemini AI-powered invoice data extraction for ERPNext
  </p>
</p>

<p align="center">
  <a href="https://github.com/wphamman/erpocr_integration/actions/workflows/ci.yml">
    <img src="https://github.com/wphamman/erpocr_integration/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://github.com/wphamman/erpocr_integration/blob/master/license.txt">
    <img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License: GPLv3">
  </a>
  <img src="https://img.shields.io/badge/version-0.4.0-green" alt="Version 0.4.0">
  <img src="https://img.shields.io/badge/ERPNext-v15-blue" alt="ERPNext v15">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
</p>

---

A [Frappe](https://frappeframework.com/) custom app that uses Google's **Gemini 2.5 Flash** API to extract structured invoice data from PDFs and images, and create Purchase Invoice, Purchase Receipt, or Journal Entry drafts in ERPNext. Essentially free at small volume (~$0.0001 per invoice).

## Features

- **File Upload** — Upload PDF, JPEG, or PNG invoices via the OCR Import form
- **Email Monitoring** — Forward invoice emails to a monitored inbox for automatic processing
- **Google Drive Scanning** — Drop files into a Drive folder for batch processing every 15 minutes
- **Multi-Invoice PDFs** — Handles statements/batch scans with multiple invoices per PDF
- **Gemini AI Extraction** — Structured JSON output with supplier, line items, amounts, dates, and currency
- **Smart Matching** — Exact match, fuzzy match (difflib), service item mappings, and a learning alias system
- **Tax Template Mapping** — Auto-selects VAT or non-VAT template based on detected tax amounts
- **User-Driven Document Creation** — Review extraction, confirm matches, then choose what to create:
  - **Purchase Invoice** — with optional Purchase Order and Purchase Receipt linking
  - **Purchase Receipt** — with optional Purchase Order linking
  - **Journal Entry** — for expense receipts (restaurant bills, tolls, entertainment)
- **Purchase Order Linking** — Match OCR items to PO items, link PRs, close the full PO→PR→PI chain
- **Google Drive Archiving** — Organises processed files into Year/Month/Supplier folders
- **Confidence Scoring** — Gemini self-reports extraction confidence (displayed as colour-coded badge)
- **Dashboard** — Workspace with KPI number cards, status chart, and quick links

## How It Works

```
File Input (Upload PDF/Image, Email, or Drive)
    ↓
Gemini 2.5 Flash API (structured JSON extraction)
    ↓
OCR Import Record (staging — extracted data)
    ↓
Matching Engine (supplier aliases → item aliases → fuzzy → service mappings)
    ↓
User Review (confirm matches, select document type, optional PO linking)
    ↓
Create Document (Purchase Invoice, Purchase Receipt, or Journal Entry draft)
```

No documents are created automatically — every decision is made by the user.

## Requirements

- ERPNext v15+ (Frappe v15+)
- Python 3.10+
- Gemini API key ([free tier](https://aistudio.google.com/apikey): 15 requests/minute)
- Google Cloud service account (optional, for Drive integration)

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

## Usage

### Manual Upload
1. Go to **OCR Import > New**
2. Click **Actions > Upload File** (accepts PDF, JPEG, PNG — max 10 MB)
3. Wait 5-30 seconds for Gemini extraction
4. Review and confirm supplier and item matches
5. Optionally link to a Purchase Order (and Purchase Receipt)
6. Click the **Create** dropdown → select Purchase Invoice, Purchase Receipt, or Journal Entry
7. Draft document is created (OCR Import status → "Draft Created")
8. Review and submit the draft in ERPNext → OCR Import moves to "Completed"
9. Need to change? Click **Actions > Unlink & Reset** to delete the draft and try again

### Email
1. Forward invoice emails to the configured address
2. Emails are checked hourly — PDF and image attachments are processed
3. Review and create documents from the OCR Import list

### Drive Scan
1. Drop PDF or image files into the configured Drive scan folder
2. Files are checked every 15 minutes
3. After processing, files are moved to the archive folder (Year/Month/Supplier)

## DocTypes

| DocType | Purpose |
|---------|---------|
| **OCR Settings** | App configuration (API keys, defaults, thresholds) |
| **OCR Import** | Main staging record — extracted data, match status, PO/PR links, created document links |
| **OCR Import Item** | Line items on OCR Import (with PO item and PR item references) |
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
├── api.py                          # Upload endpoint, PO/PR matching endpoints, background processing
├── hooks.py                        # Scheduled jobs, fixtures
├── tasks/
│   ├── gemini_extract.py           # Gemini API integration (PDF + image support)
│   ├── matching.py                 # Supplier + item matching (exact, fuzzy, service)
│   ├── process_import.py           # OCR text cleaning + parsing utilities
│   ├── email_monitor.py            # IMAP email polling (PDF + image attachments)
│   └── drive_integration.py        # Google Drive upload/download/scan (PDF + images)
├── erpnext_ocr/
│   └── doctype/                    # All DocType definitions
│       └── ocr_import/ocr_import.py  # Document creation (PI, PR, JE) with guards
├── public/
│   └── js/ocr_import.js            # Upload UI, PO matching dialogs, real-time progress
├── patches/                        # Migration patches
└── fixtures/                       # Dashboard charts + number cards
```

## Documentation

- [Uploader Guide](OCR_Quick_Start_Guide.md) — For anyone sending invoices into the system
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
