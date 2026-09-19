"""Doctype-shape test for the Jev shadow trial's blindness (Q17, v1.12.0).

Terra/Grok review, item 6: on the prod-copy bench 2 of 3 OCR Managers also
hold System Manager, so permlevel 1 alone does not keep the trial blind for
them — System Manager has read access at permlevel 1 by design (see the
existing DocPerm row this doctype already carries, matching OCR Fleet Slip's
raw_payload pattern). The actual blindness guarantee is `hidden: 1` on every
Jev field AND the section/column breaks that hold them — nobody sees any of
it on the form, regardless of role. permlevel 1 + read_only stay as
defence-in-depth (belt and braces against a future custom Property Setter
un-hiding a single field without also clearing permlevel).

This test loads the real ocr_import.json (no frappe involved) and asserts the
doctype-JSON contract directly — it is the one guarantee that can't silently
regress via a future edit that adds a field to the Jev section without
copying all three flags.
"""

import json
import os

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OCR_IMPORT_JSON = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "ocr_import", "ocr_import.json")

# Every field this trial introduced, plus the section/column breaks that hold
# them. Hardcoded rather than pattern-matched on "jev_status/jev_supplier/...
# starts with jev_" or similar, so a typo'd fieldname can't silently escape
# the check by not matching the pattern.
_JEV_SECTION_BREAKS = {"section_break_jev", "column_break_jev"}
_JEV_DATA_FIELDS = {
	"jev_status",
	"jev_supplier",
	"jev_choice_none",
	"jev_probability",
	"jev_model",
	"jev_matcher_supplier",
	"jev_matcher_status",
	"jev_candidate_count",
	"jev_cost_usd",
	"jev_run_at",
	"jev_note",
}


def _load_ocr_import_fields():
	with open(OCR_IMPORT_JSON) as f:
		doc = json.load(f)
	return {f["fieldname"]: f for f in doc["fields"]}


def test_all_jev_fieldnames_present_and_accounted_for():
	"""Sanity check on the test's own fixture lists — every jev_* fieldname in
	the doctype must be one we're asserting on (catches a future field added
	to the section without being added here too)."""
	fields = _load_ocr_import_fields()
	all_jev_fieldnames = {name for name in fields if name.startswith("jev_") or name in _JEV_SECTION_BREAKS}
	expected = _JEV_DATA_FIELDS | _JEV_SECTION_BREAKS
	assert all_jev_fieldnames == expected


def test_jev_section_and_column_breaks_hidden_and_permlevel_1():
	fields = _load_ocr_import_fields()
	for fieldname in _JEV_SECTION_BREAKS:
		field = fields[fieldname]
		assert field.get("hidden") == 1, f"{fieldname} must be hidden=1"
		assert field.get("permlevel") == 1, f"{fieldname} must be permlevel=1"


def test_every_jev_data_field_hidden_permlevel_1_read_only():
	fields = _load_ocr_import_fields()
	for fieldname in _JEV_DATA_FIELDS:
		field = fields[fieldname]
		assert field.get("hidden") == 1, f"{fieldname} must be hidden=1"
		assert field.get("permlevel") == 1, f"{fieldname} must be permlevel=1"
		assert field.get("read_only") == 1, f"{fieldname} must be read_only=1"


def test_no_other_field_in_the_doctype_is_hidden_permlevel_1():
	"""Guard against the assertion above becoming vacuous — confirms these
	flags are a Jev-section-specific addition, not something already true of
	every field (e.g. raw_payload, which is permlevel=1 but NOT hidden — it
	remains visible-to-System-Manager-only via permlevel alone, a DIFFERENT,
	pre-existing pattern this trial deliberately does not reuse)."""
	fields = _load_ocr_import_fields()
	non_jev_hidden_permlevel1 = {
		name
		for name, field in fields.items()
		if name not in (_JEV_DATA_FIELDS | _JEV_SECTION_BREAKS)
		and field.get("hidden") == 1
		and field.get("permlevel") == 1
	}
	assert non_jev_hidden_permlevel1 == set()
	# And raw_payload specifically is the OLD pattern (permlevel only, not hidden).
	assert fields["raw_payload"].get("permlevel") == 1
	assert fields["raw_payload"].get("hidden") != 1
