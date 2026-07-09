"""v1.8.0 Q7(b): the image decode-verify gate on every ingest path.

Before v1.8.0 only ``upload_fleet_slip`` decode-verified images (v1.5.x); a
JPEG with a valid magic header but an undecodable body sailed through manual
upload, email ingest, and all three Drive polls — then 500'd inside PIL when
Frappe built a thumbnail. The gate (``api.is_image_decodable``) now runs on
every path; on Drive paths a decode failure lands in the SAME
``_record_drive_scan_failure`` accounting as other pre-enqueue validation
failures, so it counts toward MAX_DRIVE_RETRIES instead of bypassing the cap.

Tests feed a REAL PIL-encoded JPEG and a real corrupt body (ADR-0009: real
values, not echo mocks).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import erpocr_integration.api
import erpocr_integration.tasks.drive_integration
import erpocr_integration.tasks.email_monitor

# ---------------------------------------------------------------------------
# Real test payloads
# ---------------------------------------------------------------------------


def _encode_jpeg() -> bytes:
	from io import BytesIO

	from PIL import Image

	buf = BytesIO()
	Image.new("RGB", (4, 4), (120, 80, 200)).save(buf, format="JPEG")
	return buf.getvalue()


_JPEG = _encode_jpeg()

# Valid JPEG magic header, garbage body: passes the magic-byte gate, fails
# decode-verify — the exact class that used to 500 downstream.
_CORRUPT_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200

# Minimal valid PDF — PIL doesn't raster PDFs, the gate must not touch them.
_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


# ---------------------------------------------------------------------------
# The shared gate itself
# ---------------------------------------------------------------------------


class TestIsImageDecodable:
	def test_real_jpeg_passes(self):
		assert erpocr_integration.api.is_image_decodable(_JPEG) is True

	def test_corrupt_body_fails(self):
		assert erpocr_integration.api.is_image_decodable(_CORRUPT_JPEG) is False

	def test_empty_fails(self):
		assert erpocr_integration.api.is_image_decodable(b"") is False


# ---------------------------------------------------------------------------
# Manual upload (api.upload_pdf)
# ---------------------------------------------------------------------------


class TestUploadPdfDecodeGate:
	def _setup(self, mock_frappe, sample_settings, filename, content):
		mock_file = MagicMock()
		mock_file.filename = filename
		mock_file.tell.return_value = len(content)
		mock_file.read.return_value = content

		mock_frappe.request = MagicMock()
		mock_frappe.request.files = {"file": mock_file}
		mock_frappe.has_permission = MagicMock(return_value=True)
		mock_frappe.session.user = "test@example.com"
		mock_frappe.db.count.return_value = 0
		mock_frappe.get_single = MagicMock(return_value=sample_settings)

		placeholder = MagicMock()
		placeholder.name = "OCR-IMP-001"
		mock_frappe.get_doc = MagicMock(return_value=placeholder)
		mock_frappe.enqueue = MagicMock()

	def test_corrupt_image_rejected_before_any_record(self, mock_frappe, sample_settings):
		self._setup(mock_frappe, sample_settings, "scan.jpg", _CORRUPT_JPEG)

		with pytest.raises(Exception):
			erpocr_integration.api.upload_pdf()

		mock_frappe.get_doc.assert_not_called()  # no placeholder, no File
		mock_frappe.enqueue.assert_not_called()

	def test_real_image_passes(self, mock_frappe, sample_settings):
		self._setup(mock_frappe, sample_settings, "scan.jpg", _JPEG)

		result = erpocr_integration.api.upload_pdf()

		assert result["status"] == "processing"
		mock_frappe.enqueue.assert_called_once()

	def test_pdf_not_raster_gated(self, mock_frappe, sample_settings):
		"""PDFs skip the image gate entirely (PIL can't raster them)."""
		self._setup(mock_frappe, sample_settings, "invoice.pdf", _PDF)

		result = erpocr_integration.api.upload_pdf()

		assert result["status"] == "processing"


# ---------------------------------------------------------------------------
# Email ingest (email_monitor._process_email)
# ---------------------------------------------------------------------------


class TestEmailDecodeGate:
	def _make_email_msg(self, image_bytes, filename="slip.jpg"):
		from email.mime.image import MIMEImage
		from email.mime.multipart import MIMEMultipart
		from email.mime.text import MIMEText

		msg = MIMEMultipart()
		msg["Subject"] = "Fuel slip"
		msg["From"] = "billing@example.com"
		msg["To"] = "invoices@example.com"
		msg["Message-ID"] = "<decode-gate-test@example.com>"
		msg.attach(MIMEText("See attached.", "plain"))
		img_part = MIMEImage(image_bytes, _subtype="jpeg")
		img_part.add_header("Content-Disposition", "attachment", filename=filename)
		msg.attach(img_part)
		return msg

	def _run(self, mock_frappe, sample_settings, image_bytes):
		mail = MagicMock()
		raw_email = self._make_email_msg(image_bytes).as_bytes()
		mail.uid.return_value = ("OK", [(b"1 (BODY.PEEK[] {999})", raw_email)])
		email_account = SimpleNamespace(email_id="invoices@example.com")

		mock_frappe.get_all = MagicMock(return_value=[])
		mock_frappe.db.exists = MagicMock(return_value=True)

		placeholder = MagicMock()
		placeholder.name = "OCR-IMP-200"
		mock_frappe.get_doc = MagicMock(return_value=placeholder)
		mock_frappe.enqueue = MagicMock()

		erpocr_integration.tasks.email_monitor._process_email(
			mail, b"1", email_account, sample_settings, use_uid=True
		)

	def test_corrupt_attachment_skipped(self, mock_frappe, sample_settings):
		self._run(mock_frappe, sample_settings, _CORRUPT_JPEG)

		mock_frappe.get_doc.assert_not_called()
		mock_frappe.enqueue.assert_not_called()

	def test_real_attachment_enqueued(self, mock_frappe, sample_settings):
		self._run(mock_frappe, sample_settings, _JPEG)

		mock_frappe.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# Drive polls — all three process functions land the decode failure in
# _record_drive_scan_failure (Error placeholder + retry accounting).
# ---------------------------------------------------------------------------


class TestDriveDecodeGate:
	def _run_process_fn(self, mock_frappe, sample_settings, process_fn, content):
		service = MagicMock()
		file_info = {"id": "drive-decode-1", "name": "slip.jpg"}

		mock_frappe.get_all = MagicMock(return_value=[])  # no dedup hits
		placeholder = MagicMock()
		placeholder.name = "PLACEHOLDER-1"
		mock_frappe.get_doc = MagicMock(return_value=placeholder)
		mock_frappe.enqueue = MagicMock()

		with patch.object(erpocr_integration.tasks.drive_integration, "_download_file", return_value=content):
			result = process_fn(service, file_info, sample_settings)
		return result

	@pytest.mark.parametrize(
		"process_fn_name",
		["_process_scan_file", "_process_dn_scan_file", "_process_fleet_scan_file"],
	)
	def test_corrupt_image_lands_in_retry_accounting(self, mock_frappe, sample_settings, process_fn_name):
		process_fn = getattr(erpocr_integration.tasks.drive_integration, process_fn_name)
		result = self._run_process_fn(mock_frappe, sample_settings, process_fn, _CORRUPT_JPEG)

		assert result is False
		mock_frappe.enqueue.assert_not_called()
		# The failure was logged with the decode-specific message ...
		assert any(
			"not decodable" in str(call.kwargs.get("message", "")) + str(call.args)
			for call in mock_frappe.log_error.call_args_list
		)
		# ... and an Error placeholder was inserted so MAX_DRIVE_RETRIES engages.
		placeholder_dicts = [
			call.args[0]
			for call in mock_frappe.get_doc.call_args_list
			if call.args and isinstance(call.args[0], dict) and call.args[0].get("status") == "Error"
		]
		assert len(placeholder_dicts) == 1
		# First attempt records retry_count 0 — the dedup branch on the NEXT
		# poll counts it and increments toward MAX_DRIVE_RETRIES.
		assert placeholder_dicts[0]["drive_retry_count"] == 0
		assert placeholder_dicts[0]["drive_file_id"] == "drive-decode-1"

	def test_fleet_real_image_still_enqueued(self, mock_frappe, sample_settings):
		"""The gate must not over-block: a real JPEG proceeds to enqueue."""
		result = self._run_process_fn(
			mock_frappe,
			sample_settings,
			erpocr_integration.tasks.drive_integration._process_fleet_scan_file,
			_JPEG,
		)

		assert result is True
		mock_frappe.enqueue.assert_called_once()
