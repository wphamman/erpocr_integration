"""Q8 (v1.8.0): opt-in Fleet Card auto-record + bulk Mark Recorded.

Covers the confidence gate + skip reasons (attempt_auto_record), the OFF-by-
default regression (behavior byte-identical to v1.7.0 with the setting off),
the ADR-0003 invariant (no path creates/links a PI on a Fleet Card slip), the
on_update Desk trigger, and per-row server revalidation of the bulk action.
"""

from unittest.mock import MagicMock

import pytest

from erpocr_integration.fleet_api import bulk_mark_recorded
from erpocr_integration.tasks.auto_record import attempt_auto_record
from erpocr_integration.tests.test_fleet_controller import _make_fleet_slip, _make_settings


def _fleet_card_slip(**overrides):
	"""A high-confidence Fleet Card fuel slip — the auto-record happy path."""
	defaults = dict(
		status="Matched",
		posting_mode="Fleet Card",
		fleet_card_supplier="WesBank",
		fleet_vehicle="VEH-001",
		vehicle_match_status="Auto Matched",
		slip_type="Fuel",
		litres=50.0,
		total_amount=1125.00,
	)
	defaults.update(overrides)
	return _make_fleet_slip(**defaults)


def _settings(auto_record=1, **overrides):
	return _make_settings(enable_fleet_auto_record=auto_record, **overrides)


# ---------------------------------------------------------------------------
# OFF by default — regression: byte-identical to v1.7.0
# ---------------------------------------------------------------------------


class TestAutoRecordOff:
	def test_setting_absent_is_no_op(self, mock_frappe):
		"""Settings without the field (pre-v1.8.0 shape) → nothing happens."""
		doc = _fleet_card_slip()
		settings = _make_settings()  # no enable_fleet_auto_record at all

		assert attempt_auto_record(doc, settings) is False

		assert doc.status == "Matched"
		assert doc.auto_recorded == 0
		doc.save.assert_not_called()
		mock_frappe.db.set_value.assert_not_called()

	def test_setting_off_is_no_op(self, mock_frappe):
		doc = _fleet_card_slip()

		assert attempt_auto_record(doc, _settings(auto_record=0)) is False

		assert doc.status == "Matched"
		doc.save.assert_not_called()
		mock_frappe.db.set_value.assert_not_called()


# ---------------------------------------------------------------------------
# The confidence gate + skip reasons
# ---------------------------------------------------------------------------


class TestAutoRecordGate:
	def test_direct_expense_never_touched(self, mock_frappe):
		"""ADR-0002/0003: Direct Expense slips are outside auto-record entirely —
		no state change AND no skip-reason noise (they're not candidates)."""
		doc = _fleet_card_slip(posting_mode="Direct Expense")

		assert attempt_auto_record(doc, _settings()) is False

		assert doc.status == "Matched"
		doc.save.assert_not_called()
		mock_frappe.db.set_value.assert_not_called()

	def test_unset_posting_mode_never_touched(self, mock_frappe):
		"""The P4 fail-safe fork (blank posting_mode) stays parked for review."""
		doc = _fleet_card_slip(posting_mode="")

		assert attempt_auto_record(doc, _settings()) is False
		mock_frappe.db.set_value.assert_not_called()

	def test_fuzzy_vehicle_match_skips_with_reason(self, mock_frappe):
		"""A fuzzy 'Suggested' vehicle NEVER auto-records — wrong-vehicle
		attribution is worse than the manual click."""
		doc = _fleet_card_slip(vehicle_match_status="Suggested")

		assert attempt_auto_record(doc, _settings()) is False

		assert doc.status == "Matched"
		reason = mock_frappe.db.set_value.call_args[0][3]
		assert "Suggested" in reason
		assert "fuzzy" in reason

	def test_no_vehicle_skips_with_reason(self, mock_frappe):
		doc = _fleet_card_slip(fleet_vehicle="")

		assert attempt_auto_record(doc, _settings()) is False
		assert "No Fleet Vehicle" in mock_frappe.db.set_value.call_args[0][3]

	def test_other_slip_type_skips_with_reason(self, mock_frappe):
		"""Willie's ruling: 'Other' slips (unauthorized_flag) always need eyes."""
		doc = _fleet_card_slip(slip_type="Other", unauthorized_flag=1)

		assert attempt_auto_record(doc, _settings()) is False

		assert doc.status == "Matched"
		assert "manual review" in mock_frappe.db.set_value.call_args[0][3]

	def test_fuel_without_litres_skips(self, mock_frappe):
		doc = _fleet_card_slip(litres=0)

		assert attempt_auto_record(doc, _settings()) is False
		assert "litres" in mock_frappe.db.set_value.call_args[0][3]

	def test_missing_total_skips(self, mock_frappe):
		doc = _fleet_card_slip(total_amount=0)

		assert attempt_auto_record(doc, _settings()) is False
		assert "total" in mock_frappe.db.set_value.call_args[0][3].lower()

	def test_non_matched_status_skips_with_reason(self, mock_frappe):
		doc = _fleet_card_slip(status="Needs Review")

		assert attempt_auto_record(doc, _settings()) is False
		assert "Needs Review" in mock_frappe.db.set_value.call_args[0][3]

	def test_already_auto_recorded_skips(self, mock_frappe):
		doc = _fleet_card_slip(auto_recorded=1)

		assert attempt_auto_record(doc, _settings()) is False
		mock_frappe.db.set_value.assert_not_called()

	def test_skip_reason_write_never_bumps_modified(self, mock_frappe):
		"""The skip-reason write runs inside on_update — bumping `modified`
		would make the operator's just-saved form stale ('Document has been
		modified') on their next save."""
		doc = _fleet_card_slip(vehicle_match_status="Suggested")

		attempt_auto_record(doc, _settings())

		assert mock_frappe.db.set_value.call_args.kwargs["update_modified"] is False

	def test_unchanged_skip_reason_not_rewritten(self, mock_frappe):
		"""Routine re-saves of a non-qualifying slip must not rewrite the
		identical reason on every save."""
		doc = _fleet_card_slip(vehicle_match_status="Suggested")

		attempt_auto_record(doc, _settings())
		assert mock_frappe.db.set_value.call_count == 1

		attempt_auto_record(doc, _settings())  # same doc, same reason
		assert mock_frappe.db.set_value.call_count == 1  # no second write


# ---------------------------------------------------------------------------
# The happy path — and the ADR-0003 invariant
# ---------------------------------------------------------------------------


class TestAutoRecordHappyPath:
	def test_fuel_confirmed_exact_records(self, mock_frappe):
		doc = _fleet_card_slip(vehicle_match_status="Auto Matched")

		assert attempt_auto_record(doc, _settings()) is True

		assert doc.status == "Completed"
		assert doc.auto_recorded == 1
		assert doc.auto_record_skipped_reason == ""
		doc.save.assert_called_once()

	def test_fuel_driver_confirmed_records(self, mock_frappe):
		doc = _fleet_card_slip(vehicle_match_status="Confirmed")

		assert attempt_auto_record(doc, _settings()) is True
		assert doc.status == "Completed"

	def test_toll_records_with_total_only(self, mock_frappe):
		doc = _fleet_card_slip(slip_type="Toll", litres=0, total_amount=48.50)

		assert attempt_auto_record(doc, _settings()) is True
		assert doc.status == "Completed"

	def test_adr_0003_no_pi_created_or_linked(self, mock_frappe):
		"""The invariant: auto-record completes the slip as a control record —
		purchase_invoice stays NULL and no document of any kind is created."""
		doc = _fleet_card_slip()

		assert attempt_auto_record(doc, _settings()) is True

		assert doc.purchase_invoice is None
		mock_frappe.get_doc.assert_not_called()  # nothing instantiated, let alone a PI

	def test_quiet_no_msgprint_from_background(self, mock_frappe):
		doc = _fleet_card_slip()

		attempt_auto_record(doc, _settings())

		mock_frappe.msgprint.assert_not_called()

	def test_mark_recorded_failure_falls_back(self, mock_frappe):
		"""A save failure inside mark_recorded → flag reset PERSISTED (not just
		in-memory), reason recorded, queued throw-message cleared, error
		logged, slip left for manual handling."""
		doc = _fleet_card_slip()
		doc.save = MagicMock(side_effect=Exception("DB gone"))

		assert attempt_auto_record(doc, _settings()) is False

		assert doc.auto_recorded == 0
		written = mock_frappe.db.set_value.call_args[0][2]
		assert written["auto_recorded"] == 0
		assert "Auto-record failed" in written["auto_record_skipped_reason"]
		# modified must NOT be bumped (this can run inside on_update — a bump
		# would make the operator's open form stale)
		assert mock_frappe.db.set_value.call_args.kwargs["update_modified"] is False
		assert mock_frappe.clear_messages.called
		assert mock_frappe.log_error.called

	def test_failure_after_status_write_rolls_back_to_savepoint(self, mock_frappe):
		"""Bounce rework 1: frappe's save() writes the row BEFORE post-save
		hooks — a failure raised after that write must roll back to the
		savepoint (reverting the persisted status="Completed"), and the
		in-memory doc must revert to its prior status too (the outer save's
		response serializes it)."""
		doc = _fleet_card_slip()
		# mark_recorded mutates status, its save persists it, THEN a post-save
		# hook explodes — status="Completed" is already in the transaction.
		doc.save = MagicMock(side_effect=Exception("doc_events hook exploded"))

		assert attempt_auto_record(doc, _settings()) is False

		# rollback to the savepoint taken BEFORE the state change
		mock_frappe.db.savepoint.assert_called_once_with("fleet_auto_record")
		mock_frappe.db.rollback.assert_called_once_with(save_point="fleet_auto_record")
		# in-memory doc reverted: not Completed-with-a-failure-reason
		assert doc.status == "Matched"
		assert doc.auto_recorded == 0
		assert "Auto-record failed" in doc.auto_record_skipped_reason


# ---------------------------------------------------------------------------
# Desk trigger — on_update when a slip reaches Matched
# ---------------------------------------------------------------------------


class TestOnUpdateTrigger:
	def test_matched_fleet_card_slip_auto_records_on_save(self, mock_frappe):
		doc = _fleet_card_slip()
		mock_frappe.get_cached_doc.return_value = _settings()

		doc.on_update()

		assert doc.status == "Completed"
		assert doc.auto_recorded == 1

	def test_off_setting_leaves_desk_save_untouched(self, mock_frappe):
		doc = _fleet_card_slip()
		mock_frappe.get_cached_doc.return_value = _settings(auto_record=0)

		doc.on_update()

		assert doc.status == "Matched"
		doc.save.assert_not_called()

	def test_direct_expense_save_never_fetches_settings(self, mock_frappe):
		"""The cheap pre-check keeps routine Direct Expense saves out of the
		auto-record path entirely."""
		doc = _fleet_card_slip(posting_mode="Direct Expense")

		doc.on_update()

		assert doc.status == "Matched"
		mock_frappe.get_cached_doc.assert_not_called()


# ---------------------------------------------------------------------------
# Bulk Mark Recorded — per-row server revalidation
# ---------------------------------------------------------------------------


class TestBulkMarkRecorded:
	def _docs_by_name(self, docs):
		by_name = {d.name: d for d in docs}

		def _get_doc(doctype, name=None):
			if name in by_name:
				return by_name[name]
			raise Exception(f"{name} not found")

		return _get_doc

	def test_mixed_selection_revalidated_per_row(self, mock_frappe):
		"""Acceptance criterion: a Direct-Expense or non-Matched row in the
		selection is rejected with a reason; the valid rows proceed."""
		good = _fleet_card_slip()
		good.name = "OCR-FS-GOOD"
		direct = _fleet_card_slip(posting_mode="Direct Expense")
		direct.name = "OCR-FS-DIRECT"
		needs_review = _fleet_card_slip(status="Needs Review")
		needs_review.name = "OCR-FS-REVIEW"

		mock_frappe.get_doc = MagicMock(side_effect=self._docs_by_name([good, direct, needs_review]))

		result = bulk_mark_recorded(["OCR-FS-GOOD", "OCR-FS-DIRECT", "OCR-FS-REVIEW", "OCR-FS-GONE"])

		assert result["recorded"] == ["OCR-FS-GOOD"]
		assert good.status == "Completed"
		assert direct.status == "Matched"
		assert needs_review.status == "Needs Review"

		skipped = {s["name"]: s["reason"] for s in result["skipped"]}
		assert "Fleet Card" in skipped["OCR-FS-DIRECT"]
		assert "Matched" in skipped["OCR-FS-REVIEW"]
		assert "OCR-FS-GONE" in skipped  # nonexistent row skipped, not fatal

	def test_bulk_rows_complete_without_pi(self, mock_frappe):
		"""ADR-0003 through the bulk path: recorded rows keep purchase_invoice NULL."""
		good = _fleet_card_slip()
		good.name = "OCR-FS-GOOD"
		mock_frappe.get_doc = MagicMock(side_effect=self._docs_by_name([good]))

		result = bulk_mark_recorded(["OCR-FS-GOOD"])

		assert result["recorded"] == ["OCR-FS-GOOD"]
		assert good.purchase_invoice is None

	def test_per_row_write_permission_enforced(self, mock_frappe):
		"""mark_recorded()'s own per-document permission check still gates each
		row — a row the caller can't write is skipped, others proceed."""
		allowed = _fleet_card_slip()
		allowed.name = "OCR-FS-ALLOWED"
		denied = _fleet_card_slip()
		denied.name = "OCR-FS-DENIED"
		mock_frappe.get_doc = MagicMock(side_effect=self._docs_by_name([allowed, denied]))

		def _has_permission(doctype, ptype=None, doc=None, *a, **kw):
			return doc != "OCR-FS-DENIED"

		mock_frappe.has_permission = MagicMock(side_effect=_has_permission)

		result = bulk_mark_recorded(["OCR-FS-ALLOWED", "OCR-FS-DENIED"])

		assert result["recorded"] == ["OCR-FS-ALLOWED"]
		assert [s["name"] for s in result["skipped"]] == ["OCR-FS-DENIED"]
		assert denied.status == "Matched"

	def test_failed_row_rolls_back_to_savepoint(self, mock_frappe):
		"""A row that throws mid-save must roll back its own partial writes —
		without the savepoint, a save that dies after db_update would leave
		status=Completed in the transaction while the row reports skipped."""
		bad = _fleet_card_slip()
		bad.name = "OCR-FS-BAD"
		bad.save = MagicMock(side_effect=Exception("hook exploded"))
		good = _fleet_card_slip()
		good.name = "OCR-FS-GOOD"
		mock_frappe.get_doc = MagicMock(side_effect=self._docs_by_name([bad, good]))

		result = bulk_mark_recorded(["OCR-FS-BAD", "OCR-FS-GOOD"])

		assert result["recorded"] == ["OCR-FS-GOOD"]
		assert [s["name"] for s in result["skipped"]] == ["OCR-FS-BAD"]
		# every row opened a savepoint; the failed row rolled back to ITS OWN
		assert mock_frappe.db.savepoint.call_count == 2
		mock_frappe.db.rollback.assert_called_once_with(save_point="bulk_mark_recorded_0")

	def test_json_string_names_accepted(self, mock_frappe):
		good = _fleet_card_slip()
		good.name = "OCR-FS-GOOD"
		mock_frappe.get_doc = MagicMock(side_effect=self._docs_by_name([good]))

		result = bulk_mark_recorded('["OCR-FS-GOOD"]')

		assert result["recorded"] == ["OCR-FS-GOOD"]

	def test_empty_selection_throws(self, mock_frappe):
		with pytest.raises(Exception):
			bulk_mark_recorded([])

	def test_oversize_selection_throws(self, mock_frappe):
		with pytest.raises(Exception):
			bulk_mark_recorded([f"OCR-FS-{i}" for i in range(201)])

	def test_doctype_write_permission_required(self, mock_frappe):
		mock_frappe.has_permission = MagicMock(return_value=False)

		with pytest.raises(Exception):
			bulk_mark_recorded(["OCR-FS-1"])
