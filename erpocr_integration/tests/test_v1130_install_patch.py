"""v1.13.0: Purchase Invoice duplicate-bill Custom Fields, Fleet Vehicle re-anchor (Q19)."""

from itertools import pairwise
from unittest.mock import patch

from erpocr_integration.install import setup_custom_fields, setup_optional_custom_fields
from erpocr_integration.patches.v1_13_0 import reanchor_fleet_ocr_section as patch_mod

_PI_FIELDS = [
	"custom_duplicate_bill_override",
	"custom_duplicate_bill_reason",
	"custom_duplicate_bill_approved_by",
	"custom_duplicate_bill_approved_at",
]


class TestPurchaseInvoiceFields:
	def _pi_fields(self):
		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			setup_custom_fields()
		return mock_create.call_args[0][0]["Purchase Invoice"]

	def test_four_fields_plain_no_copy_chained_after_bill_date(self):
		rows = {f["fieldname"]: f for f in self._pi_fields()}
		assert [n for n in rows if n.startswith("custom_duplicate_bill")] == _PI_FIELDS
		for name in _PI_FIELDS:
			assert rows[name]["no_copy"] == 1
			assert rows[name]["fieldtype"] not in ("Section Break", "Column Break", "Tab Break")
		assert rows[_PI_FIELDS[0]]["insert_after"] == "bill_date"
		for prev, cur in pairwise(_PI_FIELDS):
			assert rows[cur]["insert_after"] == prev

	def test_field_specs(self):
		rows = {f["fieldname"]: f for f in self._pi_fields()}
		gate = "eval:doc.custom_duplicate_bill_override"
		assert rows[_PI_FIELDS[0]]["fieldtype"] == "Check" and rows[_PI_FIELDS[0]]["in_standard_filter"] == 1
		assert rows[_PI_FIELDS[1]]["fieldtype"] == "Small Text"
		assert rows[_PI_FIELDS[1]]["depends_on"] == rows[_PI_FIELDS[1]]["mandatory_depends_on"] == gate
		assert rows[_PI_FIELDS[2]]["fieldtype"] == "Link" and rows[_PI_FIELDS[2]]["options"] == "User"
		assert rows[_PI_FIELDS[3]]["fieldtype"] == "Datetime"
		assert rows[_PI_FIELDS[2]]["read_only"] == rows[_PI_FIELDS[3]]["read_only"] == 1


class TestFleetAnchor:
	def _section(self):
		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			setup_optional_custom_fields()
		return next(
			f for f in mock_create.call_args[0][0]["Fleet Vehicle"] if f["fieldname"] == "custom_ocr_section"
		)

	def test_anchors_on_driver_name(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.get_meta.return_value.has_field.return_value = True
		assert self._section()["insert_after"] == "driver_name"

	def test_missing_driver_name_appends_and_logs_never_crashes(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.get_meta.return_value.has_field.return_value = False
		mock_frappe.log_error.reset_mock()
		assert self._section()["insert_after"] is None
		mock_frappe.log_error.assert_called_once()
		mock_frappe.get_meta.return_value.has_field.return_value = True


class TestReanchorPatch:
	def test_old_anchor_moved_and_cache_cleared(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "wesbank_cost_code"
		mock_frappe.db.set_value.reset_mock()
		mock_frappe.clear_cache.reset_mock()
		patch_mod.execute()
		mock_frappe.db.set_value.assert_called_once_with(
			"Custom Field", "Fleet Vehicle-custom_ocr_section", "insert_after", "driver_name"
		)
		mock_frappe.clear_cache.assert_called_once_with(doctype="Fleet Vehicle")
		mock_frappe.db.get_value.return_value = None

	def test_already_different_untouched(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "some_hand_fixed_field"
		mock_frappe.db.set_value.reset_mock()
		mock_frappe.clear_cache.reset_mock()
		patch_mod.execute()
		mock_frappe.db.set_value.assert_not_called()
		mock_frappe.clear_cache.assert_not_called()
		mock_frappe.db.get_value.return_value = None

	def test_doctype_absent_is_noop(self, mock_frappe):
		mock_frappe.db.exists.return_value = False
		mock_frappe.db.set_value.reset_mock()
		patch_mod.execute()
		mock_frappe.db.set_value.assert_not_called()
