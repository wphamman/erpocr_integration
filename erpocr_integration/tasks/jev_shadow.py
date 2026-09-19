# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""Jev shadow trial (Q17, v1.12.0).

Records TypeSafe Jev's supplier pick ALONGSIDE today's matcher's pick on every
extraction, for later comparison — Willie: "I would like to test jev, but I
don't want to replace what we have until we know jev is more accurate or the
same accuracy for much cheaper." See docs/architecture/OPEN-QUESTIONS.md Q17
for the full ruling.

Hard invariants (ruled, not negotiable without a new Q17 decision):
  - Blind: OCR Manager never sees these fields (permlevel 1, System Manager
    read only on OCR Import — see ocr_import.json).
  - Off by default; opt-in via OCR Settings.enable_jev_shadow.
  - Never writes `supplier`, `supplier_match_status`, `status`, `items`, or
    any auto-draft field. Writes ONLY its own jev_* fields, via
    frappe.db.set_value(update_modified=False) — never doc.save().
  - A Jev failure or timeout is recorded and swallowed — it must never affect
    matching, auto-draft, or extraction.
  - The API key and request headers must NEVER appear in a stored field or a
    log entry.
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
	Imports. Month boundary is a plain string slice of frappe.utils.today()
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


def _sanitize(message: str, api_key: str | None) -> str:
	"""Strip the API key (if it somehow ended up in an exception string, e.g. an
	SDK error echoing a bad request) and cap length before it's ever stored or
	logged."""
	text = message or ""
	if api_key:
		text = text.replace(api_key, "[redacted]")
	return text[:_MAX_NOTE_LENGTH]


def run_jev_shadow(ocr_import_name: str, matcher_supplier: str | None, matcher_status: str | None) -> None:
	"""Ask TypeSafe Jev to pick a supplier for `ocr_import_name` and record the
	answer beside today's matcher's pick. See module docstring for invariants.

	Args:
	    ocr_import_name: the OCR Import to annotate.
	    matcher_supplier / matcher_status: OUR matcher's pick, snapshotted by the
	        caller BEFORE auto-draft runs (gemini_process) — the fixed comparison
	        point for this trial. Always written, regardless of what follows.
	"""
	frappe.set_user("Administrator")

	settings = frappe.get_cached_doc("OCR Settings")
	if not getattr(settings, "enable_jev_shadow", 0):
		return

	api_key = settings.get_password("typesafe_api_key")
	if not api_key:
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

	budget = flt(getattr(settings, "jev_monthly_budget_usd", 0))
	if budget > 0:
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
		response = requests.post(
			_TYPESAFE_URL,
			json=body,
			headers={"Authorization": f"Bearer {api_key}"},
			timeout=timeout,
		)
		response.raise_for_status()
		payload = response.json()

		answer = payload["answers"]["supplier"]
		choice = answer["choice"]
		probabilities = answer.get("probabilities") or {}
		probability = max((flt(p) for p in probabilities.values()), default=0.0)
		usage = payload.get("usage") or {}
		input_tokens = flt(usage.get("input_tokens") or 0)
		cost_usd = input_tokens * _PRICE_PER_MTOK / 1e6
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
				"jev_note": "",
			}
		)
	except Exception as exc:
		note = _sanitize(f"{type(exc).__name__}: {exc}", api_key)
		try:
			frappe.log_error(
				title="Jev Shadow Failed",
				message=f"OCR Import {ocr_import_name}: {note}",
			)
		except Exception:
			pass  # never let logging itself break the shadow job
		_finish({"jev_status": "Error", "jev_note": note})
