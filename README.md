# ERPNext OCR Integration

Gemini 2.5 Flash OCR integration for ERPNext — automatic invoice data extraction and import.

## Features

- PDF upload via form or email forwarding
- Gemini 2.5 Flash API extracts structured invoice data (supplier, items, amounts, dates, currency)
- Automatic supplier and item matching with learning alias system
- Purchase Invoice draft creation from matched data
- Google Drive archiving with organized folder structure (Year/Month/Supplier)
- Review workflow for unmatched imports

## Installation

```bash
bench get-app https://github.com/wphamman/erpocr_integration
bench --site your-site install-app erpocr_integration
bench --site your-site migrate
```

## Configuration

1. Navigate to **Setup > OCR Settings**
2. Enter your **Gemini API Key** (get from https://aistudio.google.com/apikey)
3. Set **Default Company**, **Warehouse**, **Expense Account**, and **Cost Center**
4. Optional: Enable **Email Monitoring** and select an Email Account
5. Optional: Enable **Google Drive Integration** with a service account

## License

GNU GPLv3
