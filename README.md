# ERPNext OCR Integration

Nanonets OCR integration for ERPNext — automatic invoice data extraction and import.

## Features

- Webhook endpoint receives OCR-extracted data from Nanonets
- Automatic supplier and item matching with learning alias system
- Purchase Invoice draft creation from matched data
- Review workflow for unmatched imports

## Installation

```bash
bench get-app https://github.com/wphamman/erpocr_integration
bench --site your-site install-app erpocr_integration
```

## License

GNU GPLv3
