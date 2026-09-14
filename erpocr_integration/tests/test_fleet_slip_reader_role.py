"""Scope test for the OCR Fleet Slip Reader role.

Guards against the most likely regression: someone adding this role to another
doctype's permissions block and quietly widening its read scope. The role must
appear in exactly two places in the repo — the Role fixture and the OCR Fleet
Slip doctype's permissions array — and must never grant create/delete/submit
anywhere. Write is granted on OCR Fleet Slip so the reader can resolve
"Needs Review" records (correct vehicle match, mark No Action) without being
able to create new fleet slips or delete existing ones.
"""

import glob
import json
import os

from erpocr_integration import hooks
from erpocr_integration.install import _SEED_ROLES

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROLE_NAME = "OCR Fleet Slip Reader"


def _load_json(path):
	with open(path) as f:
		return json.load(f)


def _doctype_jsons():
	"""All DocType JSON files shipped by this app."""
	pattern = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "*", "*.json")
	return [p for p in glob.glob(pattern) if not p.endswith(("_list.json", "_dashboard.json"))]


def test_role_seeded_create_only_not_fixtured():
	"""v1.10.4: role is app-owned + seeded create-only, not fixture-delivered —
	a Role fixture is deleted and re-inserted on every sibling app's migrate,
	silently reverting operator edits (e.g. disabling desk access)."""
	assert not os.path.exists(os.path.join(APP_ROOT, "fixtures", "role.json"))
	entry = next((r for r in _SEED_ROLES if r.get("role_name") == ROLE_NAME), None)
	assert entry is not None, f"{ROLE_NAME} missing from install._SEED_ROLES"
	assert entry.get("desk_access") == 1
	assert entry.get("disabled") == 0


def test_hooks_fixtures_do_not_include_role():
	"""hooks.py must no longer export a Role fixture (v1.10.4) — roles are
	seeded create-only by install._seed_roles instead."""
	dts = {f.get("dt") if isinstance(f, dict) else f for f in hooks.fixtures}
	assert "Role" not in dts


def test_fleet_slip_grants_read_write_to_role():
	"""OCR Fleet Slip must grant read + write (no create/delete/submit) to this role.

	Write is needed so the reader can resolve "Needs Review" records — correct
	a misread vehicle plate, mark a non-fleet slip as No Action. Create / delete
	stay zero so the reader can't fabricate or destroy fleet slips.
	"""
	path = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "ocr_fleet_slip", "ocr_fleet_slip.json")
	fleet = _load_json(path)
	perms = [p for p in fleet.get("permissions", []) if p.get("role") == ROLE_NAME]
	assert len(perms) == 1, f"Expected exactly one perm row for {ROLE_NAME}, got {len(perms)}"
	perm = perms[0]
	assert perm.get("read") == 1
	assert perm.get("write") == 1
	assert perm.get("report") == 1
	assert perm.get("print") == 1
	# Forbidden surface must all be zero
	for field in ("create", "delete", "submit", "cancel", "amend"):
		assert perm.get(field, 0) == 0, f"{ROLE_NAME} must not have {field}=1 on OCR Fleet Slip"
	# Data-exfiltration surface must all be zero
	assert perm.get("export", 0) == 0
	assert perm.get("email", 0) == 0
	assert perm.get("share", 0) == 0
	assert perm.get("permlevel", 0) == 0


def test_role_not_present_on_any_other_doctype():
	"""Role must NOT appear in any OCR doctype permissions block other than OCR Fleet Slip.

	This is the critical scope-creep guard: accidentally adding the role to
	OCR Statement / OCR Supplier Alias / OCR Delivery Note / etc. would leak
	supplier data that Simone must not see.
	"""
	target = os.path.join("ocr_fleet_slip", "ocr_fleet_slip.json")
	leaked = []
	for path in _doctype_jsons():
		if path.endswith(target):
			continue
		doc = _load_json(path)
		for perm in doc.get("permissions") or []:
			if perm.get("role") == ROLE_NAME:
				leaked.append(os.path.relpath(path, APP_ROOT))
	assert not leaked, f"{ROLE_NAME} must only appear on OCR Fleet Slip but was also found on: {leaked}"


def test_existing_system_manager_perms_unchanged():
	"""Regression guard: the System Manager rows on OCR Fleet Slip must keep full access."""
	path = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "ocr_fleet_slip", "ocr_fleet_slip.json")
	fleet = _load_json(path)
	sys_mgr_perms = [p for p in fleet.get("permissions", []) if p.get("role") == "System Manager"]
	assert len(sys_mgr_perms) >= 1
	top = next((p for p in sys_mgr_perms if p.get("permlevel", 0) == 0), None)
	assert top is not None
	for field in ("read", "write", "create", "delete", "report", "print", "email", "export", "share"):
		assert top.get(field) == 1, f"System Manager permlevel-0 regressed on field {field}"
