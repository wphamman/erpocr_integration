# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""Jev shadow trial (Q17, v1.12.0).

Records TypeSafe Jev's supplier pick ALONGSIDE today's matcher's pick on every
extraction, for later comparison — Willie: "I would like to test jev, but I
don't want to replace what we have until we know jev is more accurate or the
same accuracy for much cheaper." See docs/architecture/OPEN-QUESTIONS.md Q17
for the full ruling.

Hard invariants (ruled, not negotiable without a new Q17 decision):
  - Blind: OCR Manager never sees these fields — HIDDEN on the form (not just
    permlevel 1: on the prod-copy bench 2 of 3 OCR Managers also hold System
    Manager, so permlevel alone would not keep the trial blind there; see
    ocr_import.json — the Jev section break, its column breaks, and every
    jev_* field all carry `hidden: 1` AND `permlevel: 1` AND (fields)
    `read_only: 1`). The data stays readable via API/DB for scoring.
  - Off by default; opt-in via OCR Settings.enable_jev_shadow.
  - Never writes `supplier`, `supplier_match_status`, `status`, `items`, or
    any auto-draft field. Writes ONLY its own jev_* fields, via
    frappe.db.set_value(update_modified=False) — never doc.save().
  - A Jev failure or timeout is recorded and swallowed — it must never affect
    matching, auto-draft, or extraction. The whole job must NEVER raise
    (Terra/Grok review, v1.12.0) — see run_jev_shadow's outer wrapper.
  - The API key and request headers must NEVER appear in a stored field or a
    log entry, INCLUDING via an uncaught exception's traceback/frame-locals
    dump (RQ's own handler calls frappe.get_traceback(with_context=True) on
    anything that escapes a job, and api_key is not on frappe's header-
    redaction list — a traceback dump is as real a leak vector as a bad
    string). Two independent defences: (1) run_jev_shadow's own frame never
    binds the key at all — only the small `_typesafe_post`/`_has_api_key`
    helpers ever call settings.get_password("typesafe_api_key"), and their
    frames are gone by the time control returns; (2) the outer wrapper never
    calls frappe.get_traceback() or logs an exception object/str(exc) — only
    a fixed, static message.
"""

from difflib import SequenceMatcher

import frappe
import requests
from frappe.utils import flt, now_datetime

from erpocr_integration.tasks.matching import _jev_key, supplier_candidates

# Wire format confirmed against the TypeSafe SDK source (jev_lab's vendored
# .venv, read-only reference — see kickoff §3): POST {base_url}/v1/systemone,
# body {"state", "model", "questions"}, header Authorization: Bearer <key>.
# `normalize_questions` (typesafe_sdk._core.questions) does NOT transform the
# question dict — it only validates that a "choice"/"score" question carries
# "criteria" — so the plain dict shape below is sent as-is.
_TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"

# USD per million INPUT tokens; output is free (jev_lab PRICE_PER_MTOK, 2026-09-19).
_PRICE_PER_MTOK = 0.042

_NONE = "none"

# Own-company guard threshold (kickoff §4d step 3): OCR sometimes reads the
# buyer's letterhead instead of the supplier's.
_OWN_COMPANY_RATIO = 0.9

_MAX_NOTE_LENGTH = 500


class _JevRequestError(Exception):
	"""Raised only by _typesafe_post, already carrying a message that has been
	sanitized (API key stripped) and length-capped. Safe to log/store as-is."""


def _supplier_question(candidates: list[dict]) -> dict:
	"""Mirror jev_lab's `supplier_question` (evals/eval1_erpocr/jevlab/jev.py)
	EXACTLY — the wording is what Q17's 94% top-1 figure was measured with.
	Do not reword without re-running the eval."""
	criteria = {}
	for c in candidates:
		parts = [f"Registered name: {c['supplier_name'] or c['name']}."]
		if c["aliases"]:
			parts.append("Has previously appeared on invoices as: " + "; ".join(c["aliases"][:5]))
		criteria[c["name"]] = " ".join(parts)
	criteria[_NONE] = (
		"The company that issued this invoice is none of the listed suppliers (a supplier we have "
		"not dealt with before, or a different company with a similar name)."
	)
	return {
		"type": "choice",
		"instructions": {
			"question": "Which of our existing supplier records is the company that issued this invoice?",
			"how": "Compare `invoice.supplier_name_as_printed` (read by OCR, may contain typos, "
			"trading names or a branch name) with each supplier's registered name and the names "
			"it has previously appeared under.",
		},
		"criteria": criteria,
	}


def _supplier_state(supplier_name_ocr: str, supplier_tax_id: str | None) -> dict:
	"""Mirror jev_lab's `supplier_state` (evals/eval1_erpocr/jevlab/jev.py)."""
	return {
		"invoice": {
			"supplier_name_as_printed": supplier_name_ocr,
			"supplier_vat_number_as_printed": supplier_tax_id or None,
		}
	}


def _own_company_guard(printed_name: str) -> bool:
	"""True when `printed_name` looks like one of OUR OWN companies rather than
	a supplier — OCR sometimes reads the buyer's letterhead. Exact `_jev_key`
	match, or SequenceMatcher ratio >= 0.9, against any `Company.company_name`."""
	want = _jev_key(printed_name)
	if not want:
		return False
	for row in frappe.get_all(
		"Company", fields=["company_name"], limit_page_length=0, ignore_permissions=True
	):
		company_key = _jev_key(row.company_name)
		if not company_key:
			continue
		if company_key == want or SequenceMatcher(None, want, company_key).ratio() >= _OWN_COMPANY_RATIO:
			return True
	return False


def _month_spend_usd() -> float:
	"""Sum of `jev_cost_usd` already spent this calendar month, across all OCR
	Imports. `flt(None)` -> 0.0, so a Done record with unknown cost (missing
	`usage` in the response — see run_jev_shadow) contributes 0 to the sum:
	an honest "we don't know", not a false "this call was free", but it does
	mean the budget check can under-count when responses are missing usage.

	Month boundary is a plain string slice of frappe.utils.today()
	("YYYY-MM-DD" -> "YYYY-MM-01") rather than frappe.utils.get_first_day —
	conftest's mock doesn't cover that function, and CLAUDE.md's standing
	gotcha is to never lean on an unfamiliar framework function the wholesale
	mock would silently let through."""
	month_start = f"{frappe.utils.today()[:7]}-01"
	rows = frappe.get_all(
		"OCR Import",
		filters={"jev_run_at": [">=", month_start]},
		fields=["jev_cost_usd"],
		limit_page_length=0,
		ignore_permissions=True,
	)
	return sum(flt(r.jev_cost_usd) for r in rows)


def _has_api_key(settings) -> bool:
	"""Read-and-discard existence check — returns a bool only, never the key
	itself, so the key's lifetime here is a single expression."""
	return bool(settings.get_password("typesafe_api_key"))


def _typesafe_post(settings, body: dict, timeout: float) -> dict:
	"""Make the actual HTTP call. `api_key` lives ONLY in this frame, for the
	shortest possible window: read here, used here in the header, gone when
	this function returns. Any failure (HTTP error, timeout, connection error,
	malformed JSON) is caught HERE — while the key is still in scope to redact
	against — and re-raised as `_JevRequestError` with an already-sanitized,
	length-capped message. The caller (run_jev_shadow) never sees `api_key`
	and never needs to sanitize against it again."""
	api_key = settings.get_password("typesafe_api_key")
	try:
		response = requests.post(
			_TYPESAFE_URL,
			json=body,
			headers={"Authorization": f"Bearer {api_key}"},
			timeout=timeout,
		)
		response.raise_for_status()
		return response.json()
	except Exception as exc:
		text = f"{type(exc).__name__}: {exc}"
		if api_key:
			text = text.replace(api_key, "[redacted]")
		raise _JevRequestError(text[:_MAX_NOTE_LENGTH]) from None


def _run_jev_shadow_inner(
	ocr_import_name: str, matcher_supplier: str | None, matcher_status: str | None
) -> None:
	"""The real logic — see run_jev_shadow for the outer safety wrapper."""
	frappe.set_user("Administrator")

	settings = frappe.get_cached_doc("OCR Settings")
	if not getattr(settings, "enable_jev_shadow", 0):
		return
	if not _has_api_key(settings):
		return

	ocr_import = frappe.get_doc("OCR Import", ocr_import_name)

	updates = {
		"jev_matcher_supplier": matcher_supplier or "",
		"jev_matcher_status": matcher_status or "",
	}

	def _finish(extra: dict) -> None:
		updates.update(extra)
		frappe.db.set_value("OCR Import", ocr_import_name, updates, update_modified=False)
		frappe.db.commit()  # nosemgrep

	printed_name = (ocr_import.supplier_name_ocr or "").strip()
	if not printed_name:
		_finish({"jev_status": "Skipped", "jev_note": "supplier_name_ocr is blank"})
		return

	# Budget <= 0 means "make no calls at all" (Terra/Grok review) — a blank or
	# zero setting is far more likely an unconfigured field than an intentional
	# unlimited-spend authorization.
	budget = flt(getattr(settings, "jev_monthly_budget_usd", 0))
	if budget <= 0:
		_finish(
			{
				"jev_status": "Skipped",
				"jev_note": "Jev monthly budget is not set (<= 0) — shadow trial makes no calls",
			}
		)
		return

	# SOFT cap (Terra/Grok review): this is a read-then-act check with no lock,
	# so concurrent shadow jobs can each pass it before any of their cost is
	# committed. Worst realistic overshoot is a handful of extra calls at
	# ~US$0.0002 each — accepted, not worth a lock on a diagnostic trial.
	spent = _month_spend_usd()
	if spent >= budget:
		_finish(
			{
				"jev_status": "Skipped",
				"jev_note": f"Monthly Jev budget reached (${spent:.4f} spent >= ${budget:.2f} cap)",
			}
		)
		return

	if _own_company_guard(printed_name):
		_finish(
			{
				"jev_status": "Skipped",
				"jev_note": f"Printed name '{printed_name}' matches one of our own companies",
			}
		)
		return

	candidates = supplier_candidates(printed_name, ocr_import.supplier_tax_id)
	if not candidates:
		_finish({"jev_status": "Skipped", "jev_note": "No enabled Supplier candidates"})
		return

	model = getattr(settings, "jev_model", None) or "jev-1.13.0"
	timeout = getattr(settings, "jev_timeout_seconds", None) or 20

	state = _supplier_state(printed_name, ocr_import.supplier_tax_id)
	question = _supplier_question(candidates)
	body = {"state": state, "model": model, "questions": {"supplier": question}}

	try:
		payload = _typesafe_post(settings, body, timeout)
	except _JevRequestError as exc:
		# Message is ALREADY sanitized + capped by _typesafe_post — api_key is
		# not in scope in this frame at all.
		note = str(exc)
		try:
			frappe.log_error(title="Jev Shadow Failed", message=f"OCR Import {ocr_import_name}: {note}")
		except Exception:
			pass
		_finish({"jev_status": "Error", "jev_note": note})
		return

	try:
		answer = payload["answers"]["supplier"]
		choice = answer["choice"]
		probabilities = answer.get("probabilities") or {}
		probability = max((flt(p) for p in probabilities.values()), default=0.0)
		usage = payload.get("usage")
		input_tokens = usage.get("input_tokens") if usage else None
		if input_tokens is None:
			# Missing usage (Terra/Grok review): still Done — we DID get a
			# usable choice — but cost is genuinely UNKNOWN, not free. Leave
			# jev_cost_usd blank rather than 0 so the budget sum's under-count
			# (see _month_spend_usd) is at least auditable via jev_note.
			cost_usd = None
			note = "Done; usage missing from response — cost unknown"
		else:
			cost_usd = flt(input_tokens) * _PRICE_PER_MTOK / 1e6
			note = ""
		is_none = choice == _NONE

		_finish(
			{
				"jev_status": "Done",
				"jev_supplier": "" if is_none else choice,
				"jev_choice_none": 1 if is_none else 0,
				"jev_probability": probability,
				"jev_cost_usd": cost_usd,
				"jev_model": payload.get("model") or model,
				"jev_candidate_count": len(candidates),
				"jev_run_at": now_datetime(),
				"jev_note": note,
			}
		)
	except Exception as exc:
		# payload came back with a 2xx status and valid JSON, but a key we
		# expect is missing/mistyped — api_key was never in scope in this
		# frame, so str(exc) here cannot carry it.
		note = str(exc)[:_MAX_NOTE_LENGTH]
		try:
			frappe.log_error(title="Jev Shadow Failed", message=f"OCR Import {ocr_import_name}: {note}")
		except Exception:
			pass
		_finish({"jev_status": "Error", "jev_note": note})


def run_jev_shadow(ocr_import_name: str, matcher_supplier: str | None, matcher_status: str | None) -> None:
	"""Ask TypeSafe Jev to pick a supplier for `ocr_import_name` and record the
	answer beside today's matcher's pick. See module docstring for invariants.

	Args:
	    ocr_import_name: the OCR Import to annotate.
	    matcher_supplier / matcher_status: OUR matcher's pick, snapshotted by the
	        caller BEFORE auto-draft runs (gemini_process) — the fixed comparison
	        point for this trial. Always written, regardless of what follows.

	This function itself does nothing but call `_run_jev_shadow_inner` inside a
	try/except — see the module docstring's "must NEVER raise" invariant and
	`_record_unexpected_failure` for why.
	"""
	try:
		_run_jev_shadow_inner(ocr_import_name, matcher_supplier, matcher_status)
	except Exception:
		_record_unexpected_failure(ocr_import_name)


def _record_unexpected_failure(ocr_import_name: str) -> None:
	"""Last-resort backstop (Terra/Grok review, v1.12.0). Every ANTICIPATED
	failure inside `_run_jev_shadow_inner` is already caught close to its
	source and turned into a sanitized `jev_status = Error` write. This
	function only runs for an UNANTICIPATED exception — a bad OCR Settings
	doctype, a DB hiccup inside `_finish` itself, a broken `frappe.get_doc`,
	etc. Such an exception must NEVER be allowed to propagate out of
	`run_jev_shadow`: RQ's own uncaught-exception handler calls
	`frappe.log_error(..., frappe.get_traceback(with_context=True))`, which
	dumps LOCAL VARIABLES for every frame in the traceback — `api_key` is not
	on frappe's header-redaction list, so an escaped exception is a live
	key-leak vector on its own, independent of anything this module does with
	its own logging. We therefore never call `frappe.get_traceback()` here and
	never pass the caught exception object (or even `str(exc)`, which could
	carry text from a frame we don't control) anywhere in this function — only
	a fixed, static string, so nothing here can possibly carry the key
	regardless of which frame the original exception came from.

	Best-effort on both fronts (per the review, in this order): first attempt
	to record the Error status on the OCR Import; then attempt to write the
	Error Log entry. Either or both may fail (e.g. the DB connection itself is
	the problem) — silently swallowed, because at this point there is nothing
	safe left to do but return.
	"""
	try:
		frappe.db.set_value(
			"OCR Import",
			ocr_import_name,
			{"jev_status": "Error", "jev_note": "Unhandled exception (see Error Log)"},
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep
	except Exception:
		pass
	try:
		frappe.log_error(
			title="Jev Shadow Failed",
			message=(
				f"Unhandled exception in run_jev_shadow for {ocr_import_name} "
				"(message withheld — see the job/queue logs, never the API key)."
			),
		)
	except Exception:
		pass
