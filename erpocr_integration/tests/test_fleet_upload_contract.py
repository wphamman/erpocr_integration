"""Tests for the P4 driver-shell fleet-slip upload contract.

Covers `erpocr_integration.fleet_api.upload_fleet_slip` and the matching-guard
changes that make it fail-safe and coexist with the Drive pipeline:

- Permission posture: guest denied, create-on-OCR-Fleet-Slip required, NEVER an
  invoice (OCR Import) surface.
- Idempotency: the R-B insert-and-catch template (same key twice → one slip,
  duplicate:true; unexpected unique re-raises).
- Fail-safe provider fork: a provider-less vehicle lands in Needs Review (blank
  posting_mode + blank supplier), never the Direct-Expense / invoice path.
- source_type discriminator set to the Shell constant (never client input).
- File validation: missing / wrong-type / oversize (own 2MB boundary) / bad
  magic bytes.
- T3 coexistence: the Drive path (fail_safe=False) keeps Direct-Expense
  fallback; a driver-confirmed vehicle is not clobbered by OCR re-matching.

Synthetic data only — no real driver names or plates.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.fleet_api import (
	MAX_FLEET_UPLOAD_SIZE,
	SOURCE_TYPE_SHELL,
	_apply_vehicle_config,
	_match_vehicle,
	_run_fleet_matching,
	fleet_gemini_process,
	upload_fleet_slip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# A REAL, decodable JPEG — the upload path now decode-verifies image uploads, so
# a header-only stub would be (correctly) rejected. Built with PIL (ships with
# Frappe) so the image tests exercise the real second gate.
def _encode_jpeg() -> bytes:
	from io import BytesIO

	from PIL import Image

	buf = BytesIO()
	Image.new("RGB", (4, 4), (120, 80, 200)).save(buf, format="JPEG")
	return buf.getvalue()


_JPEG = _encode_jpeg()

# A file with a VALID JPEG magic header but a truncated/garbage body: passes the
# magic-byte gate yet is undecodable — the class of file that used to 500 in PIL
# downstream. The decode-verify gate must reject it cleanly before any File.
_CORRUPT_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200

# A minimal valid PDF — slips may legitimately be PDFs, which PIL does not raster
# (the gate is image-only), so this must still pass validation untouched.
_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class _NS(SimpleNamespace):
	"""SimpleNamespace with .get() to mimic frappe._dict."""

	def get(self, key, default=None):
		return getattr(self, key, default)


def _settings(**overrides):
	defaults = dict(
		default_company="Test Company",
		fleet_default_supplier="Default Supplier",
		fleet_expense_account="5000 - Fuel Expense - TC",
	)
	defaults.update(overrides)
	return _NS(**defaults)


class _Slip:
	"""Mock OCR Fleet Slip — built by upload_fleet_slip via frappe.get_doc(dict),
	so it is constructed FROM the dict the contract passes (mirroring real
	Frappe). insert() optionally raises (idempotency-collision simulation);
	otherwise it records the call. Attribute assignment stays open so
	_apply_vehicle_config can set posting_mode/supplier/etc.
	"""

	def __init__(self, data=None, name="OCR-FS-00040", insert_error=None):
		for k, v in (data or {}).items():
			setattr(self, k, v)
		self.name = name
		self._insert_error = insert_error
		self.inserted = False

	def get(self, key, default=None):
		return getattr(self, key, default)

	def insert(self, ignore_permissions=False):
		if self._insert_error is not None:
			raise self._insert_error
		self.inserted = True


def _file_obj(filename="slip.jpg", content=_JPEG, size=None):
	"""A multipart file stand-in (werkzeug FileStorage shape)."""
	f = MagicMock()
	f.filename = filename
	f.tell.return_value = len(content) if size is None else size
	f.read.return_value = content
	return f


def _wire_upload(
	mock_frappe,
	*,
	file=None,
	settings=None,
	existing=None,
	vehicle=None,
	insert_error=None,
	fleet_vehicle_doctype=True,
):
	"""Wire the frappe mock for an upload_fleet_slip call.

	Returns a holder dict; after the call, holder["slip"] is the slip the
	contract built (constructed from the get_doc dict, like real Frappe), and
	holder["file_doc"] is the attached-File mock.
	"""
	file = file if file is not None else _file_obj()
	settings = settings if settings is not None else _settings()
	holder = {"slip": None, "file_doc": MagicMock()}

	mock_frappe.request = MagicMock()
	mock_frappe.request.files = {"file": file} if file is not False else {}
	mock_frappe.get_cached_doc.return_value = settings
	mock_frappe.db.exists.return_value = fleet_vehicle_doctype
	mock_frappe.db.get_value.return_value = vehicle

	def _get_doc(*args, **kwargs):
		if len(args) == 1 and isinstance(args[0], dict):
			dt = args[0].get("doctype")
			if dt == "OCR Fleet Slip":
				holder["slip"] = _Slip(dict(args[0]), insert_error=insert_error)
				return holder["slip"]
			if dt == "File":
				return holder["file_doc"]
		if len(args) >= 2 and args[0] == "OCR Fleet Slip":
			# Refetch only happens on the duplicate path. existing=None models
			# "no row carries the key" → the unexpected-unique re-raise branch.
			if existing is None:
				raise mock_frappe.DoesNotExistError("no row carries the key")
			return existing
		return MagicMock()

	mock_frappe.get_doc.side_effect = _get_doc
	return holder


# ---------------------------------------------------------------------------
# Permission guards
# ---------------------------------------------------------------------------


class TestUploadPermissionGuards:
	def test_guest_denied(self, mock_frappe):
		mock_frappe.session.user = "Guest"
		_wire_upload(mock_frappe)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_not_called()

	def test_no_create_permission_denied(self, mock_frappe):
		mock_frappe.has_permission.return_value = False
		_wire_upload(mock_frappe)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_not_called()

	def test_never_checks_ocr_import_create(self, mock_frappe):
		"""The contract must never gate on OCR Import — only OCR Fleet Slip create."""
		_wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1")
		checked = [c.args[0] for c in mock_frappe.has_permission.call_args_list if c.args]
		assert "OCR Import" not in checked
		assert "OCR Fleet Slip" in checked

	def test_plain_driver_role_can_upload(self, mock_frappe):
		"""D0 posture: a user holding ONLY the plain `Driver` role (NO doctype
		create perm — real drivers are never provisioned `OCR Fleet Driver`) can
		submit a slip. The endpoint gate itself makes Driver sufficient."""
		mock_frappe.session.user = "driver-only@starpops.test"
		mock_frappe.has_permission.return_value = False  # no OCR Fleet Slip create
		mock_frappe.get_roles.return_value = ["All", "Driver"]
		holder = _wire_upload(mock_frappe)

		result = upload_fleet_slip(client_request_id="k-driver-1")

		assert holder["slip"].inserted is True
		assert result["duplicate"] is False
		assert result["ocr_fleet_slip"] == "OCR-FS-00040"

	def test_plain_driver_idempotent_replay_still_works(self, mock_frappe):
		"""The Driver-widened gate must not bypass the idempotency contract: a
		replayed key from a plain-Driver caller returns the ORIGINAL slip with
		duplicate: true, no second enqueue."""
		mock_frappe.has_permission.return_value = False
		mock_frappe.get_roles.return_value = ["All", "Driver"]
		existing = _Slip({"status": "Pending", "client_request_id": "k-driver-dup"}, name="OCR-FS-00002")
		_wire_upload(
			mock_frappe,
			existing=existing,
			insert_error=mock_frappe.UniqueValidationError("unique"),
		)

		result = upload_fleet_slip(client_request_id="k-driver-dup")

		assert result["duplicate"] is True
		assert result["ocr_fleet_slip"] == "OCR-FS-00002"
		mock_frappe.enqueue.assert_not_called()

	def test_no_driver_and_no_ocr_role_rejected(self, mock_frappe):
		"""Negative posture: neither `Driver` nor any create-granting OCR role →
		still a permission error (the widening is Driver-scoped, not open)."""
		mock_frappe.has_permission.return_value = False
		mock_frappe.get_roles.return_value = ["All", "Employee"]
		_wire_upload(mock_frappe)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_not_called()

	def test_missing_client_request_id_rejected(self, mock_frappe):
		_wire_upload(mock_frappe)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="")
		mock_frappe.enqueue.assert_not_called()

	def test_whitespace_client_request_id_rejected(self, mock_frappe):
		_wire_upload(mock_frappe)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="   ")


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


class TestUploadFileValidation:
	def test_no_files_in_request(self, mock_frappe):
		_wire_upload(mock_frappe, file=False)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")

	def test_file_field_absent(self, mock_frappe):
		_wire_upload(mock_frappe)
		mock_frappe.request.files = {"other": _file_obj()}
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")

	def test_unsupported_type_rejected(self, mock_frappe):
		_wire_upload(mock_frappe, file=_file_obj(filename="slip.txt"))
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_not_called()

	def test_oversize_rejected(self, mock_frappe):
		"""> 2MB rejected by the contract's own boundary (tighter than 10MB)."""
		big = _file_obj(size=MAX_FLEET_UPLOAD_SIZE + 1)
		_wire_upload(mock_frappe, file=big)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_not_called()

	def test_at_size_limit_accepted(self, mock_frappe):
		ok = _file_obj(content=_JPEG, size=MAX_FLEET_UPLOAD_SIZE)
		_wire_upload(mock_frappe, file=ok)
		result = upload_fleet_slip(client_request_id="k1")
		assert result["duplicate"] is False

	def test_bad_magic_bytes_rejected(self, mock_frappe):
		bad = _file_obj(filename="slip.png", content=b"not-a-png-at-all")
		_wire_upload(mock_frappe, file=bad)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")

	def test_corrupt_but_magic_valid_image_rejected_before_file(self, mock_frappe):
		"""A JPEG with a valid magic header but an undecodable body passes the
		magic-byte gate yet must be rejected by the decode-verify gate — cleanly,
		before any File/slip is created (no 500, no orphan)."""
		corrupt = _file_obj(filename="slip.jpg", content=_CORRUPT_JPEG)
		_wire_upload(mock_frappe, file=corrupt)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		assert "couldn't be read" in mock_frappe.throw.call_args[0][0]
		# No slip and no File were built (validation precedes both).
		file_dicts = [
			c.args[0]
			for c in mock_frappe.get_doc.call_args_list
			if c.args and isinstance(c.args[0], dict) and c.args[0].get("doctype") == "File"
		]
		assert file_dicts == []
		mock_frappe.enqueue.assert_not_called()

	def test_pdf_slip_skips_decode_verify(self, mock_frappe):
		"""Slips may be PDFs; PIL does not raster a PDF, so the image-only gate
		must NOT touch it — a valid PDF still uploads (regression: the gate is
		additive and image-scoped, it does not reject legitimate PDF slips)."""
		pdf = _file_obj(filename="slip.pdf", content=_PDF)
		_wire_upload(mock_frappe, file=pdf)
		result = upload_fleet_slip(client_request_id="k1")
		assert result["duplicate"] is False

	def test_missing_default_company_rejected(self, mock_frappe):
		_wire_upload(mock_frappe, settings=_settings(default_company=""))
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")


# ---------------------------------------------------------------------------
# Happy path / shape
# ---------------------------------------------------------------------------


class TestUploadHappyPath:
	def test_creates_slip_with_shell_source_and_stamps(self, mock_frappe):
		mock_frappe.session.user = "driver@starpops.test"
		holder = _wire_upload(mock_frappe)
		result = upload_fleet_slip(client_request_id="k-abc")
		slip = holder["slip"]

		assert slip.source_type == SOURCE_TYPE_SHELL
		assert slip.source_type == "Gemini Shell Upload"
		assert slip.uploaded_by == "driver@starpops.test"
		assert slip.client_request_id == "k-abc"
		assert slip.status == "Pending"
		assert slip.company == "Test Company"
		assert slip.inserted is True
		assert result == {
			"ocr_fleet_slip": "OCR-FS-00040",
			"status": "Pending",
			"client_request_id": "k-abc",
			"duplicate": False,
		}

	def test_attaches_private_file(self, mock_frappe):
		_wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1")
		# The File doc built must be private and attached to the slip.
		file_dicts = [
			c.args[0]
			for c in mock_frappe.get_doc.call_args_list
			if c.args and isinstance(c.args[0], dict) and c.args[0].get("doctype") == "File"
		]
		assert len(file_dicts) == 1
		fd = file_dicts[0]
		assert fd["is_private"] == 1
		assert fd["attached_to_doctype"] == "OCR Fleet Slip"
		assert fd["attached_to_name"] == "OCR-FS-00040"

	def test_enqueues_extraction_on_long_queue(self, mock_frappe):
		_wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1")
		mock_frappe.enqueue.assert_called_once()
		_args, kwargs = mock_frappe.enqueue.call_args
		assert mock_frappe.enqueue.call_args.args[0] == "erpocr_integration.fleet_api.fleet_gemini_process"
		assert kwargs["queue"] == "long"
		assert kwargs["timeout"] == 600
		assert kwargs["ocr_fleet_name"] == "OCR-FS-00040"
		assert kwargs["mime_type"] == "image/jpeg"
		# Tied to the commit so the slip+File+job land atomically and the worker
		# never races the commit.
		assert kwargs["enqueue_after_commit"] is True

	def test_captured_at_tz_aware_normalized_to_site_naive(self, mock_frappe):
		"""A shell-shaped UTC 'Z' captured_at lands tz-NAIVE site-local — no 1292.

		The bug: get_datetime('…Z') returns a tz-AWARE datetime; assigning it to
		the naive MariaDB DATETIME column raises (1292) at insert(), OUTSIDE the
		parse try/except, so EVERY driver-shell slip 500'd.

		Mock-trap (cost real time in fleet P3.5): the conftest echo-mocks
		get_datetime, so a string/naive mock would HIDE this exact bug. This test
		patches a GENUINELY tz-aware datetime in (real get_datetime behaviour) so
		the tzinfo-stripping is actually exercised."""
		from datetime import datetime, timezone

		holder = _wire_upload(mock_frappe)
		# The shell always sends new Date().toISOString() — UTC with a 'Z'.
		aware_utc = datetime(2026, 6, 23, 8, 26, 0, 123456, tzinfo=timezone.utc)
		with (
			patch("frappe.utils.get_datetime", return_value=aware_utc),
			patch("frappe.utils.get_system_timezone", return_value="Africa/Johannesburg"),
		):
			upload_fleet_slip(client_request_id="k-tz", captured_at="2026-06-23T08:26:00.123456Z")

		stored = holder["slip"].captured_at
		# 08:26 UTC → 10:26 SAST (+02:00), tz stripped, microseconds zeroed —
		# exactly like now_datetime() produces.
		assert stored == datetime(2026, 6, 23, 10, 26, 0)
		assert stored.tzinfo is None
		assert stored.microsecond == 0

	def test_captured_at_naive_stored_unchanged(self, mock_frappe):
		"""An already-naive captured_at passes through (microseconds zeroed, no tz
		conversion) — the coexistence path stays correct."""
		from datetime import datetime

		holder = _wire_upload(mock_frappe)
		naive = datetime(2026, 6, 11, 8, 30, 0, 500000)
		with patch("frappe.utils.get_datetime", return_value=naive):
			upload_fleet_slip(client_request_id="k1", captured_at="2026-06-11T08:30:00.5")
		assert holder["slip"].captured_at == datetime(2026, 6, 11, 8, 30, 0)
		assert holder["slip"].captured_at.tzinfo is None

	def test_captured_at_none_not_set(self, mock_frappe):
		"""The Drive pipeline + offline-with-no-timestamp path: captured_at omitted
		→ never assigned (server-side creation timestamp governs)."""
		holder = _wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1")
		assert holder["slip"].get("captured_at") is None

	def test_captured_at_unparseable_dropped_not_fatal(self, mock_frappe):
		"""A malformed device timestamp is logged + dropped — it must NEVER block
		the recon upload (the slip still lands)."""
		holder = _wire_upload(mock_frappe)
		with patch("frappe.utils.get_datetime", side_effect=ValueError("bad")):
			result = upload_fleet_slip(client_request_id="k1", captured_at="not-a-date")
		assert holder["slip"].get("captured_at") is None
		assert holder["slip"].inserted is True
		assert result["duplicate"] is False

	def test_pre_commit_failure_leaves_no_committed_slip(self, mock_frappe):
		"""Atomicity (Codex FAIL-1 fix): slip + File + job land on ONE commit, the
		LAST step. A failure before it (modelled via the enqueue registration
		raising) never reaches the commit, so the request rolls back and an
		idempotent retry rebuilds a complete slip instead of replaying a half-built
		keyed row that retries could never repair."""
		_wire_upload(mock_frappe)
		mock_frappe.enqueue.side_effect = Exception("redis down")
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1")
		mock_frappe.db.commit.assert_not_called()

	def test_happy_path_commits_exactly_once(self, mock_frappe):
		"""The atomic unit is a single commit (was two: slip then File)."""
		_wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1")
		assert mock_frappe.db.commit.call_count == 1


# ---------------------------------------------------------------------------
# Vehicle resolution + fail-safe provider fork
# ---------------------------------------------------------------------------


class TestUploadVehicleFork:
	def test_confirmed_vehicle_with_provider_is_fleet_card(self, mock_frappe):
		vehicle = _NS(
			name="VEH-DIESEL-1",
			registration="AAA111",
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="Transport - TC",
		)
		holder = _wire_upload(mock_frappe, vehicle=vehicle)
		upload_fleet_slip(client_request_id="k1", fleet_vehicle="VEH-DIESEL-1")
		slip = holder["slip"]

		assert slip.fleet_vehicle == "VEH-DIESEL-1"
		assert slip.vehicle_match_status == "Confirmed"
		assert slip.posting_mode == "Fleet Card"
		assert slip.fleet_card_supplier == "WesBank"

	def test_confirmed_vehicle_without_provider_fails_safe(self, mock_frappe):
		"""FAIL-SAFE: provider missing → blank posting_mode + blank supplier.

		The slip cannot reach the invoice path (PI guard needs posting_mode ==
		'Direct Expense'); it lands in Needs Review for an OCR Manager.
		"""
		vehicle = _NS(
			name="VEH-NOPROV-1",
			registration="BBB222",
			custom_fleet_card_provider="",  # NOT maintained
			custom_fleet_control_account="",
			custom_cost_center="Transport - TC",
		)
		holder = _wire_upload(mock_frappe, vehicle=vehicle)
		upload_fleet_slip(client_request_id="k1", fleet_vehicle="VEH-NOPROV-1")
		slip = holder["slip"]

		assert slip.fleet_vehicle == "VEH-NOPROV-1"
		assert slip.vehicle_match_status == "Confirmed"
		# The fork must NOT silently route toward Direct Expense.
		assert slip.posting_mode == ""
		assert slip.fleet_card_supplier == ""
		assert slip.expense_account == ""

	def test_unknown_vehicle_rejected(self, mock_frappe):
		_wire_upload(mock_frappe, vehicle=None)  # get_value returns None
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1", fleet_vehicle="VEH-GHOST")
		mock_frappe.enqueue.assert_not_called()

	def test_fleet_vehicle_doctype_absent_rejected(self, mock_frappe):
		_wire_upload(mock_frappe, fleet_vehicle_doctype=False)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k1", fleet_vehicle="VEH-1")

	def test_registration_only_stored_raw(self, mock_frappe):
		"""No vehicle picked → raw plate stored; async matching resolves it later."""
		holder = _wire_upload(mock_frappe)
		upload_fleet_slip(client_request_id="k1", vehicle_registration=" cxx 579 l ")
		slip = holder["slip"]
		assert slip.vehicle_registration == "cxx 579 l"
		assert getattr(slip, "fleet_vehicle", None) is None


# ---------------------------------------------------------------------------
# Idempotency (R-B insert-and-catch)
# ---------------------------------------------------------------------------


class TestUploadIdempotency:
	def test_duplicate_returns_existing_no_second_enqueue(self, mock_frappe):
		existing = _Slip({"status": "Matched", "client_request_id": "dup-key"}, name="OCR-FS-00001")
		_wire_upload(
			mock_frappe,
			existing=existing,
			insert_error=mock_frappe.UniqueValidationError("unique"),
		)

		result = upload_fleet_slip(client_request_id="dup-key")

		assert result["duplicate"] is True
		assert result["ocr_fleet_slip"] == "OCR-FS-00001"
		assert result["status"] == "Matched"
		# Full rollback (REPEATABLE-READ correctness) + no second slip enqueued.
		mock_frappe.db.rollback.assert_called_once()
		mock_frappe.enqueue.assert_not_called()

	def test_duplicate_entry_error_also_caught(self, mock_frappe):
		existing = _Slip({"status": "Needs Review", "client_request_id": "k"}, name="OCR-FS-7")
		_wire_upload(
			mock_frappe,
			existing=existing,
			insert_error=mock_frappe.DuplicateEntryError("dup"),
		)

		result = upload_fleet_slip(client_request_id="k")
		assert result["duplicate"] is True
		assert result["ocr_fleet_slip"] == "OCR-FS-7"

	def test_unexpected_unique_violation_reraises(self, mock_frappe):
		"""A unique error with NO row carrying the key is a real error, not a replay.

		existing=None makes the refetch raise DoesNotExistError → the contract
		must re-raise the original violation rather than masquerade it as a replay.
		"""
		_wire_upload(
			mock_frappe,
			existing=None,
			insert_error=mock_frappe.UniqueValidationError("some other unique"),
		)
		with pytest.raises(Exception):
			upload_fleet_slip(client_request_id="k")
		mock_frappe.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# T3 coexistence — fail-safe threading + Drive path unchanged
# ---------------------------------------------------------------------------


class TestApplyVehicleConfigFailSafe:
	def test_fail_safe_provider_present_still_fleet_card(self):
		doc = SimpleNamespace()
		vehicle = _NS(
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100",
			custom_cost_center="Transport",
		)
		_apply_vehicle_config(doc, vehicle, _settings(), fail_safe=True)
		assert doc.posting_mode == "Fleet Card"
		assert doc.fleet_card_supplier == "WesBank"

	def test_fail_safe_no_provider_blanks_not_direct_expense(self):
		doc = SimpleNamespace()
		vehicle = _NS(
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, _settings(), fail_safe=True)
		assert doc.posting_mode == ""
		assert doc.fleet_card_supplier == ""
		assert doc.expense_account == ""

	def test_drive_default_no_provider_keeps_direct_expense(self):
		"""REGRESSION: Drive path (fail_safe=False) is unchanged — Direct Expense."""
		doc = SimpleNamespace()
		vehicle = _NS(
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, _settings())  # default fail_safe=False
		assert doc.posting_mode == "Direct Expense"
		assert doc.fleet_card_supplier == "Default Supplier"
		assert doc.expense_account == "5000 - Fuel Expense - TC"


class TestMatchVehicleFailSafeThreading:
	def test_match_threads_fail_safe_to_config(self, mock_frappe):
		"""A provider-less exact match under fail_safe=True → blank posting_mode."""
		doc = SimpleNamespace(vehicle_registration="BBB222")
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-NOPROV",
			registration="BBB222",
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)
		_match_vehicle(doc, _settings(), fail_safe=True)
		assert doc.fleet_vehicle == "VEH-NOPROV"
		assert doc.posting_mode == ""  # fail-safe, not Direct Expense

	def test_run_matching_default_is_not_fail_safe(self, mock_frappe):
		"""_run_fleet_matching default keeps Drive behaviour (Direct Expense)."""
		doc = SimpleNamespace(vehicle_registration="BBB222")
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-NOPROV",
			registration="BBB222",
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)
		_run_fleet_matching(doc, _settings())
		assert doc.posting_mode == "Direct Expense"


class TestExtractionGuard:
	"""fleet_gemini_process must skip re-matching a driver-confirmed vehicle and
	apply fail-safe matching for shell-sourced slips, while leaving Drive slips
	(fail_safe=False) untouched."""

	def _run(self, mock_frappe, slip):
		mock_frappe.get_cached_doc.return_value = _settings()
		mock_frappe.get_doc.return_value = slip
		with (
			patch("erpocr_integration.fleet_api._populate_ocr_fleet") as pop,
			patch("erpocr_integration.fleet_api._run_fleet_matching") as run_match,
			patch(
				"erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data",
				return_value={"header_fields": {}},
			),
		):
			pop.return_value = None
			fleet_gemini_process(b"x", "slip.jpg", "OCR-FS-1", mime_type="image/jpeg")
		return run_match

	def test_confirmed_vehicle_skips_rematching(self, mock_frappe):
		slip = MagicMock()
		slip.vehicle_match_status = "Confirmed"
		slip.fleet_vehicle = "VEH-1"
		slip.source_type = "Gemini Shell Upload"
		slip.drive_file_id = None
		run_match = self._run(mock_frappe, slip)
		run_match.assert_not_called()

	def test_shell_source_runs_fail_safe_matching(self, mock_frappe):
		slip = MagicMock()
		slip.vehicle_match_status = "Unmatched"
		slip.fleet_vehicle = None
		slip.source_type = "Gemini Shell Upload"
		slip.drive_file_id = None
		run_match = self._run(mock_frappe, slip)
		run_match.assert_called_once()
		assert run_match.call_args.kwargs.get("fail_safe") is True

	def test_drive_source_runs_without_fail_safe(self, mock_frappe):
		"""REGRESSION: a Drive slip matches with fail_safe=False (unchanged)."""
		slip = MagicMock()
		slip.vehicle_match_status = "Unmatched"
		slip.fleet_vehicle = None
		slip.source_type = "Gemini Drive Scan"
		slip.drive_file_id = None
		run_match = self._run(mock_frappe, slip)
		run_match.assert_called_once()
		assert run_match.call_args.kwargs.get("fail_safe") is False
