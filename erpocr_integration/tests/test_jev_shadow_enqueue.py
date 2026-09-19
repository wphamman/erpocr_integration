"""Tests for the Jev shadow enqueue wiring inside api.gemini_process (Q17, v1.12.0).

Focused on ORCHESTRATION only — matching itself is patched away (covered by
test_matching.py / test_api.py already). What's under test here: the shadow job
is enqueued exactly when enable_jev_shadow is on, carrying the matcher snapshot
taken BEFORE auto-draft runs, and an enqueue failure never fails extraction.
"""

from unittest.mock import MagicMock, patch

# Pre-import modules so patch.object() can resolve their attributes.
import erpocr_integration.api
import erpocr_integration.tasks.auto_draft
import erpocr_integration.tasks.drive_integration
import erpocr_integration.tasks.gemini_extract


def _minimal_invoice_list():
	return [
		{
			"header_fields": {
				"supplier_name": "Acme Trading (Pty) Ltd",
				"invoice_number": "INV-1",
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


def _default_matching(ocr_import, header_fields, settings):
	ocr_import.supplier = "SUP-001"
	ocr_import.supplier_match_status = "Auto Matched"


def _jev_enqueue_calls(mock_frappe):
	return [
		c
		for c in mock_frappe.enqueue.call_args_list
		if c.args and c.args[0] == "erpocr_integration.tasks.jev_shadow.run_jev_shadow"
	]


class TestJevShadowEnqueueWiring:
	def _run(self, mock_frappe, settings, matching_side_effect=None):
		placeholder = MagicMock()
		placeholder.name = "OCR-IMP-0001"
		placeholder.email_message_id = None
		placeholder.drive_file_id = None
		placeholder.drive_retry_count = 0

		mock_frappe.get_doc = MagicMock(return_value=placeholder)
		mock_frappe.get_cached_doc = MagicMock(return_value=settings)
		# Truthy existing_drive_file_id routes gemini_process past the "upload new
		# file" branch and into the "move to archive" branch, which we patch away
		# below — keeps this test focused on the jev-enqueue wiring only.
		mock_frappe.db.get_value = MagicMock(return_value="existing-drive-id")

		with (
			patch.object(
				erpocr_integration.tasks.gemini_extract,
				"extract_invoice_data",
				return_value=_minimal_invoice_list(),
			),
			patch.object(erpocr_integration.api, "_populate_ocr_import", MagicMock()),
			patch.object(
				erpocr_integration.api,
				"_run_matching",
				side_effect=matching_side_effect or _default_matching,
			),
			patch.object(
				erpocr_integration.tasks.drive_integration,
				"move_file_to_archive",
				return_value={"file_id": "existing-drive-id", "shareable_link": None, "folder_path": None},
			),
		):
			erpocr_integration.api.gemini_process(
				pdf_content=b"%PDF-1.4 test",
				filename="invoice.pdf",
				ocr_import_name="OCR-IMP-0001",
				source_type="Gemini Manual Upload",
				uploaded_by="Administrator",
			)
		return placeholder

	def test_enqueued_when_enabled(self, mock_frappe, sample_settings):
		sample_settings.enable_jev_shadow = 1
		self._run(mock_frappe, sample_settings)

		calls = _jev_enqueue_calls(mock_frappe)
		assert len(calls) == 1
		kwargs = calls[0].kwargs
		assert kwargs["ocr_import_name"] == "OCR-IMP-0001"
		assert kwargs["matcher_supplier"] == "SUP-001"
		assert kwargs["matcher_status"] == "Auto Matched"
		assert kwargs["queue"] == "short"

	def test_not_enqueued_when_disabled(self, mock_frappe, sample_settings):
		sample_settings.enable_jev_shadow = 0
		self._run(mock_frappe, sample_settings)
		assert _jev_enqueue_calls(mock_frappe) == []

	def test_not_enqueued_by_default_when_unset(self, mock_frappe, sample_settings):
		"""sample_settings doesn't define enable_jev_shadow at all — getattr's
		default (0) must govern, matching a freshly-migrated site."""
		assert not hasattr(sample_settings, "enable_jev_shadow")
		self._run(mock_frappe, sample_settings)
		assert _jev_enqueue_calls(mock_frappe) == []

	def test_snapshot_taken_before_auto_draft_mutation(self, mock_frappe, sample_settings):
		"""Even when auto-draft runs (and, hypothetically, changes the in-memory
		doc's supplier fields), the enqueued snapshot must be what MATCHING
		produced, not whatever auto-draft leaves behind."""
		sample_settings.enable_jev_shadow = 1
		sample_settings.enable_auto_draft = 1

		def mutate_after_match(ocr_doc, settings):
			ocr_doc.supplier = "SUP-MUTATED-BY-AUTODRAFT"
			ocr_doc.supplier_match_status = "Confirmed"
			return False

		with patch.object(
			erpocr_integration.tasks.auto_draft, "attempt_auto_draft", side_effect=mutate_after_match
		):
			self._run(mock_frappe, sample_settings)

		calls = _jev_enqueue_calls(mock_frappe)
		assert len(calls) == 1
		assert calls[0].kwargs["matcher_supplier"] == "SUP-001"
		assert calls[0].kwargs["matcher_status"] == "Auto Matched"

	def test_enqueue_exception_does_not_fail_extraction(self, mock_frappe, sample_settings):
		sample_settings.enable_jev_shadow = 1
		mock_frappe.enqueue = MagicMock(side_effect=Exception("queue down"))

		# Must not raise.
		self._run(mock_frappe, sample_settings)

		assert any("Jev Shadow Enqueue Failed" in str(c) for c in mock_frappe.log_error.call_args_list)
		# And the overall run must not have been recorded as an Error — the
		# top-level except was never reached (no rollback/Error status set for
		# THIS reason). We assert no "OCR Integration Error" was logged.
		assert not any("OCR Integration Error" in str(c) for c in mock_frappe.log_error.call_args_list)

	def test_dn_fleet_statement_pipelines_untouched(self):
		"""Sanity check on scope: the shadow job module is imported only from
		api.py — never from dn_api.py / fleet_api.py / statement_api.py."""
		import inspect

		import erpocr_integration.dn_api as dn_api
		import erpocr_integration.fleet_api as fleet_api
		import erpocr_integration.statement_api as statement_api

		for module in (dn_api, fleet_api, statement_api):
			source = inspect.getsource(module)
			assert "jev_shadow" not in source
