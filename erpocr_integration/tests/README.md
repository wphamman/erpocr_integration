# ERPNext OCR Integration Tests

## Test Structure

This directory contains unit and integration tests for the ERPNext OCR Integration app.

## Running Tests

### Prerequisites

1. Set environment variables for API keys (never hardcode them in test files):
   ```bash
   export GEMINI_API_KEY="your-api-key-here"
   ```

2. Install test dependencies:
   ```bash
   pip install pytest pytest-cov
   ```

### Running All Tests

```bash
# From the bench directory
bench --site your-site run-tests --app erpocr_integration

# Or using pytest directly
pytest erpocr_integration/tests/
```

### Running Specific Tests

```bash
pytest erpocr_integration/tests/test_matching.py
pytest erpocr_integration/tests/test_gemini_extract.py -v
```

## Writing Tests

### Unit Tests

- Place in `tests/unit/`
- Test individual functions in isolation
- Mock external dependencies (Frappe DB, API calls)

### Integration Tests

- Place in `tests/integration/`
- Test full workflows (OCR Import creation, PI generation)
- Require a running Frappe site

### Example Test

```python
import os
from unittest.mock import patch
import frappe
from frappe.tests.utils import FrappeTestCase

class TestGeminiExtract(FrappeTestCase):
    def setUp(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            self.skipTest("GEMINI_API_KEY not set")

    def test_extract_invoice_data(self):
        # Your test here
        pass
```

## Security

**NEVER commit API keys or credentials to version control!**

- Root-level `test_*.py` files are ignored by .gitignore (for local testing only)
- Use environment variables for sensitive data
- Use Frappe's `get_password()` for production credentials stored in the database
