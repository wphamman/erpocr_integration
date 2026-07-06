"""Tests for erpocr_integration.install — conditional Custom Field setup.

Covers the v1.1.6 "soft-dep-on-fleet_management" install pattern: the install
hook checks whether the Fleet Vehicle doctype exists before provisioning the
optional `fleet_vehicle` Custom Field on OCR Import.

Frappe is mocked at module-import time by conftest; we use the shared mock_frappe
fixture to configure return values per test.
"""

from unittest.mock import patch

from erpocr_integration.install import (
	after_install,
	after_migrate,
	setup_optional_custom_fields,
)
from erpocr_integration.patches.v1_1_6 import (
	migrate_fleet_vehicle_to_custom_field as patch_mod,
)


class TestSetupOptionalCustomFields:
	def test_skips_when_fleet_vehicle_doctype_absent(self, mock_frappe):
		"""Site without fleet_management → no Custom Field is created."""
		mock_frappe.db.exists.return_value = False

		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			setup_optional_custom_fields()

		mock_frappe.db.exists.assert_called_once_with("DocType", "Fleet Vehicle")
		mock_create.assert_not_called()

	def test_creates_custom_field_when_fleet_vehicle_doctype_present(self, mock_frappe):
		"""Site with fleet_management → exactly one OCR Import Custom Field
		is provisioned, pointing at Fleet Vehicle, inserted after `supplier`."""
		mock_frappe.db.exists.return_value = True

		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			setup_optional_custom_fields()

		mock_create.assert_called_once()
		args, kwargs = mock_create.call_args
		custom_fields_dict = args[0]
		assert kwargs.get("ignore_validate") is True
		assert "OCR Import" in custom_fields_dict
		fields = custom_fields_dict["OCR Import"]
		assert len(fields) == 1
		f = fields[0]
		assert f["fieldname"] == "fleet_vehicle"
		assert f["fieldtype"] == "Link"
		assert f["options"] == "Fleet Vehicle"
		assert f["insert_after"] == "supplier"
		assert f["label"] == "Fleet Vehicle (optional)"

	def test_after_install_delegates(self, mock_frappe):
		"""after_install runs the always-on back-link setup but skips the gated
		Fleet Vehicle block when the doctype is absent (standalone site)."""
		mock_frappe.db.exists.return_value = False
		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			after_install()
		# Exactly one call: setup_custom_fields (PI/PR/JE → OCR Import back-links).
		# The gated Fleet Vehicle block must NOT have fired.
		mock_create.assert_called_once()
		created = mock_create.call_args[0][0]
		assert "Fleet Vehicle" not in created
		assert set(created) == {"Purchase Invoice", "Purchase Receipt", "Journal Entry"}
		for rows in created.values():
			assert rows[0]["fieldname"] == "custom_ocr_import"
			assert rows[0]["options"] == "OCR Import"

	def test_after_migrate_delegates(self, mock_frappe):
		"""after_migrate runs both setups when Fleet Vehicle exists; idempotency
		comes from create_custom_fields itself (Frappe-side guarantee)."""
		mock_frappe.db.exists.return_value = True
		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			after_migrate()
		# Two calls: back-links (always) + the gated Fleet Vehicle/OCR Import block.
		assert mock_create.call_count == 2
		gated = mock_create.call_args_list[1][0][0]
		assert "Fleet Vehicle" in gated
		# The five fields previously shipped as fixtures (moved here — review O1)
		assert [f["fieldname"] for f in gated["Fleet Vehicle"]] == [
			"custom_ocr_section",
			"custom_fleet_card_provider",
			"custom_fleet_control_account",
			"custom_column_break_ocr",
			"custom_cost_center",
		]


class TestMigrationPatch:
	"""Covers patches.v1_1_6.migrate_fleet_vehicle_to_custom_field."""

	def test_clears_stopgap_property_setters_on_both_doctypes(self, mock_frappe):
		"""Operators who applied the v1.1.5 stopgap (Property Setter overriding
		fleet_vehicle.options) get cleaned up automatically on the v1.1.6 patch."""
		mock_frappe.get_all.return_value = ["ps-1", "ps-2"]
		mock_frappe.db.exists.return_value = False  # no Fleet Vehicle on this site

		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			patch_mod.execute()

		# Property Setter cleanup: queried for both OCR Import + OCR Fleet Slip
		# with field_name=fleet_vehicle, property=options
		mock_frappe.get_all.assert_called_once()
		args, kwargs = mock_frappe.get_all.call_args
		assert args[0] == "Property Setter"
		filters = kwargs["filters"]
		assert filters["doc_type"] == ("in", ("OCR Import", "OCR Fleet Slip"))
		assert filters["field_name"] == "fleet_vehicle"
		assert filters["property"] == "options"
		assert kwargs["pluck"] == "name"

		# Each stale row was deleted (order doesn't matter)
		assert mock_frappe.delete_doc.call_count == 2
		mock_frappe.delete_doc.assert_any_call("Property Setter", "ps-1", ignore_permissions=True, force=True)
		mock_frappe.delete_doc.assert_any_call("Property Setter", "ps-2", ignore_permissions=True, force=True)

		# No Custom Field created when Fleet Vehicle doctype absent
		mock_create.assert_not_called()

	def test_runs_install_when_fleet_vehicle_present(self, mock_frappe):
		"""On a site with fleet_management, the patch ensures the Custom Field
		is back in place after migrate — even if no stopgap Property Setters
		were ever applied."""
		mock_frappe.get_all.return_value = []  # no stopgap Property Setters
		mock_frappe.db.exists.return_value = True

		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			patch_mod.execute()

		mock_frappe.delete_doc.assert_not_called()
		mock_create.assert_called_once()
