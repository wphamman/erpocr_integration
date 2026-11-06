"""v1.14.2: the retired-Gemini-model patch and the OCR Settings model choices."""

import json
from pathlib import Path

import pytest

from erpocr_integration.patches.v1_14_2 import replace_retired_gemini_models as patch_mod

SETTINGS_JSON = (
	Path(__file__).resolve().parents[1] / "erpnext_ocr" / "doctype" / "ocr_settings" / "ocr_settings.json"
)


def _run(mock_frappe, current):
	mock_frappe.db.get_single_value.return_value = current
	mock_frappe.db.set_single_value.reset_mock()
	patch_mod.execute()
	mock_frappe.db.get_single_value.return_value = None


class TestReplaceRetiredGeminiModels:
	@pytest.mark.parametrize(
		"old,new",
		[
			("gemini-2.5-flash", "gemini-3.5-flash-lite"),
			("gemini-2.0-flash", "gemini-3.5-flash-lite"),
			("gemini-2.5-pro", "gemini-3.6-flash"),
		],
	)
	def test_retired_model_moved_to_its_replacement(self, mock_frappe, old, new):
		_run(mock_frappe, old)
		mock_frappe.db.set_single_value.assert_called_once_with("OCR Settings", "gemini_model", new)

	@pytest.mark.parametrize("current", ["gemini-3.6-flash", "gemini-flash-latest", None, ""])
	def test_current_or_unset_choice_untouched(self, mock_frappe, current):
		_run(mock_frappe, current)
		mock_frappe.db.set_single_value.assert_not_called()


class TestModelField:
	def test_every_patch_target_is_a_dropdown_option_and_no_retired_name_is(self):
		field = next(
			f for f in json.loads(SETTINGS_JSON.read_text())["fields"] if f["fieldname"] == "gemini_model"
		)
		options = field["options"].split("\n")
		assert field["default"] == "gemini-3.5-flash-lite"
		assert set(patch_mod._REPLACEMENTS.values()) <= set(options)
		assert not set(patch_mod._REPLACEMENTS) & set(options)
