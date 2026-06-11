"""Scope test for the OCR Fleet Driver role (P4 upload contract).

The driver shell authenticates drivers who upload fleet slips via
erpocr_integration.fleet_api.upload_fleet_slip. The role must grant CREATE on
OCR Fleet Slip ONLY (so a driver can never open the invoice / OCR Import
surface), with reads scoped by if_owner (a driver cannot read other drivers'
slips). It must NOT grant write/delete/submit/export/email/share anywhere, and
must appear on no doctype other than OCR Fleet Slip. Guards against the most
likely regressions: scope creep onto another doctype, or accidentally widening
the driver's surface.
"""

import glob
import json
import os

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROLE_NAME = "OCR Fleet Driver"


def _load_json(path):
	with open(path) as f:
		return json.load(f)


def _doctype_jsons():
	pattern = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "*", "*.json")
	return [p for p in glob.glob(pattern) if not p.endswith(("_list.json", "_dashboard.json"))]


def test_role_fixture_exists():
	"""Role fixture must define OCR Fleet Driver. desk_access=0 — drivers use the
	shell SPA + the whitelisted API, never the ERPNext Desk."""
	roles = _load_json(os.path.join(APP_ROOT, "fixtures", "role.json"))
	entry = next((r for r in roles if r.get("role_name") == ROLE_NAME), None)
	assert entry is not None, f"{ROLE_NAME} missing from fixtures/role.json"
	assert entry.get("doctype") == "Role"
	assert entry.get("disabled") == 0
	assert entry.get("desk_access") == 0, "driver role must not have Desk access"


def test_hooks_fixtures_include_role():
	"""hooks.py must export this role in its Role fixture filter."""
	with open(os.path.join(APP_ROOT, "hooks.py")) as f:
		content = f.read()
	assert ROLE_NAME in content, f"{ROLE_NAME} must appear in hooks.py fixtures filter"


def test_fleet_slip_grants_create_and_if_owner_read_only():
	"""OCR Fleet Slip must grant create + if_owner read to the driver — nothing else.

	create=1 lets the upload contract pass its has_permission("OCR Fleet Slip",
	"create") gate; if_owner read lets a driver read back their own slip and no
	one else's. write/delete/submit stay zero so a driver can neither edit/destroy
	slips nor reach the accounting review surface.
	"""
	path = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "ocr_fleet_slip", "ocr_fleet_slip.json")
	fleet = _load_json(path)
	perms = [p for p in fleet.get("permissions", []) if p.get("role") == ROLE_NAME]
	assert len(perms) == 1, f"Expected exactly one perm row for {ROLE_NAME}, got {len(perms)}"
	perm = perms[0]
	assert perm.get("create") == 1
	assert perm.get("read") == 1
	assert perm.get("if_owner") == 1, "driver reads must be if_owner-scoped"
	# Forbidden surface must all be zero — the driver is create-only.
	for field in ("write", "delete", "submit", "cancel", "amend", "report", "print"):
		assert perm.get(field, 0) == 0, f"{ROLE_NAME} must not have {field}=1 on OCR Fleet Slip"
	# Data-exfiltration surface must all be zero.
	assert perm.get("export", 0) == 0
	assert perm.get("email", 0) == 0
	assert perm.get("share", 0) == 0
	assert perm.get("permlevel", 0) == 0


def test_role_not_present_on_any_other_doctype():
	"""Critical scope-creep guard: the driver role must touch ONLY OCR Fleet Slip.

	Appearing on OCR Import (the invoice surface) would defeat the entire point of
	the contract — the driver could open the accounting path.
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


def test_does_not_disturb_reader_or_manager_rows():
	"""Regression: the existing OCR Manager + Reader rows on OCR Fleet Slip stay intact."""
	path = os.path.join(APP_ROOT, "erpnext_ocr", "doctype", "ocr_fleet_slip", "ocr_fleet_slip.json")
	fleet = _load_json(path)
	roles = {p.get("role") for p in fleet.get("permissions", [])}
	assert {"System Manager", "OCR Manager", "OCR Fleet Slip Reader", ROLE_NAME} <= roles
	manager = next(
		(p for p in fleet["permissions"] if p.get("role") == "OCR Manager" and p.get("permlevel", 0) == 0),
		None,
	)
	assert manager is not None and manager.get("create") == 1 and manager.get("read") == 1
