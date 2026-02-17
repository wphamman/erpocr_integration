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
  <img src="https://img.shields.io/badge/ERPNext-v15-blue" alt="ERPNext v15">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
</p>

---

A [Frappe](https://frappeframework.com/) custom app that uses Google's **Gemini 2.5 Flash** API to extract structured invoice data from PDFs and create Purchase Invoice drafts in ERPNext. Essentially free at small volume (~$0.0001 per invoice).

## Features

- **PDF Upload** — Manual upload via the OCR Import form
- **Email Monitoring** — Forward invoice emails to a monitored inbox for automatic processing
- **Google Drive Scanning** — Drop PDFs into a Drive folder for batch processing every 15 minutes
- **Multi-Invoice PDFs** — Handles statements/batch scans with multiple invoices per PDF
- **Gemini AI Extraction** — Structured JSON output with supplier, line items, amounts, dates, and currency
- **Smart Matching** — Exact match, fuzzy match (difflib), service item mappings, and a learning alias system
- **Tax Template Mapping** — Auto-selects VAT or non-VAT template based on detected tax amounts
- **Purchase Invoice Drafts** — Automatically creates PI drafts when all items are matched
- **Google Drive Archiving** — Organizes processed PDFs into Year/Month/Supplier folders
- **Confidence Scoring** — Gemini self-reports extraction confidence (displayed as color-coded badge)
- **Dashboard** — Workspace with KPI number cards, status chart, and quick links

## How It Works

```
PDF Input (Upload / Email / Drive)
    ↓
Gemini 2.5 Flash API (structured JSON extraction)
    ↓
OCR Import Record (staging — extracted data)
    ↓
Matching Engine (supplier aliases → item aliases → fuzzy → service mappings)
    ↓
Purchase Invoice Draft (auto-created when fully matched)
```

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
| **Default Company** | Company for Purchase Invoice creation |

### Recommended
| Setting | Description |
|---------|-------------|
| **Default Warehouse** | Warehouse for PI line items |
| **Default Expense Account** | Fallback GL account for unmatched items |
| **Default Cost Center** | Cost center for PI line items |
| **VAT Tax Template** | Applied when tax is detected (e.g., SA 15% VAT) |
| **Non-VAT Tax Template** | Applied when no tax detected |
| **Matching Threshold** | Minimum fuzzy match score (0-100, default: 80) |

### Optional: Email Monitoring
1. Enable **Email Monitoring** in OCR Settings
2. Select an ERPNext **Email Account** to monitor
3. Create Gmail labels: `OCR Invoices` (inbox) and `OCR Processed` (archive)
4. Forward invoice emails to the monitored address

### Optional: Google Drive Integration
1. Create a [Google Cloud service account](https://cloud.google.com/iam/docs/service-accounts-create)
2. Share your Drive archive folder with the service account email
3. Enable **Drive Integration** in OCR Settings
4. Paste the service account JSON (stored encrypted)
5. Set the **Archive Folder ID** and optionally a **Scan Inbox Folder ID**

## Usage

### Manual Upload
1. Go to **OCR Import > New**
2. Click **Actions > Upload PDF**
3. Wait 5-30 seconds for Gemini extraction
4. Review supplier and item matches
5. PI draft is auto-created when fully matched

### Email
1. Forward invoice emails to the configured address
2. Emails are checked hourly
3. PDFs are automatically extracted and processed

### Drive Scan
1. Drop PDF files into the configured Drive scan folder
2. Files are checked every 15 minutes
3. After processing, files are moved to the archive folder

## DocTypes

| DocType | Purpose |
|---------|---------|
| **OCR Settings** | App configuration (API keys, defaults, thresholds) |
| **OCR Import** | Main staging record — extracted data, match status, PI link |
| **OCR Import Item** | Line items on OCR Import |
| **OCR Supplier Alias** | Learned mapping: OCR text &rarr; ERPNext Supplier |
| **OCR Item Alias** | Learned mapping: OCR text &rarr; ERPNext Item |
| **OCR Service Mapping** | Pattern-based mapping: description &rarr; Item + GL account |

## Status Workflow

```
Pending → Needs Review → Matched → Completed
                ↓
              Error
```

- **Pending** — PDF uploaded, waiting for extraction
- **Needs Review** — Extracted but not all items matched
- **Matched** — All suppliers and items matched; PI auto-created
- **Completed** — Purchase Invoice created
- **Error** — Extraction or processing failed (check Error Log)

## Architecture

This is a standard Frappe custom app — no external middleware or separate services.

```
erpocr_integration/
├── api.py                          # Upload endpoint + background processing
├── hooks.py                        # Scheduled jobs, fixtures
├── tasks/
│   ├── gemini_extract.py           # Gemini API integration
│   ├── matching.py                 # Supplier + item matching (exact, fuzzy, service)
│   ├── process_import.py           # OCR text cleaning + parsing utilities
│   ├── email_monitor.py            # IMAP email polling
│   └── drive_integration.py        # Google Drive upload/download/scan
├── erpnext_ocr/
│   └── doctype/                    # All DocType definitions
├── public/
│   └── js/ocr_import.js            # Upload button + real-time progress UI
└── fixtures/                       # Dashboard charts + number cards
```

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run `ruff check .` and `ruff format .`
5. Commit and push
6. Open a Pull Request

## License

[GNU General Public License v3.0](license.txt)
