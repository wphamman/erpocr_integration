"""Tests for Gemini-based document classification."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from erpocr_integration.tasks.classify_document import classify_document


class TestClassifyDocument:
	def test_classifies_invoice(self, mock_frappe):
		mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
		mock_settings.get_password = MagicMock(return_value="fake-api-key")
		mock_frappe.get_single.return_value = mock_settings

		with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
			mock_api.return_value = {"document_type": "invoice", "confidence": 0.95}
			doc_type, confidence = classify_document(b"fake-pdf", "invoice.pdf")

		assert doc_type == "invoice"
		assert confidence == 0.95

	def test_classifies_statement(self, mock_frappe):
		mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
		mock_settings.get_password = MagicMock(return_value="fake-api-key")
		mock_frappe.get_single.return_value = mock_settings

		with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
			mock_api.return_value = {"document_type": "statement", "confidence": 0.90}
			doc_type, confidence = classify_document(b"fake-pdf", "statement.pdf")

		assert doc_type == "statement"
		assert confidence == 0.90

	def test_defaults_to_invoice_on_unknown(self, mock_frappe):
		mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
		mock_settings.get_password = MagicMock(return_value="fake-api-key")
		mock_frappe.get_single.return_value = mock_settings

		with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
			mock_api.return_value = {"document_type": "other", "confidence": 0.5}
			doc_type, _ = classify_document(b"fake-pdf", "unknown.pdf")

		assert doc_type == "invoice"

	def test_defaults_to_invoice_on_api_error(self, mock_frappe):
		mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
		mock_settings.get_password = MagicMock(return_value="fake-api-key")
		mock_frappe.get_single.return_value = mock_settings

		with patch("erpocr_integration.tasks.classify_document._call_classification_api") as mock_api:
			mock_api.side_effect = Exception("API error")
			doc_type, confidence = classify_document(b"fake-pdf", "broken.pdf")

		assert doc_type == "invoice"
		assert confidence == 0.0

	def test_defaults_to_invoice_on_no_api_key(self, mock_frappe):
		mock_settings = SimpleNamespace(gemini_model="gemini-2.5-flash")
		mock_settings.get_password = MagicMock(return_value="")
		mock_frappe.get_single.return_value = mock_settings

		doc_type, confidence = classify_document(b"fake-pdf", "test.pdf")

		assert doc_type == "invoice"
		assert confidence == 0.0
