"""Auto-record for high-confidence Fleet Card slips (Q8, v1.8.0).

Fleet Card slips are NULL-PI control records (ADR-0003): the provider's
monthly invoice books the cost, and the slip just needs Mark Recorded once
verified. That per-slip click built a growing backlog (34+ at the 2026-07-06
review). When ``OCR Settings.enable_fleet_auto_record`` is on (OFF by
default, ADR-0002 philosophy), a slip that lands or updates to ``Matched``
with high confidence is completed via the existing ``mark_recorded()`` path.

High confidence (Willie's ruling 2026-07-09):
  - ``fleet_vehicle`` set with a CONFIRMED match — exact ("Auto Matched") or
    driver/operator-supplied ("Confirmed"); NEVER a fuzzy "Suggested".
  - Recon payload present — Fuel: litres + total; Toll: total.
  - Slip type "Other" never auto-records (unauthorized_flag — human eyes).

Audit mirrors auto-draft: ``auto_recorded`` flag + ``auto_record_skipped_reason``
so "why didn't it auto-record" is a group-by, not a debugging session.

ADR-0003 invariants: never applies to Direct Expense slips; ``purchase_invoice``
stays NULL (mark_recorded creates nothing); ``Completed`` continues to feed
fleet_management's monthly_summary.py (it reads Matched AND Completed).
"""

import frappe
from frappe.utils import flt

# Exact registration match or a driver/operator-confirmed pick — never fuzzy.
_CONFIRMED_VEHICLE_STATUSES = frozenset({"Auto Matched", "Confirmed"})


def _write_skip_reason(ocr_fleet, reason: str) -> None:
	"""Persist the skip reason without touching `modified`.

	This runs inside on_update — bumping `modified` here would make the
	operator's just-saved form stale ("Document has been modified after you
	opened it" on their next save). Skips the write when the reason is
	already recorded (routine re-saves of a non-qualifying slip).
	"""
	if (getattr(ocr_fleet, "auto_record_skipped_reason", "") or "") == reason:
		return
	ocr_fleet.auto_record_skipped_reason = reason
	frappe.db.set_value(
		"OCR Fleet Slip",
		ocr_fleet.name,
		"auto_record_skipped_reason",
		reason,
		update_modified=False,
	)


def _is_high_confidence(ocr_fleet) -> tuple[bool, str]:
	"""Check whether a Fleet Card slip is safe to auto-record.

	Returns:
	    (is_high_confidence, reason_if_not)
	"""
	if ocr_fleet.slip_type not in ("Fuel", "Toll"):
		return (
			False,
			f"Slip type '{ocr_fleet.slip_type or '?'}' requires manual review (only Fuel/Toll auto-record)",
		)

	if not ocr_fleet.fleet_vehicle:
		return False, "No Fleet Vehicle linked"
	if ocr_fleet.vehicle_match_status not in _CONFIRMED_VEHICLE_STATUSES:
		return (
			False,
			f"Vehicle match is '{ocr_fleet.vehicle_match_status}' (needs exact or confirmed, not fuzzy)",
		)

	if flt(ocr_fleet.total_amount) <= 0:
		return False, "No total amount extracted"
	if ocr_fleet.slip_type == "Fuel" and flt(ocr_fleet.litres) <= 0:
		return False, "Fuel slip has no litres (recon payload incomplete)"

	return True, ""


def attempt_auto_record(ocr_fleet, settings) -> bool:
	"""Attempt to auto-record a high-confidence Fleet Card slip.

	Called after matching completes in fleet_gemini_process() and from the
	controller's on_update when a slip reaches "Matched". Completes via the
	existing mark_recorded() path — which itself re-enforces posting_mode and
	vehicle guards server-side. Falls back gracefully on any error: the slip
	stays at its current status and the operator handles it manually.

	Returns:
	    True if the slip was auto-recorded, False otherwise.
	"""
	if not getattr(settings, "enable_fleet_auto_record", 0):
		return False

	# Fleet Card only — Direct Expense slips take the PI path (ADR-0002/0003)
	# and unset posting_mode means the fail-safe fork parked it for review.
	# No skip reason written: the audit field answers "why didn't my Fleet
	# Card slip auto-record", not "why didn't a non-candidate".
	if ocr_fleet.posting_mode != "Fleet Card":
		return False

	# .get(): tolerate pre-migrate rows that lack the v1.8.0 column.
	if ocr_fleet.get("auto_recorded"):
		return False

	# Defence in depth (ADR-0003): a Fleet Card slip must never carry a PI.
	if getattr(ocr_fleet, "purchase_invoice", None):
		return False

	if ocr_fleet.status != "Matched":
		_write_skip_reason(ocr_fleet, f"Status is '{ocr_fleet.status}' (requires 'Matched')")
		return False

	is_high, reason = _is_high_confidence(ocr_fleet)
	if not is_high:
		_write_skip_reason(ocr_fleet, reason)
		return False

	# Savepoint before the state change (same pattern as bulk_mark_recorded):
	# frappe's save() writes the DB row BEFORE post-save hooks run, so an
	# exception raised after that write would otherwise leave
	# status="Completed" in the open transaction while the except clause
	# resets only the audit fields — the outer commit would ship a slip that
	# is Completed with a skip reason saying it failed.
	savepoint = "fleet_auto_record"
	frappe.db.savepoint(savepoint)
	prior_status = ocr_fleet.status
	try:
		ocr_fleet.auto_recorded = 1
		ocr_fleet.auto_record_skipped_reason = ""
		# Reuse the existing terminal-disposition path — it re-checks
		# posting_mode/status/vehicle and saves. quiet: no per-slip msgprint
		# from a background job.
		ocr_fleet.flags.quiet_mark_recorded = True
		ocr_fleet.mark_recorded()
		return True
	except Exception as e:
		# Revert the inner save's writes (incl. any persisted
		# status="Completed") FIRST, then record the audit outcome.
		frappe.db.rollback(save_point=savepoint)
		# mark_recorded's frappe.throw queued its message before raising —
		# clear it, or the operator's (successful) outer save renders a red
		# error dialog for a slip that simply parked for manual review.
		frappe.clear_messages()
		# Reset the in-memory doc too — mark_recorded may have mutated status
		# before throwing, and the outer save's response serializes this doc.
		ocr_fleet.status = prior_status
		ocr_fleet.auto_recorded = 0
		ocr_fleet.auto_record_skipped_reason = f"Auto-record failed: {e}"
		frappe.db.set_value(
			"OCR Fleet Slip",
			ocr_fleet.name,
			{"auto_recorded": 0, "auto_record_skipped_reason": ocr_fleet.auto_record_skipped_reason},
			update_modified=False,
		)
		frappe.log_error(
			title="Fleet Auto-Record Failed",
			message=f"Auto-record failed for {ocr_fleet.name}: {e}\n{frappe.get_traceback()}",
		)
		return False
