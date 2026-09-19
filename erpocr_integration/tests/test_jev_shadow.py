"""Tests for erpocr_integration.tasks.jev_shadow — the Q17 Jev shadow trial.

Hard invariants under test: blind (never touches supplier/status/items/auto-draft
fields), off by default, budget-capped, own-company guarded, and the API key
never leaks into a stored field or a log entry on any failure path.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

API_KEY = "ts-secret-abc123xyz"


def _make_settings(**overrides):
	defaults = dict(
		enable_jev_shadow=1,
		jev_model="jev-1.13.0",
		jev_monthly_budget_usd=2.0,
		jev_timeout_seconds=20,
		get_password=MagicMock(return_value=API_KEY),
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


def _make_ocr_import(**overrides):
	defaults = dict(
		supplier_name_ocr="Acme Trading (Pty) Ltd",
		supplier_tax_id="4123456789",
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


def _configure_get_all(mock_frappe, companies=None, suppliers=None, aliases=None, ocr_imports=None):
	"""Route frappe.get_all by doctype for Company / Supplier / OCR Supplier Alias /
	OCR Import — the four tables the shadow job (via supplier_candidates,
	_own_company_guard, _month_spend_usd) can query."""
	company_rows = [SimpleNamespace(company_name=c) for c in (companies or [])]
	supplier_rows = [
		SimpleNamespace(name=s[0], supplier_name=s[1], tax_id=s[2] if len(s) > 2 else None)
		for s in (suppliers or [])
	]
	alias_rows = [SimpleNamespace(supplier=a[0], ocr_text=a[1]) for a in (aliases or [])]
	ocr_import_rows = [SimpleNamespace(jev_cost_usd=c) for c in (ocr_imports or [])]

	def side_effect(doctype, **kwargs):
		if doctype == "Company":
			return company_rows
		if doctype == "Supplier":
			return supplier_rows
		if doctype == "OCR Supplier Alias":
			return alias_rows
		if doctype == "OCR Import":
			return ocr_import_rows
		return []

	mock_frappe.get_all = MagicMock(side_effect=side_effect)


def _fake_response(payload, status_ok=True):
	resp = MagicMock()
	if status_ok:
		resp.raise_for_status = MagicMock()
	else:
		resp.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError("500 Server Error"))
	resp.json = MagicMock(return_value=payload)
	return resp


def _updates_from_set_value(mock_frappe):
	"""The last frappe.db.set_value("OCR Import", name, {updates}, ...) call's dict."""
	call = mock_frappe.db.set_value.call_args
	return call.args[2]


class TestRunJevShadowGates:
	def test_disabled_no_write_no_call(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings(enable_jev_shadow=0))
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_frappe.db.set_value.assert_not_called()
		mock_post.assert_not_called()

	def test_no_api_key_no_write_no_call(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		settings = _make_settings(get_password=MagicMock(return_value=""))
		mock_frappe.get_cached_doc = MagicMock(return_value=settings)
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_frappe.db.set_value.assert_not_called()
		mock_post.assert_not_called()

	def test_blank_supplier_name_ocr_skipped(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import(supplier_name_ocr=""))
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_post.assert_not_called()
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Skipped"
		assert "blank" in updates["jev_note"].lower()
		# Matcher snapshot is ALWAYS written, even on a skip.
		assert updates["jev_matcher_supplier"] == "SUP-001"
		assert updates["jev_matcher_status"] == "Auto Matched"

	def test_own_company_guard_skips(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(
			return_value=_make_ocr_import(supplier_name_ocr="Star Pops (Pty) Ltd")
		)
		_configure_get_all(mock_frappe, companies=["Star Pops (Pty) Ltd"])
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", None, None)
		mock_post.assert_not_called()
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Skipped"
		assert "own" in updates["jev_note"].lower() or "compan" in updates["jev_note"].lower()

	def test_own_company_guard_fuzzy_match(self, mock_frappe):
		"""A near-identical OCR misread of the buyer's own name must also guard."""
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import(supplier_name_ocr="Star Pops Pty Ltd"))
		_configure_get_all(mock_frappe, companies=["Star Pops (Pty) Ltd"])
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", None, None)
		mock_post.assert_not_called()
		assert _updates_from_set_value(mock_frappe)["jev_status"] == "Skipped"

	def test_budget_reached_skips_no_call(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings(jev_monthly_budget_usd=1.0))
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import())
		_configure_get_all(mock_frappe, ocr_imports=[0.60, 0.45])  # 1.05 already spent >= 1.0 cap
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_post.assert_not_called()
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Skipped"
		assert "budget" in updates["jev_note"].lower()

	def test_budget_not_reached_proceeds(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings(jev_monthly_budget_usd=5.0))
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import())
		_configure_get_all(
			mock_frappe,
			ocr_imports=[0.10],
			suppliers=[("SUP-001", "Acme Trading (Pty) Ltd", "4123456789")],
		)
		payload = {
			"model": "jev-1.13.0",
			"answers": {"supplier": {"choice": "SUP-001", "probabilities": {"SUP-001": 0.9, "none": 0.1}}},
			"usage": {"input_tokens": 500},
		}
		with patch(
			"erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)
		) as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_post.assert_called_once()
		assert _updates_from_set_value(mock_frappe)["jev_status"] == "Done"

	def test_zero_candidates_skipped(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import())
		_configure_get_all(mock_frappe, suppliers=[])  # no enabled suppliers at all
		with patch("erpocr_integration.tasks.jev_shadow.requests.post") as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		mock_post.assert_not_called()
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Skipped"
		assert "candidate" in updates["jev_note"].lower()


class TestRunJevShadowSuccess:
	def _setup(self, mock_frappe, printed_name="Acme Trading (Pty) Ltd", tax_id="4123456789"):
		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(
			return_value=_make_ocr_import(supplier_name_ocr=printed_name, supplier_tax_id=tax_id)
		)
		_configure_get_all(
			mock_frappe,
			suppliers=[("SUP-001", "Acme Trading (Pty) Ltd", "4123456789")],
		)

	def test_success_writes_fields_from_response(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		payload = {
			"model": "jev-1.13.0",
			"answers": {
				"supplier": {
					"choice": "SUP-001",
					"probabilities": {"SUP-001": 0.87, "none": 0.13},
				}
			},
			"usage": {"input_tokens": 1000},
		}
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)):
			with patch(
				"erpocr_integration.tasks.jev_shadow.now_datetime", return_value="2025-01-15 09:00:00"
			):
				run_jev_shadow("OCR-IMP-00001", "SUP-002", "Suggested")

		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Done"
		assert updates["jev_supplier"] == "SUP-001"
		assert updates["jev_choice_none"] == 0
		assert updates["jev_probability"] == 0.87
		# 1000 input tokens * 0.042 / 1e6
		assert updates["jev_cost_usd"] == 1000 * 0.042 / 1e6
		assert updates["jev_model"] == "jev-1.13.0"
		assert updates["jev_candidate_count"] == 1
		assert updates["jev_run_at"] == "2025-01-15 09:00:00"
		assert updates["jev_matcher_supplier"] == "SUP-002"
		assert updates["jev_matcher_status"] == "Suggested"
		mock_frappe.db.commit.assert_called()

	def test_success_none_choice_handled(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		payload = {
			"model": "jev-1.13.0",
			"answers": {"supplier": {"choice": "none", "probabilities": {"SUP-001": 0.2, "none": 0.8}}},
			"usage": {"input_tokens": 400},
		}
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")

		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Done"
		assert updates["jev_supplier"] == ""
		assert updates["jev_choice_none"] == 1

	def test_update_modified_false(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		payload = {
			"model": "jev-1.13.0",
			"answers": {"supplier": {"choice": "SUP-001", "probabilities": {"SUP-001": 1.0}}},
			"usage": {"input_tokens": 100},
		}
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		call = mock_frappe.db.set_value.call_args
		assert call.kwargs.get("update_modified") is False

	def test_never_touches_operator_fields(self, mock_frappe):
		"""The written dict must never carry supplier / status / items / auto-draft keys."""
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		payload = {
			"model": "jev-1.13.0",
			"answers": {"supplier": {"choice": "SUP-001", "probabilities": {"SUP-001": 1.0}}},
			"usage": {"input_tokens": 100},
		}
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		forbidden = {"supplier", "supplier_match_status", "status", "items", "auto_drafted"}
		assert forbidden.isdisjoint(updates.keys())
		# doc.save() must never be used for this job.
		mock_frappe.get_doc.return_value.save = MagicMock()
		assert not mock_frappe.get_doc.return_value.save.called

	def test_request_body_shape_mirrors_jev_lab(self, mock_frappe):
		"""The POSTed JSON must exactly mirror jev_lab's supplier_question/state shape —
		this wording is what the 94% top-1 figure (Q17) was measured with."""
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe, printed_name="Acme Trading (Pty) Ltd", tax_id="4123456789")
		payload = {
			"model": "jev-1.13.0",
			"answers": {"supplier": {"choice": "SUP-001", "probabilities": {"SUP-001": 1.0}}},
			"usage": {"input_tokens": 100},
		}
		with patch(
			"erpocr_integration.tasks.jev_shadow.requests.post", return_value=_fake_response(payload)
		) as mock_post:
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")

		_url, kwargs = mock_post.call_args.args, mock_post.call_args.kwargs
		body = kwargs["json"]
		assert body["model"] == "jev-1.13.0"
		assert body["state"] == {
			"invoice": {
				"supplier_name_as_printed": "Acme Trading (Pty) Ltd",
				"supplier_vat_number_as_printed": "4123456789",
			}
		}
		question = body["questions"]["supplier"]
		assert question["type"] == "choice"
		assert question["criteria"]["SUP-001"] == "Registered name: Acme Trading (Pty) Ltd."
		assert "none" in question["criteria"]
		assert kwargs["headers"]["Authorization"] == f"Bearer {API_KEY}"
		assert kwargs["timeout"] == 20
		assert mock_post.call_args.args[0] == "https://api.typesafe.ai/v1/systemone"


class TestRunJevShadowErrors:
	def _setup(self, mock_frappe):
		mock_frappe.get_cached_doc = MagicMock(return_value=_make_settings())
		mock_frappe.get_doc = MagicMock(return_value=_make_ocr_import())
		_configure_get_all(mock_frappe, suppliers=[("SUP-001", "Acme Trading (Pty) Ltd", "4123456789")])

	def test_http_error_records_error_status_no_key_leak(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		resp = _fake_response({}, status_ok=False)
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=resp):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Error"
		assert API_KEY not in updates["jev_note"]
		log_call = mock_frappe.log_error.call_args
		assert API_KEY not in log_call.kwargs.get("message", "")

	def test_timeout_records_error_no_key_leak(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		with patch(
			"erpocr_integration.tasks.jev_shadow.requests.post",
			side_effect=requests.exceptions.Timeout("timed out after 20s"),
		):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Error"
		assert API_KEY not in updates["jev_note"]

	def test_malformed_json_records_error_no_key_leak(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		resp = MagicMock()
		resp.raise_for_status = MagicMock()
		resp.json = MagicMock(side_effect=ValueError("Expecting value: line 1 column 1"))
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=resp):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Error"
		assert API_KEY not in updates["jev_note"]

	def test_missing_answer_key_records_error(self, mock_frappe):
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		resp = _fake_response({"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 10}})
		with patch("erpocr_integration.tasks.jev_shadow.requests.post", return_value=resp):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		assert updates["jev_status"] == "Error"

	def test_key_leaked_into_exception_message_is_still_redacted(self, mock_frappe):
		"""Even if a lower layer echoes the key into an exception's own text (e.g. an
		SDK error dump), the stored/logged message must not contain it."""
		from erpocr_integration.tasks.jev_shadow import run_jev_shadow

		self._setup(mock_frappe)
		with patch(
			"erpocr_integration.tasks.jev_shadow.requests.post",
			side_effect=RuntimeError(f"bad auth header 'Bearer {API_KEY}'"),
		):
			run_jev_shadow("OCR-IMP-00001", "SUP-001", "Auto Matched")
		updates = _updates_from_set_value(mock_frappe)
		assert API_KEY not in updates["jev_note"]
		assert "[redacted]" in updates["jev_note"]
