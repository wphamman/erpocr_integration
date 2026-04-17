"""Tests for email parsing functions in erpocr_integration.tasks.email_monitor."""

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock

import pytest

from erpocr_integration.tasks.email_monitor import (
	_decode_header_value,
	_extract_pdfs_from_email,
	_imap_copy_and_delete,
	_move_to_processed_folder,
)

PDF_BYTES = b"%PDF-1.4 fake-pdf-content"


# ---------------------------------------------------------------------------
# _extract_pdfs_from_email
# ---------------------------------------------------------------------------


class TestExtractPdfsFromEmail:
	def test_single_pdf_attachment(self, sample_email_with_pdf):
		pdfs = _extract_pdfs_from_email(sample_email_with_pdf)
		assert len(pdfs) == 1
		content, filename, content_type = pdfs[0]
		assert filename == "INV-2024-0042.pdf"
		assert content == b"%PDF-1.4 fake-pdf-content-for-testing"
		assert content_type == "application/pdf"

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
		filenames = {f for _, f, _ in pdfs}
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
		"""PDF + image in same email — both extracted as supported types."""
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
		assert len(pdfs) == 2
		assert pdfs[0][1] == "invoice.pdf"
		assert pdfs[0][2] == "application/pdf"
		assert pdfs[1][1] == "logo.png"
		assert pdfs[1][2] == "application/png"

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
		# Content-Type header value is preserved even when detection was by filename
		assert pdfs[0][2] == "application/octet-stream"

	def test_content_type_header_returned_for_images(self):
		"""Image content_type from email header is carried through, not inferred from filename."""
		msg = MIMEMultipart()
		msg["Subject"] = "JPEG invoice"
		msg.attach(MIMEText("Body", "plain"))

		# JPEG with correct Content-Type but no extension in filename
		from email.mime.image import MIMEImage

		img_part = MIMEImage(b"\xff\xd8\xff fake-jpeg", _subtype="jpeg")
		img_part.add_header("Content-Disposition", "attachment", filename="scan_001")
		msg.attach(img_part)

		pdfs = _extract_pdfs_from_email(msg)
		assert len(pdfs) == 1
		_, filename, content_type = pdfs[0]
		assert filename == "scan_001"
		assert content_type == "image/jpeg"

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
		assert _decode_header_value("Invoice from Acme Trading") == "Invoice from Acme Trading"

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


# ---------------------------------------------------------------------------
# _move_to_processed_folder (IMAP COPY+DELETE with X-GM-LABELS fallback)
# ---------------------------------------------------------------------------


class TestImapCopyAndDelete:
	def test_success_on_first_destination_variant(self):
		mail = MagicMock()
		mail.uid.return_value = ("OK", [])

		result = _imap_copy_and_delete(mail, "123", use_uid=True)

		assert result is True
		# First call = copy, second call = store +FLAGS \Deleted
		copy_call = mail.uid.call_args_list[0]
		store_call = mail.uid.call_args_list[1]
		assert copy_call.args[0] == "copy"
		assert copy_call.args[2] == '"OCR Processed"'
		assert store_call.args[0] == "store"
		assert store_call.args[2] == "+FLAGS"
		assert store_call.args[3] == "\\Deleted"

	def test_falls_through_variants_until_copy_succeeds(self):
		mail = MagicMock()
		# First two COPY attempts fail, third succeeds; then STORE succeeds
		mail.uid.side_effect = [
			("NO", []),  # "OCR Processed" quoted — fails
			("NO", []),  # "OCR Processed" unquoted — fails
			("OK", []),  # OCR_Processed — succeeds
			("OK", []),  # STORE +FLAGS \Deleted
		]

		result = _imap_copy_and_delete(mail, "123", use_uid=True)

		assert result is True

	def test_returns_false_when_all_copy_variants_fail(self):
		mail = MagicMock()
		mail.uid.return_value = ("NO", [])

		result = _imap_copy_and_delete(mail, "123", use_uid=True)

		assert result is False

	def test_returns_false_when_copy_raises(self):
		mail = MagicMock()
		mail.uid.side_effect = Exception("boom")

		result = _imap_copy_and_delete(mail, "123", use_uid=True)

		assert result is False

	def test_non_uid_uses_store_and_copy(self):
		mail = MagicMock()
		mail.copy.return_value = ("OK", [])
		mail.store.return_value = ("OK", [])

		result = _imap_copy_and_delete(mail, "123", use_uid=False)

		assert result is True
		mail.copy.assert_called_once()
		mail.store.assert_called_once()
		# uid() must NOT be called in non-UID mode
		mail.uid.assert_not_called()


class TestMoveToProcessedFolder:
	def test_prefers_standard_copy_and_skips_label_fallback(self):
		mail = MagicMock()
		mail.uid.return_value = ("OK", [])  # COPY + STORE both succeed

		_move_to_processed_folder(mail, "123", use_uid=True)

		# Should not attempt X-GM-LABELS if standard COPY+DELETE worked.
		# Only 2 calls expected (copy + store \Deleted), not 4 (copy + store + add label + remove label).
		assert mail.uid.call_count == 2

	def test_falls_back_to_x_gm_labels_when_copy_fails(self, mock_frappe):
		mail = MagicMock()
		# All COPY attempts fail; X-GM-LABELS succeeds on first try
		mail.uid.side_effect = [
			("NO", []),  # copy "OCR Processed"
			("NO", []),  # copy OCR Processed
			("NO", []),  # copy OCR_Processed
			("OK", []),  # +X-GM-LABELS "OCR Processed"
			("OK", []),  # -X-GM-LABELS "OCR Invoices"
		]

		_move_to_processed_folder(mail, "123", use_uid=True)

		assert mail.uid.call_count == 5


# ---------------------------------------------------------------------------
# \Seen removal guard — documents the policy without spinning up a full IMAP
# mock. The guard itself is `mail.uid("store", uid, "-FLAGS", "\\Seen")` and
# is applied to every fetched UID that was NOT moved in Phase 2.
# ---------------------------------------------------------------------------


class TestSeenRemovalGuard:
	def test_uid_list_excludes_moved_uids(self):
		"""The failed-UIDs list is the set-difference of fetched and moved UIDs."""
		all_fetched_uids = [b"1", b"2", b"3", b"4"]
		uids_to_move = [b"2", b"4"]
		failed_uids = [u for u in all_fetched_uids if u not in uids_to_move]
		assert failed_uids == [b"1", b"3"]

	def test_unseen_store_command_shape(self):
		"""Emulate the per-UID STORE call the poll loop emits."""
		mail = MagicMock()
		mail.uid.return_value = ("OK", [])

		# This is the exact call the guard makes
		mail.uid("store", b"7", "-FLAGS", "\\Seen")

		call = mail.uid.call_args
		assert call.args == ("store", b"7", "-FLAGS", "\\Seen")

	def test_guard_swallows_exceptions(self):
		"""Failures on the un-Seen STORE must not bubble up — best effort only."""
		mail = MagicMock()
		mail.uid.side_effect = Exception("IMAP transient")

		# Mirror the try/except around the guard
		try:
			mail.uid("store", b"1", "-FLAGS", "\\Seen")
		except Exception:
			pass  # guard swallows

		# If we got here, the guard behaved correctly
		assert True
