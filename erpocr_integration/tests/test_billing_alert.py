"""Gemini billing-failure alert (v1.14.0, Q23): detection, throttle, recipients,
banner endpoint, and Drive self-heal."""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

import erpocr_integration.api
import erpocr_integration.tasks.billing_alert as billing
import erpocr_integration.tasks.drive_integration as drive
import erpocr_integration.tasks.gemini_extract as gx
from erpocr_integration.tasks.billing_alert import GeminiBillingError, is_billing_failure


def _response(status, body):
	"""A realistic requests.Response: status code + JSON body."""
	import json

	r = requests.Response()
	r.status_code = status
	r._content = json.dumps(body).encode()
	r.url = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"
	return r


PREPAY_429 = {
	"error": {
		"code": 429,
		"message": "Your prepayment credits are depleted. Please go to AI Studio to manage your project.",
		"status": "RESOURCE_EXHAUSTED",
	}
}
RATE_LIMIT_429 = {
	"error": {
		"code": 429,
		"message": "You exceeded your current quota, please check your plan and billing details.",
		"status": "RESOURCE_EXHAUSTED",
	}
}
OK_BODY = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


@pytest.fixture(autouse=True)
def _isolate(mock_frappe):
	"""Give each test a clean, in-memory OCR Settings state."""
	state = {"billing_failure_since": None, "billing_alert_last_sent": None, "billing_alert_recipients": ""}
	saved = {
		k: getattr(mock_frappe.db, k)
		for k in ("get_single_value", "set_single_value", "get_value", "set_value")
	}
	mock_frappe.db.get_single_value = MagicMock(side_effect=lambda dt, f: state.get(f))
	mock_frappe.db.set_single_value = MagicMock(side_effect=lambda dt, f, v, **kw: state.__setitem__(f, v))
	mock_frappe.sendmail = MagicMock()
	mock_frappe.local = SimpleNamespace(site="test.example.com")
	mock_frappe.db.commit.reset_mock()
	mock_frappe.log_error.reset_mock()
	yield state
	for k, v in saved.items():
		setattr(mock_frappe.db, k, v)


# ------------------------------------------------------------------ detection


class TestDetection:
	def test_402_is_billing(self):
		assert is_billing_failure(402, "") is True

	def test_429_prepay_is_billing(self):
		assert is_billing_failure(429, _response(429, PREPAY_429).text) is True

	def test_429_plain_rate_limit_is_not_billing(self):
		# Google's ordinary quota text mentions "billing details": must stay retryable.
		assert is_billing_failure(429, _response(429, RATE_LIMIT_429).text) is False

	def test_429_without_resource_exhausted_is_not_billing(self):
		assert is_billing_failure(429, "credits") is False

	def test_other_statuses_are_not_billing(self):
		assert is_billing_failure(500, "payment credits") is False
		assert is_billing_failure(None, "") is False


class TestCallGeminiApi:
	def _call(self):
		return gx._call_gemini_api(b"x", "p", {}, "fake-key", "gemini-2.5-flash")

	def test_402_raises_billing_error_without_retry(self, _isolate):
		with (
			patch.object(gx.requests, "post", return_value=_response(402, {"error": {"code": 402}})) as post,
			patch.object(gx.time, "sleep") as sleep,
		):
			with pytest.raises(GeminiBillingError) as exc:
				self._call()
		assert post.call_count == 1
		sleep.assert_not_called()
		assert "fake-key" not in str(exc.value)
		assert _isolate["billing_failure_since"] is not None

	def test_429_prepay_raises_billing_error(self):
		with (
			patch.object(gx.requests, "post", return_value=_response(429, PREPAY_429)) as post,
			patch.object(gx.time, "sleep"),
		):
			with pytest.raises(GeminiBillingError):
				self._call()
		assert post.call_count == 1

	def test_429_plain_rate_limit_stays_retryable(self, _isolate):
		with (
			patch.object(
				gx.requests, "post", side_effect=[_response(429, RATE_LIMIT_429), _response(200, OK_BODY)]
			) as post,
			patch.object(gx.time, "sleep") as sleep,
		):
			result = self._call()
		assert result == OK_BODY
		assert post.call_count == 2
		sleep.assert_called_once_with(15)
		assert _isolate["billing_failure_since"] is None

	def test_503_stays_retryable(self):
		with (
			patch.object(gx.requests, "post", side_effect=[_response(503, {}), _response(200, OK_BODY)]),
			patch.object(gx.time, "sleep"),
		):
			assert self._call() == OK_BODY

	def test_success_clears_failure_state(self, _isolate):
		_isolate["billing_failure_since"] = datetime.datetime(2026, 10, 23, 11, 30)
		_isolate["billing_alert_last_sent"] = datetime.datetime(2026, 10, 23, 11, 30)
		with patch.object(gx.requests, "post", return_value=_response(200, OK_BODY)):
			self._call()
		assert _isolate["billing_failure_since"] is None
		assert _isolate["billing_alert_last_sent"] is None

	def test_billing_error_passes_through_extract_wrappers(self):
		settings = SimpleNamespace(gemini_model="m")
		settings.get_password = MagicMock(return_value="fake-key")
		with (
			patch.object(gx.frappe, "get_single", return_value=settings),
			patch.object(gx, "_call_gemini_api", side_effect=GeminiBillingError("no pay")),
		):
			for fn in (
				gx.extract_invoice_data,
				gx.extract_delivery_note_data,
				gx.extract_fleet_slip_data,
				gx.extract_statement_data,
			):
				with pytest.raises(GeminiBillingError):
					fn(b"x", "f.pdf")


class TestClassifier:
	def test_classifier_402_raises_billing_error(self, mock_frappe):
		import erpocr_integration.tasks.classify_document as cd

		settings = SimpleNamespace(gemini_model="m")
		settings.get_password = MagicMock(return_value="fake-key")
		mock_frappe.get_single.return_value = settings
		with patch.object(cd.requests, "post", return_value=_response(402, {})):
			with pytest.raises(GeminiBillingError):
				cd.classify_document(b"x", "f.pdf")

	def test_classifier_other_error_still_defaults_to_invoice(self, mock_frappe):
		import erpocr_integration.tasks.classify_document as cd

		settings = SimpleNamespace(gemini_model="m")
		settings.get_password = MagicMock(return_value="fake-key")
		mock_frappe.get_single.return_value = settings
		with patch.object(cd.requests, "post", return_value=_response(500, {})):
			assert cd.classify_document(b"x", "f.pdf") == ("invoice", 0.0)


# ------------------------------------------------------------ throttle + email


class TestThrottle:
	def _fixed_now(self, when):
		return patch.object(billing, "now_datetime", return_value=when)

	def test_first_failure_sends_one_email(self, mock_frappe, _isolate):
		t0 = datetime.datetime(2026, 10, 26, 8, 0)
		with (
			self._fixed_now(t0),
			patch.object(billing, "get_alert_recipients", return_value=["a@x.com"]),
		):
			billing.record_billing_failure()
		assert mock_frappe.sendmail.call_count == 1
		assert mock_frappe.sendmail.call_args.kwargs["now"] is False
		assert _isolate["billing_failure_since"] == t0
		assert _isolate["billing_alert_last_sent"] == t0

	def test_second_failure_within_six_hours_sends_nothing(self, mock_frappe, _isolate):
		t0 = datetime.datetime(2026, 10, 26, 8, 0)
		with patch.object(billing, "get_alert_recipients", return_value=["a@x.com"]):
			with self._fixed_now(t0):
				billing.record_billing_failure()
			with self._fixed_now(t0 + datetime.timedelta(hours=5, minutes=59)):
				billing.record_billing_failure()
		assert mock_frappe.sendmail.call_count == 1
		assert _isolate["billing_failure_since"] == t0  # "since" is not moved

	def test_failure_after_six_hours_sends_again(self, mock_frappe, _isolate):
		t0 = datetime.datetime(2026, 10, 26, 8, 0)
		with patch.object(billing, "get_alert_recipients", return_value=["a@x.com"]):
			with self._fixed_now(t0):
				billing.record_billing_failure()
			with self._fixed_now(t0 + datetime.timedelta(hours=6, minutes=1)):
				billing.record_billing_failure()
		assert mock_frappe.sendmail.call_count == 2

	def test_email_has_no_key_and_plain_words(self, mock_frappe):
		with patch.object(billing, "get_alert_recipients", return_value=["a@x.com"]):
			billing.record_billing_failure()
		kwargs = mock_frappe.sendmail.call_args.kwargs
		text = kwargs["subject"] + kwargs["message"]
		assert "Retry Extraction" in text and "billing account" in text
		assert "test.example.com" in text
		assert "api_key" not in text.lower()

	def test_never_raises_and_does_not_leak(self, mock_frappe):
		mock_frappe.db.get_single_value = MagicMock(side_effect=RuntimeError("secret-key-123"))
		billing.record_billing_failure()  # must not raise
		for call in mock_frappe.log_error.call_args_list:
			assert "secret-key-123" not in str(call)

	def test_no_recipients_sends_nothing(self, mock_frappe):
		with patch.object(billing, "get_alert_recipients", return_value=[]):
			billing.record_billing_failure()
		mock_frappe.sendmail.assert_not_called()


class TestRecipients:
	def test_system_managers_plus_extra_list_deduped(self, mock_frappe, _isolate):
		_isolate["billing_alert_recipients"] = "danell@x.com; Boss@x.com\nwillie@x.com"

		def get_all(doctype, **kw):
			if doctype == "Has Role":
				return [
					SimpleNamespace(parent="Administrator"),
					SimpleNamespace(parent="Guest"),
					SimpleNamespace(parent="willie@x.com"),
					SimpleNamespace(parent="off@x.com"),
				]
			if doctype == "User":
				assert kw["filters"]["enabled"] == 1
				assert "Administrator" not in kw["filters"]["name"][1]
				assert "off@x.com" in kw["filters"]["name"][1]
				return [SimpleNamespace(name="willie@x.com", email="willie@x.com")]
			return []

		with patch.object(mock_frappe, "get_all", side_effect=get_all):
			got = billing.get_alert_recipients()
		assert got == ["willie@x.com", "danell@x.com", "Boss@x.com"]

	def test_queries_have_explicit_plain_order_by(self, mock_frappe):
		seen = []

		def get_all(doctype, **kw):
			seen.append(kw.get("order_by"))
			return [SimpleNamespace(parent="a@x.com")] if doctype == "Has Role" else []

		with patch.object(mock_frappe, "get_all", side_effect=get_all):
			billing.get_alert_recipients()
		assert seen and all(o and "(" not in o for o in seen)


# ---------------------------------------------------------------------- banner


class TestBannerEndpoint:
	def test_up(self, mock_frappe):
		mock_frappe.has_permission = MagicMock(return_value=True)
		assert billing.get_billing_status() == {"down": False, "since": None}

	def test_down_returns_only_down_and_since(self, mock_frappe, _isolate):
		mock_frappe.has_permission = MagicMock(return_value=True)
		_isolate["billing_failure_since"] = datetime.datetime(2026, 10, 23, 11, 30)
		out = billing.get_billing_status()
		assert set(out) == {"down", "since"}
		assert out["down"] is True and out["since"].startswith("2026-10-23 11:30")

	def test_no_permission_refused(self, mock_frappe):
		mock_frappe.has_permission = MagicMock(return_value=False)
		mock_frappe.throw = MagicMock(side_effect=PermissionError)
		with pytest.raises(PermissionError):
			billing.get_billing_status()
		mock_frappe.throw = MagicMock(side_effect=Exception)

	def test_fleet_only_user_allowed(self, mock_frappe):
		mock_frappe.has_permission = MagicMock(side_effect=lambda dt, perm: dt == "OCR Fleet Slip")
		assert billing.get_billing_status()["down"] is False

	def teardown_method(self):
		import sys

		sys.modules["frappe"].has_permission = MagicMock(return_value=True)


# ----------------------------------------------------------------- Drive heal


class _Store:
	"""Tiny in-memory stand-in for the OCR Import table."""

	def __init__(self):
		self.rows = {}
		self.n = 0

	def get_all(self, doctype, **kw):
		if doctype != "OCR Import":
			return []
		fid = kw["filters"]["drive_file_id"]
		return [SimpleNamespace(**r) for r in self.rows.values() if r["drive_file_id"] == fid]

	def get_doc(self, data):
		store = self
		self.n += 1
		doc = MagicMock()
		doc.name = f"OCR-IMP-{self.n:05d}"

		def insert(**kw):
			store.rows[doc.name] = {
				"name": doc.name,
				"status": data["status"],
				"drive_file_id": data["drive_file_id"],
				"drive_retry_count": data.get("drive_retry_count", 0),
			}

		doc.insert = insert
		return doc

	def delete_doc(self, doctype, name, **kw):
		self.rows.pop(name, None)

	def get_value(self, doctype, name, fields, as_dict=False):
		r = self.rows.get(name)
		return {f: r[f] for f in fields} if r else None

	def set_value(self, doctype, name, field, value=None, **kw):
		if isinstance(field, dict):
			self.rows[name].update(field)
		else:
			self.rows[name][field] = value


class TestDriveSelfHeal:
	def test_billing_failure_x10_then_success(self, mock_frappe, sample_settings, _isolate):
		store = _Store()
		jobs = []
		mock_frappe.get_all = MagicMock(side_effect=store.get_all)
		mock_frappe.get_doc = MagicMock(side_effect=store.get_doc)
		mock_frappe.delete_doc = MagicMock(side_effect=store.delete_doc)
		mock_frappe.db.get_value = MagicMock(side_effect=store.get_value)
		mock_frappe.db.set_value = MagicMock(side_effect=store.set_value)
		mock_frappe.enqueue = MagicMock(side_effect=lambda *a, **kw: jobs.append(kw))
		mock_frappe.set_user = MagicMock()

		file_info = {"id": "drive-1", "name": "inv.pdf"}
		extractions = []

		def run_job(job, outcome):
			if outcome == "ok":
				# Billing is fixed: the extraction succeeds and the record moves on.
				extractions.append(1)
				store.rows[job["ocr_import_name"]]["status"] = "Matched"
				return
			with patch(
				"erpocr_integration.tasks.gemini_extract.extract_invoice_data",
				side_effect=GeminiBillingError("no pay"),
			):
				erpocr_integration.api.gemini_process(
					pdf_content=job["pdf_content"],
					filename=job["filename"],
					ocr_import_name=job["ocr_import_name"],
					source_type=job["source_type"],
					uploaded_by=job["uploaded_by"],
					mime_type=job["mime_type"],
				)

		with (
			patch.object(drive, "_download_file", return_value=b"%PDF-1.4 content"),
			patch(
				"erpocr_integration.tasks.classify_document.classify_document", return_value=("invoice", 0.9)
			),
		):
			for _ in range(10):
				assert drive._process_scan_file(MagicMock(), file_info, sample_settings) is True
				run_job(jobs.pop(), "billing")
				# exactly one Error placeholder, never over the cap
				(row,) = store.rows.values()
				assert row["status"] == "Error"
				assert row["drive_retry_count"] < drive.MAX_DRIVE_RETRIES

			# Billing fixed: next poll retries the same file and it extracts.
			assert drive._process_scan_file(MagicMock(), file_info, sample_settings) is True
			run_job(jobs.pop(), "ok")

		assert len(extractions) == 1
		assert not [r for r in store.rows.values() if r["status"] == "Error"]
		assert mock_frappe.enqueue.call_count == 11

	def test_plain_failures_still_hit_the_cap(self, mock_frappe, sample_settings):
		"""A non-billing failure still counts: the cap is unchanged."""
		store = _Store()
		mock_frappe.get_all = MagicMock(side_effect=store.get_all)
		store.rows["E"] = {"name": "E", "status": "Error", "drive_file_id": "d", "drive_retry_count": 3}
		assert drive._process_scan_file(MagicMock(), {"id": "d", "name": "x.pdf"}, sample_settings) is False

	def test_give_back_floors_first_attempt_at_minus_one(self, mock_frappe):
		store = _Store()
		store.rows["A"] = {"name": "A", "status": "Error", "drive_file_id": "d", "drive_retry_count": 0}
		mock_frappe.db.get_value = MagicMock(side_effect=store.get_value)
		mock_frappe.db.set_value = MagicMock(side_effect=store.set_value)
		billing.give_back_drive_attempt("OCR Import", "A")
		assert store.rows["A"]["drive_retry_count"] == -1

	def test_give_back_ignores_non_drive_records(self, mock_frappe):
		mock_frappe.db.get_value = MagicMock(return_value={"drive_file_id": None, "drive_retry_count": 0})
		mock_frappe.db.set_value = MagicMock()
		billing.give_back_drive_attempt("OCR Import", "A")
		mock_frappe.db.set_value.assert_not_called()

	def test_classifier_billing_stops_the_poll_without_placeholders(self, mock_frappe, sample_settings):
		store = _Store()
		mock_frappe.get_all = MagicMock(side_effect=store.get_all)
		mock_frappe.get_doc = MagicMock(side_effect=store.get_doc)
		sample_settings.drive_integration_enabled = True
		sample_settings.drive_scan_folder_id = "folder"
		sample_settings.get_password = MagicMock(return_value="{}")
		mock_frappe.get_single = MagicMock(return_value=sample_settings)
		files = [{"id": f"d{i}", "name": f"{i}.pdf"} for i in range(3)]
		with (
			patch.object(drive, "_get_drive_service", return_value=MagicMock()),
			patch.object(drive, "_list_pdf_files", return_value=files),
			patch.object(drive, "_download_file", return_value=b"%PDF-1.4 c") as dl,
			patch(
				"erpocr_integration.tasks.classify_document.classify_document",
				side_effect=GeminiBillingError("no pay"),
			) as cls,
		):
			drive.poll_drive_scan_folder()
		assert cls.call_count == 1  # stopped after the first refusal
		assert dl.call_count == 1
		assert store.rows == {}

	def test_five_files_classifier_refusing_while_down_one_classify_no_placeholders(
		self, mock_frappe, sample_settings, _isolate
	):
		_isolate["billing_failure_since"] = datetime.datetime(2026, 10, 26, 8, 0)
		store = _Store()
		mock_frappe.get_all = MagicMock(side_effect=store.get_all)
		mock_frappe.get_doc = MagicMock(side_effect=store.get_doc)
		sample_settings.drive_integration_enabled = True
		sample_settings.drive_scan_folder_id = "folder"
		sample_settings.get_password = MagicMock(return_value="{}")
		mock_frappe.get_single = MagicMock(return_value=sample_settings)
		files = [{"id": f"d{i}", "name": f"{i}.pdf"} for i in range(5)]
		with (
			patch.object(drive, "_get_drive_service", return_value=MagicMock()),
			patch.object(drive, "_list_pdf_files", return_value=files),
			patch.object(drive, "_download_file", return_value=b"%PDF-1.4 c"),
			patch(
				"erpocr_integration.tasks.classify_document.classify_document",
				side_effect=GeminiBillingError("no pay"),
			) as cls,
			patch("time.sleep"),
		):
			drive.poll_drive_scan_folder()
		assert cls.call_count == 1
		assert store.rows == {}

	def test_probe_only_while_billing_is_down(self, mock_frappe, sample_settings, _isolate):
		_isolate["billing_failure_since"] = datetime.datetime(2026, 10, 26, 8, 0)
		sample_settings.drive_integration_enabled = True
		sample_settings.drive_scan_folder_id = "folder"
		sample_settings.get_password = MagicMock(return_value="{}")
		mock_frappe.get_single = MagicMock(return_value=sample_settings)
		files = [{"id": f"d{i}", "name": f"{i}.pdf"} for i in range(4)]
		with (
			patch.object(drive, "_get_drive_service", return_value=MagicMock()),
			patch.object(drive, "_list_pdf_files", return_value=files),
			patch.object(drive, "_process_scan_file", return_value=True) as proc,
			patch("time.sleep"),
		):
			drive.poll_drive_scan_folder()
		assert proc.call_count == 1

	def test_all_files_flow_when_billing_is_fine(self, mock_frappe, sample_settings):
		sample_settings.drive_integration_enabled = True
		sample_settings.drive_scan_folder_id = "folder"
		sample_settings.get_password = MagicMock(return_value="{}")
		mock_frappe.get_single = MagicMock(return_value=sample_settings)
		files = [{"id": f"d{i}", "name": f"{i}.pdf"} for i in range(4)]
		with (
			patch.object(drive, "_get_drive_service", return_value=MagicMock()),
			patch.object(drive, "_list_pdf_files", return_value=files),
			patch.object(drive, "_process_scan_file", return_value=True) as proc,
			patch("time.sleep"),
		):
			drive.poll_drive_scan_folder()
		assert proc.call_count == 4


class TestOtherProcessors:
	"""DN / fleet / statement processors give the attempt back too."""

	@pytest.mark.parametrize(
		("module", "func", "extract", "doctype", "kwargs"),
		[
			(
				"erpocr_integration.dn_api",
				"dn_gemini_process",
				"extract_delivery_note_data",
				"OCR Delivery Note",
				{"file_content": b"x", "filename": "f.pdf", "ocr_dn_name": "N1"},
			),
			(
				"erpocr_integration.fleet_api",
				"fleet_gemini_process",
				"extract_fleet_slip_data",
				"OCR Fleet Slip",
				{"file_content": b"x", "filename": "f.pdf", "ocr_fleet_name": "N1"},
			),
			(
				"erpocr_integration.statement_api",
				"statement_gemini_process",
				"extract_statement_data",
				"OCR Statement",
				{"file_content": b"x", "filename": "f.pdf", "ocr_statement_name": "N1"},
			),
		],
	)
	def test_billing_error_gives_back_attempt(self, mock_frappe, module, func, extract, doctype, kwargs):
		import importlib
		import inspect

		mod = importlib.import_module(module)
		fn = getattr(mod, func)
		params = inspect.signature(fn).parameters
		call_kwargs = {k: v for k, v in kwargs.items() if k in params}
		for name in params:
			if name not in call_kwargs and params[name].default is inspect._empty:
				call_kwargs[name] = "x"
		mock_frappe.set_user = MagicMock()
		with (
			patch(f"erpocr_integration.tasks.gemini_extract.{extract}", side_effect=GeminiBillingError("x")),
			patch.object(mod, "give_back_drive_attempt") as give_back,
		):
			fn(**call_kwargs)
		give_back.assert_called_once()
		assert give_back.call_args.args[0] == doctype

	def test_plain_error_does_not_give_back(self, mock_frappe):
		import erpocr_integration.dn_api as dn

		mock_frappe.set_user = MagicMock()
		with (
			patch(
				"erpocr_integration.tasks.gemini_extract.extract_delivery_note_data",
				side_effect=Exception("x"),
			),
			patch.object(dn, "give_back_drive_attempt") as give_back,
		):
			dn.dn_gemini_process(file_content=b"x", filename="f.pdf", ocr_dn_name="N1")
		give_back.assert_not_called()


class TestSettingsValidation:
	def _settings(self, text):
		from erpocr_integration.erpnext_ocr.doctype.ocr_settings.ocr_settings import OCRSettings

		s = OCRSettings()
		s.billing_alert_recipients = text
		return s

	def test_bad_address_is_refused_by_name(self, mock_frappe):
		import sys

		sys.modules["frappe.utils"].validate_email_address = lambda a: bool("@" in a)
		mock_frappe.throw = MagicMock(side_effect=Exception("refused"))
		with pytest.raises(Exception, match="refused"):
			self._settings("danell@x.com, not-an-address")._validate_billing_alert_recipients()
		assert "not-an-address" in mock_frappe.throw.call_args.args[0]
		mock_frappe.throw = MagicMock(side_effect=Exception)

	def test_good_or_blank_list_passes(self, mock_frappe):
		import sys

		sys.modules["frappe.utils"].validate_email_address = lambda a: bool("@" in a)
		self._settings("danell@x.com; a@b.co")._validate_billing_alert_recipients()
		self._settings("")._validate_billing_alert_recipients()
