import json
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Frappe mock — installed into sys.modules at conftest import time.
#
# This MUST happen before any erpocr_integration module is imported, because
# they all have `import frappe` at the top level. Pytest fixtures run too
# late (after collection), so we do it here at module scope.
# ---------------------------------------------------------------------------


def _build_frappe_mock():
	"""Return a MagicMock that satisfies common frappe usage patterns."""
	mock = MagicMock()
	# frappe._() returns its argument (translation passthrough)
	mock._ = MagicMock(side_effect=lambda x: x)
	# frappe.db helpers
	mock.db = MagicMock()
	mock.db.get_value = MagicMock(return_value=None)
	mock.db.exists = MagicMock(return_value=False)
	mock.db.commit = MagicMock()
	mock.db.set_value = MagicMock()
	# frappe.get_all returns empty list by default
	mock.get_all = MagicMock(return_value=[])
	mock.get_single = MagicMock()
	mock.get_cached_doc = MagicMock()
	mock.get_doc = MagicMock()
	# frappe.throw raises an exception (like production)
	mock.throw = MagicMock(side_effect=Exception)
	# frappe.log_error returns a mock with .name
	error_log = MagicMock()
	error_log.name = "ERR-00001"
	mock.log_error = MagicMock(return_value=error_log)
	mock.logger = MagicMock(return_value=MagicMock())
	mock.get_traceback = MagicMock(return_value="<traceback>")
	# frappe.defaults
	mock.defaults = MagicMock()
	mock.defaults.get_user_default = MagicMock(return_value="Test Company")
	# frappe.whitelist — decorator that returns the function unchanged
	mock.whitelist = MagicMock(side_effect=lambda *a, **kw: (lambda fn: fn) if not a else a[0])
	# frappe.has_permission
	mock.has_permission = MagicMock(return_value=True)
	# frappe.session
	mock.session = MagicMock()
	mock.session.user = "Administrator"
	return mock


# Install frappe mock into sys.modules BEFORE any test module imports
_frappe_mock = _build_frappe_mock()
sys.modules["frappe"] = _frappe_mock


# Mock frappe.model.document so OCRImport can inherit from Document
class _MockDocument:
	"""Minimal Document base class for test imports."""

	def save(self):
		pass

	def get(self, key, default=None):
		return getattr(self, key, default)


_frappe_model_document_mock = MagicMock()
_frappe_model_document_mock.Document = _MockDocument
sys.modules["frappe.model"] = MagicMock()
sys.modules["frappe.model.document"] = _frappe_model_document_mock


# Mock frappe.utils with a working flt function (needed for JE amount math)
def _mock_flt(value, precision=None):
	if value is None:
		return 0.0
	try:
		v = float(value)
	except (ValueError, TypeError):
		return 0.0
	if precision is not None:
		return round(v, int(precision))
	return v


_frappe_utils_mock = MagicMock()
_frappe_utils_mock.flt = _mock_flt
_frappe_utils_mock.today = MagicMock(return_value="2025-01-15")
_frappe_utils_mock.escape_html = MagicMock(side_effect=lambda x: x)
_frappe_utils_mock.get_link_to_form = MagicMock(side_effect=lambda dt, name: f"{dt}/{name}")
sys.modules["frappe.utils"] = _frappe_utils_mock

# Mock Google libraries so drive_integration can be imported without them installed
for _mod_name in [
	"google",
	"google.oauth2",
	"google.oauth2.service_account",
	"googleapiclient",
	"googleapiclient.discovery",
	"googleapiclient.errors",
	"googleapiclient.http",
]:
	if _mod_name not in sys.modules:
		sys.modules[_mod_name] = MagicMock()


@pytest.fixture(autouse=True)
def reset_frappe_mock():
	"""Reset frappe mock state between tests so tests don't leak into each other."""
	_frappe_mock.db.get_value.reset_mock()
	_frappe_mock.db.get_value.return_value = None
	_frappe_mock.db.get_value.side_effect = None
	_frappe_mock.db.set_value.reset_mock()
	_frappe_mock.db.exists.reset_mock()
	_frappe_mock.db.exists.return_value = False
	_frappe_mock.db.exists.side_effect = None
	_frappe_mock.db.commit.reset_mock()
	_frappe_mock.db.sql.reset_mock()
	_frappe_mock.db.sql.return_value = []
	_frappe_mock.db.sql.side_effect = None
	_frappe_mock.get_all.reset_mock()
	_frappe_mock.get_all.return_value = []
	_frappe_mock.get_all.side_effect = None
	_frappe_mock.get_list.reset_mock()
	_frappe_mock.get_list.return_value = []
	_frappe_mock.get_list.side_effect = None
	_frappe_mock.get_doc.reset_mock()
	_frappe_mock.get_doc.side_effect = None
	_frappe_mock.get_cached_doc.reset_mock()
	_frappe_mock.get_cached_doc.side_effect = None
	_frappe_mock.log_error.reset_mock()
	_frappe_mock.enqueue.reset_mock()
	_frappe_mock.delete_doc.reset_mock()
	_frappe_mock.msgprint = MagicMock()
	_frappe_mock.throw = MagicMock(side_effect=Exception)
	_frappe_mock.has_permission = MagicMock(return_value=True)
	_frappe_mock.session.user = "Administrator"
	yield _frappe_mock


@pytest.fixture
def mock_frappe():
	"""Explicit access to the frappe mock for tests that need to configure it."""
	return _frappe_mock


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_gemini_api_response():
	"""A realistic raw Gemini API response (single invoice)."""
	invoice_json = {
		"invoices": [
			{
				"supplier_name": "Star Pops ( Pty ) Ltd",
				"supplier_tax_id": "4123456789",
				"invoice_number": "INV-2024-0042",
				"invoice_date": "2024-06-15",
				"due_date": "2024-07-15",
				"subtotal": 1000.00,
				"tax_amount": 150.00,
				"total_amount": 1150.00,
				"currency": "ZAR",
				"confidence": 0.95,
				"line_items": [
					{
						"description": "Premium Lollipops Assorted 50pk",
						"product_code": "POP-050",
						"quantity": 10,
						"unit_price": 85.00,
						"amount": 850.00,
					},
					{
						"description": "Delivery Fee",
						"product_code": "",
						"quantity": 1,
						"unit_price": 150.00,
						"amount": 150.00,
					},
				],
			}
		]
	}
	return {"candidates": [{"content": {"parts": [{"text": json.dumps(invoice_json)}]}}]}


@pytest.fixture
def sample_multi_invoice_response():
	"""Gemini API response with 2 invoices (multi-invoice PDF)."""
	invoice_json = {
		"invoices": [
			{
				"supplier_name": "Supplier A",
				"supplier_tax_id": "",
				"invoice_number": "A-001",
				"invoice_date": "2024-01-10",
				"due_date": "",
				"subtotal": 500.00,
				"tax_amount": 0,
				"total_amount": 500.00,
				"currency": "USD",
				"confidence": 0.9,
				"line_items": [
					{
						"description": "Widget A",
						"product_code": "WA-01",
						"quantity": 5,
						"unit_price": 100.00,
						"amount": 500.00,
					}
				],
			},
			{
				"supplier_name": "Supplier B",
				"supplier_tax_id": "9876543210",
				"invoice_number": "B-002",
				"invoice_date": "2024-01-11",
				"due_date": "2024-02-11",
				"subtotal": 200.00,
				"tax_amount": 30.00,
				"total_amount": 230.00,
				"currency": "EUR",
				"confidence": 0.85,
				"line_items": [
					{
						"description": "Service Fee",
						"product_code": "",
						"quantity": 1,
						"unit_price": 200.00,
						"amount": 200.00,
					}
				],
			},
		]
	}
	return {"candidates": [{"content": {"parts": [{"text": json.dumps(invoice_json)}]}}]}


@pytest.fixture
def sample_extracted_data():
	"""Transformed data in OCR Import format (output of _transform_to_ocr_import_format)."""
	return {
		"header_fields": {
			"supplier_name": "Star Pops (Pty) Ltd",
			"supplier_tax_id": "4123456789",
			"invoice_number": "INV-2024-0042",
			"invoice_date": "2024-06-15",
			"due_date": "2024-07-15",
			"subtotal": 1000.00,
			"tax_amount": 150.00,
			"total_amount": 1150.00,
			"currency": "ZAR",
			"confidence": 0.95,
		},
		"line_items": [
			{
				"description": "Premium Lollipops Assorted 50pk",
				"product_code": "POP-050",
				"quantity": 10,
				"unit_price": 85.00,
				"amount": 850.00,
			},
			{
				"description": "Delivery Fee",
				"product_code": "",
				"quantity": 1,
				"unit_price": 150.00,
				"amount": 150.00,
			},
		],
		"source_filename": "invoice.pdf",
		"raw_response": "{}",
		"extraction_time": 5.2,
	}


@pytest.fixture
def sample_email_with_pdf():
	"""Construct a MIME email message with a PDF attachment."""
	msg = MIMEMultipart()
	msg["Subject"] = "Invoice from Star Pops"
	msg["From"] = "billing@starpops.co.za"
	msg["To"] = "invoices@example.com"
	msg["Message-ID"] = "<test-001@starpops.co.za>"

	# Text body
	msg.attach(MIMEText("Please find invoice attached.", "plain"))

	# PDF attachment (minimal valid PDF header)
	pdf_bytes = b"%PDF-1.4 fake-pdf-content-for-testing"
	pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
	pdf_part.add_header("Content-Disposition", "attachment", filename="INV-2024-0042.pdf")
	msg.attach(pdf_part)

	return msg


@pytest.fixture
def sample_email_no_pdf():
	"""Email message with no PDF attachments."""
	msg = MIMEMultipart()
	msg["Subject"] = "Meeting notes"
	msg["From"] = "colleague@example.com"
	msg["To"] = "invoices@example.com"
	msg["Message-ID"] = "<test-002@example.com>"
	msg.attach(MIMEText("No invoice here.", "plain"))
	return msg


class _MockSettings(SimpleNamespace):
	"""Settings mock that supports both attribute access and .get()."""

	def get(self, key, default=None):
		return getattr(self, key, default)


@pytest.fixture
def sample_settings():
	"""Mock OCR Settings object."""
	return _MockSettings(
		default_company="Test Company",
		default_warehouse="Stores - TC",
		default_expense_account="5000 - Cost of Goods Sold - TC",
		default_cost_center="Main - TC",
		default_tax_template="SA VAT 15%",
		non_vat_tax_template="Non-VAT",
		default_item=None,
		default_credit_account="2100 - Accounts Payable - TC",
		matching_threshold=80,
		gemini_api_key="fake-key",
		gemini_model="gemini-2.5-flash",
		email_monitoring_enabled=False,
		drive_integration_enabled=False,
	)
