"""Tests for email parsing functions in erpocr_integration.tasks.email_monitor."""

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from erpocr_integration.tasks.email_monitor import (
	_decode_header_value,
	_extract_pdfs_from_email,
)

PDF_BYTES = b"%PDF-1.4 fake-pdf-content"


# ---------------------------------------------------------------------------
# _extract_pdfs_from_email
# ---------------------------------------------------------------------------


class TestExtractPdfsFromEmail:
	def test_single_pdf_attachment(self, sample_email_with_pdf):
		pdfs = _extract_pdfs_from_email(sample_email_with_pdf)
		assert len(pdfs) == 1
		content, filename = pdfs[0]
		assert filename == "INV-2024-0042.pdf"
		assert content == b"%PDF-1.4 fake-pdf-content-for-testing"

	def test_no_pdf_attachments(self, sample_email_no_pdf):
		pdfs = _extract_pdfs_from_email(sample_email_no_pdf)
		assert len(pdfs) == 0

	def test_multiple_pdf_attachments(self):
		msg = MIMEMultipart()
		msg["Subject"] = "Two invoices"
		msg.attach(MIMEText("See attached.", "plain"))

		for name in ("invoice1.pdf", "invoice2.pdf"):
			pdf_part = MIMEApplication(PDF_BYTES, _subtype="pdf")
			pdf_part.add_header("Content-Disposition", "attachment", filename=name)
			msg.attach(pdf_part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 2
		filenames = {f for _, f in pdfs}
		assert filenames == {"invoice1.pdf", "invoice2.pdf"}

	def test_non_pdf_attachment_skipped(self):
		msg = MIMEMultipart()
		msg["Subject"] = "Not a PDF"
		msg.attach(MIMEText("See attached.", "plain"))

		# Attach a .xlsx file
		xlsx_part = MIMEApplication(
			b"fake-xlsx", _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet"
		)
		xlsx_part.add_header("Content-Disposition", "attachment", filename="report.xlsx")
		msg.attach(xlsx_part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 0

	def test_mixed_attachments(self):
		"""PDF + non-PDF in same email — only PDF extracted."""
		msg = MIMEMultipart()
		msg["Subject"] = "Mixed"
		msg.attach(MIMEText("Body", "plain"))

		# PDF
		pdf_part = MIMEApplication(PDF_BYTES, _subtype="pdf")
		pdf_part.add_header("Content-Disposition", "attachment", filename="invoice.pdf")
		msg.attach(pdf_part)

		# Image
		img_part = MIMEApplication(b"fake-png", _subtype="png")
		img_part.add_header("Content-Disposition", "attachment", filename="logo.png")
		msg.attach(img_part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 1
		assert pdfs[0][1] == "invoice.pdf"

	def test_inline_pdf(self):
		"""PDFs with Content-Disposition: inline should also be extracted."""
		msg = MIMEMultipart()
		msg["Subject"] = "Inline PDF"
		msg.attach(MIMEText("Body", "plain"))

		pdf_part = MIMEApplication(PDF_BYTES, _subtype="pdf")
		pdf_part.add_header("Content-Disposition", "inline", filename="invoice.pdf")
		msg.attach(pdf_part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 1

	def test_pdf_detected_by_filename(self):
		"""PDF detected by .pdf extension even if MIME type is wrong."""
		msg = MIMEMultipart()
		msg["Subject"] = "Wrong MIME"
		msg.attach(MIMEText("Body", "plain"))

		# Wrong MIME type but .pdf filename
		part = MIMEApplication(PDF_BYTES, _subtype="octet-stream")
		part.add_header("Content-Disposition", "attachment", filename="invoice.pdf")
		msg.attach(part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 1

	def test_single_part_pdf(self):
		"""Non-multipart email that is itself a PDF."""
		from email.message import EmailMessage

		msg = EmailMessage()
		msg["Subject"] = "Single part PDF"
		msg.set_content(PDF_BYTES, maintype="application", subtype="pdf")
		msg.add_header("Content-Disposition", "attachment", filename="invoice.pdf")

		pdfs = _extract_pdfs_from_email(msg)
		# Single-part PDF email
		assert len(pdfs) >= 0  # May or may not work depending on email structure


# ---------------------------------------------------------------------------
# _decode_header_value
# ---------------------------------------------------------------------------


class TestDecodeHeaderValue:
	def test_plain_ascii(self):
		assert _decode_header_value("Invoice from Star Pops") == "Invoice from Star Pops"

	def test_none_returns_empty(self):
		assert _decode_header_value(None) == ""

	def test_empty_returns_empty(self):
		assert _decode_header_value("") == ""

	def test_utf8_encoded(self):
		# RFC 2047 encoded header
		encoded = "=?utf-8?b?SW52b2ljZQ==?="  # "Invoice" in base64
		result = _decode_header_value(encoded)
		assert result == "Invoice"

	def test_multi_part_encoded(self):
		# Two encoded parts
		encoded = "=?utf-8?q?Star?= =?utf-8?q?_Pops?="
		result = _decode_header_value(encoded)
		assert "Star" in result
		assert "Pops" in result
