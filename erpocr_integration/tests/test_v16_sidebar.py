"""v16 app-owned Sidebar (Q22, v1.14.0): the shipped file and the after_migrate tidy-up."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import erpocr_integration
from erpocr_integration import install

APP = os.path.dirname(erpocr_integration.__file__)
SIDEBAR_FILE = os.path.join(APP, "erpnext_ocr", "sidebar", "erpnext_ocr", "erpnext_ocr.json")


class TestSidebarFile:
	@pytest.fixture(scope="class")
	def doc(self):
		with open(SIDEBAR_FILE) as f:
			return json.load(f)

	def test_identity(self, doc):
		assert doc["doctype"] == "Sidebar"
		assert doc["name"] == doc["title"] == "ERPNext OCR"  # get_module_shell: row named after the module
		assert doc["module"] == "ERPNext OCR"
		assert doc["app"] == "erpocr_integration"
		assert doc["standard"] == 1

	def test_items_are_workspace_and_doctype_only_no_url(self, doc):
		assert {i["link_type"] for i in doc["items"]} == {"Workspace", "DocType"}
		assert all(i["type"] == "Link" for i in doc["items"])
		assert not any(i.get("url") for i in doc["items"])

	def test_home_first_then_the_eight_doctypes(self, doc):
		items = doc["items"]
		assert (items[0]["label"], items[0]["link_type"], items[0]["link_to"]) == (
			"Home",
			"Workspace",
			"ERPNext OCR",
		)
		assert [i["link_to"] for i in items[1:]] == [
			"OCR Import",
			"OCR Delivery Note",
			"OCR Fleet Slip",
			"OCR Statement",
			"OCR Supplier Alias",
			"OCR Item Alias",
			"OCR Service Mapping",
			"OCR Settings",
		]

	def test_every_target_exists_in_the_app(self, doc):
		for item in doc["items"][1:]:
			folder = item["link_to"].lower().replace(" ", "_")
			assert os.path.exists(os.path.join(APP, "erpnext_ocr", "doctype", folder, f"{folder}.json"))
		with open(os.path.join(APP, "erpnext_ocr", "workspace", "erpnext_ocr", "erpnext_ocr.json")) as f:
			assert json.load(f)["label"] == "ERPNext OCR"

	def test_path_is_module_rooted_scrubbed(self):
		# <module folder>/sidebar/<scrub(name)>/<scrub(name)>.json, as v16 sync_for walks it
		assert os.path.exists(SIDEBAR_FILE)
		assert os.path.exists(os.path.join(APP, "modules.txt"))
		assert open(os.path.join(APP, "modules.txt")).read().strip() == "ERPNext OCR"


@pytest.fixture
def db(mock_frappe):
	"""A tiny fake of the rows the tidy-up reads and writes."""
	rows = {
		("DocType", "Sidebar"): True,
		("Sidebar", "ERPNext OCR"): {"standard": 1, "app": "erpocr_integration", "module": "ERPNext OCR"},
		("Sidebar", "ERPNext OCR (Custom)"): {"standard": 0, "app": None, "module": "ERPNext OCR (Custom)"},
		("Module Def", "ERPNext OCR (Custom)"): {"custom": 1},
	}
	content = set()  # (doctype, module) pairs that hold something
	state = SimpleNamespace(rows=rows, content=content, sidebars_in_ghost=["ERPNext OCR (Custom)"])

	def exists(doctype, name=None):
		if isinstance(name, dict):
			return (doctype, name.get("module")) in content
		return bool(rows.get((doctype, name)))

	def get_value(doctype, name, fields, as_dict=False):
		row = rows.get((doctype, name))
		return dict(row) if row else None

	def set_value(doctype, name, values, **kw):
		rows[(doctype, name)].update(values)

	saved = {k: getattr(mock_frappe.db, k) for k in ("exists", "get_value", "set_value")}
	mock_frappe.db.exists = MagicMock(side_effect=exists)
	mock_frappe.db.get_value = MagicMock(side_effect=get_value)
	mock_frappe.db.set_value = MagicMock(side_effect=set_value)
	saved_get_all, saved_delete = mock_frappe.get_all, mock_frappe.delete_doc
	mock_frappe.get_all = MagicMock(
		side_effect=lambda dt, **kw: [n for n in state.sidebars_in_ghost if n != kw["filters"]["name"][1]]
	)
	mock_frappe.delete_doc = MagicMock()
	yield state
	for k, v in saved.items():
		setattr(mock_frappe.db, k, v)
	mock_frappe.get_all, mock_frappe.delete_doc = saved_get_all, saved_delete


class TestHeal:
	def test_noop_without_sidebar_doctype(self, mock_frappe, db):
		db.rows[("DocType", "Sidebar")] = False
		db.rows[("Sidebar", "ERPNext OCR (Custom)")]["standard"] = 1
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()
		mock_frappe.db.set_value.assert_not_called()
		assert db.rows[("Sidebar", "ERPNext OCR (Custom)")]["standard"] == 1

	def test_restores_standard_app_and_module_on_our_row(self, mock_frappe, db):
		db.rows[("Sidebar", "ERPNext OCR")].update({"standard": 0, "app": None})
		install.heal_v16_sidebar()
		assert db.rows[("Sidebar", "ERPNext OCR")] == {
			"standard": 1,
			"app": "erpocr_integration",
			"module": "ERPNext OCR",
		}

	def test_leaves_a_healthy_row_alone(self, mock_frappe, db):
		install._restore_standard_sidebar()
		mock_frappe.db.set_value.assert_not_called()

	def test_never_creates_the_row(self, mock_frappe, db):
		del db.rows[("Sidebar", "ERPNext OCR")]
		install.heal_v16_sidebar()
		assert ("Sidebar", "ERPNext OCR") not in db.rows

	def test_neutralises_converted_sidebar(self, mock_frappe, db):
		db.rows[("Sidebar", "ERPNext OCR (Custom)")].update({"standard": 1, "app": "erpocr_integration"})
		db.content.add(("Workspace", "ERPNext OCR (Custom)"))  # keeps the ghost module alive
		install.heal_v16_sidebar()
		row = db.rows[("Sidebar", "ERPNext OCR (Custom)")]
		assert row["standard"] == 0 and row["app"] is None
		mock_frappe.delete_doc.assert_not_called()

	def test_deletes_empty_custom_ghost_module(self, mock_frappe, db):
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_called_once_with(
			"Module Def", "ERPNext OCR (Custom)", force=True, ignore_permissions=True
		)

	@pytest.mark.parametrize("doctype", ["Workspace", "DocType", "Report", "Page"])
	def test_keeps_ghost_module_that_holds_content(self, mock_frappe, db, doctype):
		db.content.add((doctype, "ERPNext OCR (Custom)"))
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()

	def test_keeps_ghost_module_that_is_not_custom(self, mock_frappe, db):
		db.rows[("Module Def", "ERPNext OCR (Custom)")]["custom"] = 0
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()

	def test_keeps_ghost_module_holding_another_sidebar(self, mock_frappe, db):
		db.sidebars_in_ghost = ["ERPNext OCR (Custom)"]
		mock_frappe.get_all = MagicMock(return_value=["Someone Elses"])
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()

	def test_keeps_ghost_module_holding_a_custom_sidebar(self, mock_frappe, db):
		db.rows[("DocType", "Custom Sidebar")] = True
		db.content.add(("Custom Sidebar", "ERPNext OCR (Custom)"))
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()

	def test_custom_sidebar_doctype_absent_does_not_block(self, mock_frappe, db):
		install.heal_v16_sidebar()  # no Custom Sidebar doctype row in the fake
		mock_frappe.delete_doc.assert_called_once()

	def test_no_ghost_module_is_fine(self, mock_frappe, db):
		del db.rows[("Module Def", "ERPNext OCR (Custom)")]
		install.heal_v16_sidebar()
		mock_frappe.delete_doc.assert_not_called()

	def test_second_run_changes_nothing(self, mock_frappe, db):
		install.heal_v16_sidebar()
		del db.rows[("Module Def", "ERPNext OCR (Custom)")]  # first run deleted it
		mock_frappe.db.set_value.reset_mock()
		mock_frappe.delete_doc.reset_mock()
		install.heal_v16_sidebar()
		mock_frappe.db.set_value.assert_not_called()
		mock_frappe.delete_doc.assert_not_called()

	def test_failure_is_logged_not_raised(self, mock_frappe, db):
		mock_frappe.delete_doc = MagicMock(side_effect=RuntimeError("boom"))
		install.heal_v16_sidebar()
		assert mock_frappe.log_error.called

	def test_after_migrate_runs_the_tidy_up(self, mock_frappe):
		from unittest.mock import patch

		with (
			patch.object(install, "heal_v16_sidebar") as heal,
			patch.object(install, "_seed_roles"),
			patch.object(install, "setup_custom_fields"),
			patch.object(install, "setup_optional_custom_fields"),
		):
			install.after_migrate()
		heal.assert_called_once()
