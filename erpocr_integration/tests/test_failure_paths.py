"""Tests for failure-path orchestration — enqueue failures, dedup, retry, archive moves."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import pytest

# Pre-import modules so patch() can resolve them
import erpocr_integration.api
import erpocr_integration.tasks.drive_integration
import erpocr_integration.tasks.email_monitor
import erpocr_integration.tasks.gemini_extract

# ---------------------------------------------------------------------------
# 1. upload_file enqueue failure cleanup (api.py)
# ---------------------------------------------------------------------------


class TestUploadFileEnqueueFailure:
	"""When frappe.enqueue() fails in upload_file, the placeholder OCR Import
	should be marked Error (not left as stale Pending)."""

	def _setup_upload_mocks(self, mock_frappe, sample_settings, enqueue_side_effect=None):
		"""Common setup for upload_file tests."""
		mock_file = MagicMock()
		mock_file.filename = "invoice.pdf"
		mock_file.tell.return_value = 1000
		mock_file.read.return_value = b"%PDF-1.4 test"

		mock_frappe.request = MagicMock()
		mock_frappe.request.files = {"file": mock_file}
		mock_frappe.has_permission = MagicMock(return_value=True)
		mock_frappe.session.user = "test@example.com"
		mock_frappe.get_single = MagicMock(return_value=sample_settings)

		placeholder = MagicMock()
		placeholder.name = "OCR-IMP-001"
		mock_frappe.get_doc = MagicMock(return_value=placeholder)

		if enqueue_side_effect:
			mock_frappe.enqueue = MagicMock(side_effect=enqueue_side_effect)
		else:
			mock_frappe.enqueue = MagicMock()

		return placeholder

	def test_enqueue_failure_marks_placeholder_as_error(self, mock_frappe, sample_settings):
		"""Enqueue failure should set status=Error on the existing placeholder."""
		self._setup_upload_mocks(mock_frappe, sample_settings, enqueue_side_effect=Exception("Redis down"))

		with pytest.raises(Exception):
			erpocr_integration.api.upload_file()

		mock_frappe.db.set_value.assert_called_with("OCR Import", "OCR-IMP-001", "status", "Error")

	def test_enqueue_success_returns_processing(self, mock_frappe, sample_settings):
		"""Normal flow: enqueue succeeds, returns processing status."""
		self._setup_upload_mocks(mock_frappe, sample_settings)

		result = erpocr_integration.api.upload_file()

		assert result["ocr_import"] == "OCR-IMP-001"
		assert result["status"] == "processing"
		# set_value should NOT have been called with Error status
		mock_frappe.db.set_value.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Email enqueue failure + stale Pending prevention (email_monitor.py)
# ---------------------------------------------------------------------------


class TestEmailEnqueueFailure:
	"""When enqueue fails during email processing, the existing placeholder
	should be marked Error (not create a second record)."""

	def _make_email_msg(self):
		"""Create a minimal MIME email with a PDF attachment."""
		from email.mime.application import MIMEApplication
		from email.mime.multipart import MIMEMultipart
		from email.mime.text import MIMEText

		msg = MIMEMultipart()
		msg["Subject"] = "Invoice"
		msg["From"] = "billing@example.com"
		msg["To"] = "invoices@example.com"
		msg["Message-ID"] = "<test-enqueue-fail@example.com>"
		msg.attach(MIMEText("See attached.", "plain"))
		pdf_part = MIMEApplication(b"%PDF-1.4 fake", _subtype="pdf")
		pdf_part.add_header("Content-Disposition", "attachment", filename="inv.pdf")
		msg.attach(pdf_part)
		return msg

	def test_enqueue_failure_marks_existing_placeholder_as_error(self, mock_frappe, sample_settings):
		"""When enqueue fails after placeholder created, mark it Error."""
		mail = MagicMock()
		raw_email = self._make_email_msg().as_bytes()
		mail.uid.return_value = ("OK", [(b"1 (BODY.PEEK[] {999})", raw_email)])

		email_account = SimpleNamespace(email_id="invoices@example.com")

		# No existing records for this message_id
		mock_frappe.get_all = MagicMock(return_value=[])
		mock_frappe.db.exists = MagicMock(return_value=True)

		# Create placeholder successfully
		placeholder = MagicMock()
		placeholder.name = "OCR-IMP-100"
		mock_frappe.get_doc = MagicMock(return_value=placeholder)

		# Enqueue FAILS
		mock_frappe.enqueue = MagicMock(side_effect=Exception("Queue full"))
		mock_frappe.get_traceback = MagicMock(return_value="<traceback>")

		erpocr_integration.tasks.email_monitor._process_email(
			mail, b"1", email_account, sample_settings, use_uid=True
		)

		# Verify: placeholder marked Error
		mock_frappe.db.set_value.assert_any_call("OCR Import", "OCR-IMP-100", "status", "Error")

	def test_enqueue_failure_before_insert_creates_error_record(self, mock_frappe, sample_settings):
		"""When placeholder insert fails, create a new Error record."""
		mail = MagicMock()
		raw_email = self._make_email_msg().as_bytes()
		mail.uid.return_value = ("OK", [(b"1 (BODY.PEEK[] {999})", raw_email)])

		email_account = SimpleNamespace(email_id="invoices@example.com")
		mock_frappe.get_all = MagicMock(return_value=[])
		mock_frappe.db.exists = MagicMock(return_value=True)

		# get_doc for placeholder fails on insert
		placeholder = MagicMock()
		placeholder.name = None
		placeholder.insert = MagicMock(side_effect=Exception("DB error"))
		mock_frappe.get_doc = MagicMock(return_value=placeholder)
		mock_frappe.get_traceback = MagicMock(return_value="<traceback>")

		erpocr_integration.tasks.email_monitor._process_email(
			mail, b"1", email_account, sample_settings, use_uid=True
		)

		# Should have logged an error
		assert mock_frappe.log_error.called


# ---------------------------------------------------------------------------
# 3. Drive dedup/retry with mixed statuses (drive_integration.py)
# ---------------------------------------------------------------------------


class TestDriveScanDedup:
	"""_process_scan_file handles dedup: skip if any non-Error record exists,
	retry only if ALL records are Error."""

	def test_skip_if_completed_record_exists(self, mock_frappe, sample_settings):
		"""File with a Completed record should be skipped entirely."""
		service = MagicMock()
		file_info = {"id": "drive-xyz", "name": "invoice.pdf"}

		mock_frappe.get_all = MagicMock(
			return_value=[
				SimpleNamespace(name="OCR-IMP-1", status="Completed"),
				SimpleNamespace(name="OCR-IMP-2", status="Error"),
			]
		)

		erpocr_integration.tasks.drive_integration._process_scan_file(service, file_info, sample_settings)

		mock_frappe.delete_doc.assert_not_called()
		mock_frappe.enqueue.assert_not_called()

	def test_skip_if_pending_record_exists(self, mock_frappe, sample_settings):
		"""File with a Pending record should be skipped (still processing)."""
		service = MagicMock()
		file_info = {"id": "drive-xyz", "name": "invoice.pdf"}

		mock_frappe.get_all = MagicMock(return_value=[SimpleNamespace(name="OCR-IMP-1", status="Pending")])

		erpocr_integration.tasks.drive_integration._process_scan_file(service, file_info, sample_settings)

		mock_frappe.delete_doc.assert_not_called()
		mock_frappe.enqueue.assert_not_called()

	def test_retry_when_all_error(self, mock_frappe, sample_settings):
		"""When ALL records for a drive_file_id are Error, delete them and retry."""
		service = MagicMock()
		file_info = {"id": "drive-retry-1", "name": "retry.pdf"}

		mock_frappe.get_all = MagicMock(
			return_value=[
				SimpleNamespace(name="OCR-IMP-E1", status="Error"),
				SimpleNamespace(name="OCR-IMP-E2", status="Error"),
			]
		)

		with patch.object(
			erpocr_integration.tasks.drive_integration,
			"_download_file",
			return_value=b"%PDF-1.4 test content",
		):
			new_placeholder = MagicMock()
			new_placeholder.name = "OCR-IMP-NEW"
			mock_frappe.get_doc = MagicMock(return_value=new_placeholder)
			mock_frappe.enqueue = MagicMock()

			erpocr_integration.tasks.drive_integration._process_scan_file(service, file_info, sample_settings)

		assert mock_frappe.delete_doc.call_count == 2
		mock_frappe.delete_doc.assert_any_call(
			"OCR Import", "OCR-IMP-E1", force=True, ignore_permissions=True
		)
		mock_frappe.delete_doc.assert_any_call(
			"OCR Import", "OCR-IMP-E2", force=True, ignore_permissions=True
		)
		mock_frappe.enqueue.assert_called_once()

	def test_new_file_creates_placeholder_and_enqueues(self, mock_frappe, sample_settings):
		"""Brand new file (no existing records) creates placeholder and enqueues."""
		service = MagicMock()
		file_info = {"id": "drive-new-1", "name": "new-invoice.pdf"}

		mock_frappe.get_all = MagicMock(return_value=[])

		with patch.object(
			erpocr_integration.tasks.drive_integration,
			"_download_file",
			return_value=b"%PDF-1.4 new content",
		):
			new_placeholder = MagicMock()
			new_placeholder.name = "OCR-IMP-NEW-2"
			mock_frappe.get_doc = MagicMock(return_value=new_placeholder)
			mock_frappe.enqueue = MagicMock()

			erpocr_integration.tasks.drive_integration._process_scan_file(service, file_info, sample_settings)

		mock_frappe.delete_doc.assert_not_called()
		mock_frappe.enqueue.assert_called_once()

	def test_enqueue_failure_deletes_placeholder(self, mock_frappe, sample_settings):
		"""When enqueue fails in Drive scan, delete placeholder so next poll retries."""
		service = MagicMock()
		file_info = {"id": "drive-fail-1", "name": "failing.pdf"}

		mock_frappe.get_all = MagicMock(return_value=[])

		with patch.object(
			erpocr_integration.tasks.drive_integration,
			"_download_file",
			return_value=b"%PDF-1.4 content",
		):
			new_placeholder = MagicMock()
			new_placeholder.name = "OCR-IMP-FAIL"
			mock_frappe.get_doc = MagicMock(return_value=new_placeholder)
			mock_frappe.enqueue = MagicMock(side_effect=Exception("Redis down"))

			erpocr_integration.tasks.drive_integration._process_scan_file(service, file_info, sample_settings)

		mock_frappe.delete_doc.assert_called_with(
			"OCR Import", "OCR-IMP-FAIL", force=True, ignore_permissions=True
		)


# ---------------------------------------------------------------------------
# 4. Archive move failure path (api.py / drive_integration.py)
# ---------------------------------------------------------------------------


class TestArchiveMoveFailure:
	"""When archive move fails, extraction data should still be saved."""

	def test_move_failure_logs_error_but_extraction_succeeds(self, mock_frappe, sample_settings):
		"""Archive move failure should be logged but not fail the overall extraction."""
		mock_invoice_list = [
			{
				"header_fields": {
					"supplier_name": "Test Supplier",
					"invoice_number": "INV-001",
					"invoice_date": "2024-01-01",
					"total_amount": 100.0,
					"tax_amount": 0,
					"confidence": 0.9,
				},
				"line_items": [],
				"raw_response": "{}",
				"extraction_time": 1.0,
			}
		]

		with (
			patch.object(
				erpocr_integration.tasks.gemini_extract,
				"extract_invoice_data",
				return_value=mock_invoice_list,
			),
			patch.object(
				erpocr_integration.tasks.drive_integration,
				"move_file_to_archive",
				side_effect=Exception("Drive API timeout"),
			),
		):
			mock_frappe.db.get_value = MagicMock(return_value="existing-drive-id")
			mock_frappe.get_cached_doc = MagicMock(return_value=sample_settings)

			placeholder = MagicMock()
			placeholder.items = []
			placeholder.append = MagicMock(
				side_effect=lambda table, row: placeholder.items.append(SimpleNamespace(**row))
			)
			placeholder.email_message_id = None
			mock_frappe.get_doc = MagicMock(return_value=placeholder)

			erpocr_integration.api.gemini_process(
				file_content=b"%PDF-1.4 test",
				filename="invoice.pdf",
				ocr_import_name="OCR-IMP-ARCHIVE",
				source_type="Gemini Drive Scan",
				uploaded_by="Administrator",
			)

		# Move failure should be logged
		error_calls = [c for c in mock_frappe.log_error.call_args_list if "Drive Move Failed" in str(c)]
		assert len(error_calls) > 0

		# But the record should still have been saved
		placeholder.save.assert_called_with(ignore_permissions=True)

	def test_move_returns_partial_result_on_failure(self, mock_frappe):
		"""move_file_to_archive returns file_id but null link/path on failure."""
		mock_frappe.get_single = MagicMock(
			return_value=SimpleNamespace(
				drive_integration_enabled=True,
				drive_archive_folder_id="folder-123",
				get_password=MagicMock(return_value='{"type": "service_account"}'),
			)
		)

		with patch.object(
			erpocr_integration.tasks.drive_integration,
			"_get_drive_service",
			side_effect=Exception("Auth failed"),
		):
			result = erpocr_integration.tasks.drive_integration.move_file_to_archive(
				"file-id-abc", supplier_name="Test", invoice_date="2024-01-01"
			)

		assert result["file_id"] == "file-id-abc"
		assert result["shareable_link"] is None
		assert result["folder_path"] is None


# ---------------------------------------------------------------------------
# 5. Retry endpoint permission + source-type gating (api.py)
# ---------------------------------------------------------------------------


class TestRetryGeminiExtraction:
	"""retry_gemini_extraction enforces permissions and source-type gating."""

	def test_rejects_non_error_status(self, mock_frappe):
		"""Cannot retry an OCR Import that isn't in Error status."""
		doc = SimpleNamespace(
			name="OCR-IMP-001",
			status="Matched",
			source_type="Gemini Manual Upload",
			drive_file_id="drive-123",
		)
		mock_frappe.get_doc = MagicMock(return_value=doc)

		with pytest.raises(Exception):
			erpocr_integration.api.retry_gemini_extraction("OCR-IMP-001")

		mock_frappe.throw.assert_called()

	def test_rejects_non_gemini_source_type(self, mock_frappe):
		"""Cannot retry a non-Gemini source type."""
		doc = SimpleNamespace(
			name="OCR-IMP-002",
			status="Error",
			source_type="Manual Entry",
			drive_file_id="drive-123",
		)
		mock_frappe.get_doc = MagicMock(return_value=doc)

		with pytest.raises(Exception):
			erpocr_integration.api.retry_gemini_extraction("OCR-IMP-002")

	def test_rejects_without_permission(self, mock_frappe):
		"""Permission check blocks unauthorized users."""
		mock_frappe.has_permission = MagicMock(return_value=False)

		with pytest.raises(Exception):
			erpocr_integration.api.retry_gemini_extraction("OCR-IMP-003")

		mock_frappe.throw.assert_called()

	def test_rejects_without_drive_file_id(self, mock_frappe):
		"""Cannot retry if no PDF is stored (drive_file_id missing)."""
		doc = SimpleNamespace(
			name="OCR-IMP-004",
			status="Error",
			source_type="Gemini Manual Upload",
			drive_file_id=None,
		)
		mock_frappe.get_doc = MagicMock(return_value=doc)

		with pytest.raises(Exception):
			erpocr_integration.api.retry_gemini_extraction("OCR-IMP-004")

	def test_accepts_all_gemini_source_types(self, mock_frappe):
		"""All three Gemini source types should be accepted for retry."""
		for source_type in ("Gemini Manual Upload", "Gemini Email", "Gemini Drive Scan"):
			# Reset between iterations
			mock_frappe.throw.reset_mock()
			mock_frappe.enqueue.reset_mock()

			doc = SimpleNamespace(
				name=f"OCR-IMP-{source_type}",
				status="Error",
				source_type=source_type,
				drive_file_id="drive-123",
				source_filename="test.pdf",
				db_set=MagicMock(),
			)
			mock_frappe.get_doc = MagicMock(return_value=doc)

			with patch.object(
				erpocr_integration.tasks.drive_integration,
				"download_file_from_drive",
				return_value=b"%PDF-1.4 test",
			):
				mock_frappe.enqueue = MagicMock()
				result = erpocr_integration.api.retry_gemini_extraction(f"OCR-IMP-{source_type}")

			assert result is not None
