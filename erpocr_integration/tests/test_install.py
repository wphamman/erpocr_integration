"""Tests for erpocr_integration.install — conditional Custom Field setup.

Covers the v1.1.6 "soft-dep-on-fleet_management" install pattern: the install
hook checks whether the Fleet Vehicle doctype exists before provisioning the
optional `fleet_vehicle` Custom Field on OCR Import.

Also covers the v1.10.4 create-only role seed (`_seed_roles`) — see that
function's docstring and the CLAUDE.md "App fixtures re-import with
force=True" gotcha for why a Role fixture was the wrong tool.

Frappe is mocked at module-import time by conftest; we use the shared mock_frappe
fixture to configure return values per test.
"""

import json
import os
from typing import ClassVar
from unittest.mock import MagicMock, patch

from erpocr_integration import hooks
from erpocr_integration.install import (
	_SEED_ROLES,
	_seed_roles,
	after_install,
	after_migrate,
	setup_optional_custom_fields,
)
from erpocr_integration.patches.v1_1_6 import (
	migrate_fleet_vehicle_to_custom_field as patch_mod,
)

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


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

	def test_planted_custom_cost_center_ignores_user_permissions(self, mock_frappe):
		"""The Fleet Vehicle-parented custom_cost_center Custom Field must carry
		ignore_user_permissions=1 (v1.10.4) — prod already has this via a manual
		2026-08-31 fix; this makes a fresh install (the v16 test site) match."""
		mock_frappe.db.exists.return_value = True
		with patch("erpocr_integration.install.create_custom_fields") as mock_create:
			setup_optional_custom_fields()
		gated = mock_create.call_args[0][0]
		cost_center_field = next(f for f in gated["Fleet Vehicle"] if f["fieldname"] == "custom_cost_center")
		assert cost_center_field["ignore_user_permissions"] == 1


class TestSeedRoles:
	"""v1.10.4 — the 3 app-owned OCR roles are seeded create-only, not fixtured.

	`fixtures/role.json` was imported with `force=True` on every migrate, and
	Frappe's fixture import DELETES and RE-INSERTS the doc. On Frappe Press every
	app's deploy migrates the whole site, so an operator disabling one of these
	roles (or flipping desk_access / two_factor_auth) was silently reverted by an
	unrelated sibling app's deploy (2026-09-10 portfolio audit). `_seed_roles` is
	CREATE-ONLY: an existing Role is never touched.
	"""

	def test_declared_role_names(self):
		assert {r["role_name"] for r in _SEED_ROLES} == {
			"OCR Manager",
			"OCR Fleet Slip Reader",
			"OCR Fleet Driver",
		}

	def test_inserts_each_missing_role(self, mock_frappe):
		mock_frappe.db.exists.return_value = False
		created = []

		def fake_get_doc(d):
			doc_mock = MagicMock()
			created.append((d, doc_mock))
			return doc_mock

		mock_frappe.get_doc.side_effect = fake_get_doc

		_seed_roles()

		assert mock_frappe.get_doc.call_count == 3
		role_names = {d["role_name"] for d, _ in created}
		assert role_names == {"OCR Manager", "OCR Fleet Slip Reader", "OCR Fleet Driver"}
		for d, doc_mock in created:
			assert d["doctype"] == "Role"
			doc_mock.insert.assert_called_once_with(ignore_permissions=True)

		manager = next(d for d, _ in created if d["role_name"] == "OCR Manager")
		assert manager["is_custom"] == 1
		assert manager["desk_access"] == 1
		assert manager["search_bar"] == 1
		assert manager["notifications"] == 1

		reader = next(d for d, _ in created if d["role_name"] == "OCR Fleet Slip Reader")
		assert reader["is_custom"] == 0
		assert reader["desk_access"] == 1
		assert reader["disabled"] == 0

		driver = next(d for d, _ in created if d["role_name"] == "OCR Fleet Driver")
		assert driver["is_custom"] == 0
		assert driver["desk_access"] == 0
		assert driver["disabled"] == 0

	def test_inserts_nothing_when_all_roles_exist(self, mock_frappe):
		"""The whole point: an operator's edit to an existing role must never be
		re-asserted — no get_doc/insert/save/db.set_value at all."""
		mock_frappe.db.exists.return_value = True

		_seed_roles()

		mock_frappe.get_doc.assert_not_called()
		mock_frappe.db.set_value.assert_not_called()

	def test_after_install_seeds_roles_before_custom_fields(self, mock_frappe):
		mock_frappe.db.exists.return_value = False
		calls = []
		with (
			patch("erpocr_integration.install._seed_roles", side_effect=lambda: calls.append("seed_roles")),
			patch(
				"erpocr_integration.install.setup_custom_fields",
				side_effect=lambda: calls.append("setup_custom_fields"),
			),
			patch(
				"erpocr_integration.install.setup_optional_custom_fields",
				side_effect=lambda: calls.append("setup_optional_custom_fields"),
			),
		):
			after_install()
		assert calls == ["seed_roles", "setup_custom_fields", "setup_optional_custom_fields"]

	def test_after_migrate_seeds_roles_before_custom_fields(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		calls = []
		with (
			patch("erpocr_integration.install._seed_roles", side_effect=lambda: calls.append("seed_roles")),
			patch(
				"erpocr_integration.install.setup_custom_fields",
				side_effect=lambda: calls.append("setup_custom_fields"),
			),
			patch(
				"erpocr_integration.install.setup_optional_custom_fields",
				side_effect=lambda: calls.append("setup_optional_custom_fields"),
			),
		):
			after_migrate()
		assert calls == ["seed_roles", "setup_custom_fields", "setup_optional_custom_fields"]

	def test_hooks_fixtures_has_no_role_entry(self):
		"""The `fixtures` hook must no longer export a Role fixture — `_seed_roles`
		owns role creation now, and the `fixtures/role.json` FILE is gone (what
		actually matters: `import_fixtures` imports every JSON in `fixtures/`
		regardless of this list)."""
		dts = {f.get("dt") if isinstance(f, dict) else f for f in hooks.fixtures}
		assert "Role" not in dts

	def test_role_fixture_file_removed(self):
		assert not os.path.exists(os.path.join(APP_ROOT, "fixtures", "role.json"))


class TestCostCenterIgnoreUserPermissions:
	"""v1.10.4 — the 3 remaining ungated cost_center Links get
	ignore_user_permissions=1 (the unfinished half of the v1.1.1 fix, cbf639b).
	`OCR Fleet Slip.cost_center` already had it; `company` fields are
	deliberately untouched (ruled: stays ungated)."""

	_TARGETS: ClassVar[list[tuple[str, str]]] = [
		("ocr_import", "ocr_import.json"),
		("ocr_import_item", "ocr_import_item.json"),
		("ocr_service_mapping", "ocr_service_mapping.json"),
	]

	def _load(self, doctype_dir, filename):
		path = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", doctype_dir, filename)
		with open(path) as fh:
			return json.load(fh)

	def test_cost_center_field_ignores_user_permissions(self):
		for doctype_dir, filename in self._TARGETS:
			doc = self._load(doctype_dir, filename)
			cost_center_fields = [f for f in doc["fields"] if f.get("fieldname") == "cost_center"]
			assert len(cost_center_fields) == 1, f"{filename}: expected exactly one cost_center field"
			assert cost_center_fields[0].get("ignore_user_permissions") == 1, (
				f"{filename}: cost_center must carry ignore_user_permissions=1"
			)

	def test_company_field_not_touched(self):
		"""Ruled: company fields stay ungated — this release must not touch them."""
		for doctype_dir, filename in self._TARGETS:
			doc = self._load(doctype_dir, filename)
			company_fields = [f for f in doc["fields"] if f.get("fieldname") == "company"]
			for f in company_fields:
				assert "ignore_user_permissions" not in f, f"{filename}: company field must stay ungated"


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
