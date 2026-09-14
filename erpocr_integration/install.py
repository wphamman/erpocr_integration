"""Install + migrate-time setup for erpocr_integration.

Conditionally provisions Custom Fields that reference doctypes owned by *optional*
sibling apps. Today the only one is `Fleet Vehicle` (from fleet_management) — but
the pattern is here for future optional integrations.

Why this lives in code and not in fixtures:
    fixtures get loaded unconditionally on install/migrate. A Custom Field whose
    `options` points at "Fleet Vehicle" raises a meta-resolution error on sites
    that don't have fleet_management installed (see v1.1.5 → v1.1.6 hotfix). Code-
    driven install lets us check first and skip cleanly.

Also seeds the app-owned OCR roles create-only (`_seed_roles`, v1.10.4) — see
that function's docstring for why a Role fixture was the wrong tool.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# App-owned roles (was `fixtures/role.json` until v1.10.4). A Role fixture is
# DELETED AND RE-INSERTED on every migrate (`import_doc` -> `delete_old_doc`),
# and on Frappe Press every app's deploy migrates the whole site — so an
# operator disabling one of these roles, or flipping desk_access, was silently
# reverted by an unrelated sibling app's deploy (2026-09-10 portfolio audit:
# our 3 OCR roles' `creation` reset to a payroll app's migrate timestamp).
# Values match the retired fixture exactly.
_SEED_ROLES: tuple[dict, ...] = (
	{
		"role_name": "OCR Manager",
		"is_custom": 1,
		"desk_access": 1,
		"search_bar": 1,
		"notifications": 1,
	},
	{
		"role_name": "OCR Fleet Slip Reader",
		"is_custom": 0,
		"desk_access": 1,
		"disabled": 0,
	},
	{
		"role_name": "OCR Fleet Driver",
		"is_custom": 0,
		"desk_access": 0,
		"disabled": 0,
	},
)


def _seed_roles() -> None:
	"""Create the app-owned OCR roles if missing. CREATE-ONLY.

	An existing Role (whatever its desk_access / disabled / two_factor_auth /
	any other operator-tuned field) is never touched — no `.save()`, no field
	re-assert. This is deliberate: unlike `fixtures/role.json` (removed
	v1.10.4), which Frappe's `sync_fixtures` deletes and re-inserts on every
	migrate — including migrates triggered by *other* apps' deploys on shared
	Frappe Press hosts — this seed only ever fills a gap, never overwrites an
	operator's edit. See the 2026-09-10 portfolio audit (CLAUDE.md gotcha) and
	the `starpops_maintenance` v0.3.9 precedent this mirrors.
	"""
	for role in _SEED_ROLES:
		if frappe.db.exists("Role", role["role_name"]):
			continue
		frappe.get_doc({"doctype": "Role", **role}).insert(ignore_permissions=True)


def after_install() -> None:
	"""Hook target: `after_install` in hooks.py."""
	_seed_roles()
	setup_custom_fields()
	setup_optional_custom_fields()


def after_migrate() -> None:
	"""Hook target: `after_migrate` in hooks.py.

	Idempotent — safe to run on every migrate. `_seed_roles` runs first so the
	roles exist before anything (Custom DocPerm, a future grant) references
	them.
	"""
	_seed_roles()
	setup_custom_fields()
	setup_optional_custom_fields()


def setup_custom_fields() -> None:
	"""Install Custom Fields on core ERPNext doctypes (always present — no gating).

	Back-links from the created accounting documents to their OCR staging record,
	so a user on a Purchase Invoice can click through to the OCR source (raw
	extraction, match state, retry). Targets are core doctypes and the link
	target (OCR Import) is this app's own doctype, so no feature-detection is
	needed. Idempotent via create_custom_fields.
	"""
	backlink = {
		"fieldname": "custom_ocr_import",
		"label": "OCR Import",
		"fieldtype": "Link",
		"options": "OCR Import",
		"insert_after": "bill_no",
		"read_only": 1,
		"no_copy": 1,
		"description": "OCR staging record this document was created from.",
	}
	create_custom_fields(
		{
			"Purchase Invoice": [dict(backlink)],
			"Purchase Receipt": [dict(backlink, insert_after="supplier_delivery_note")],
			"Journal Entry": [dict(backlink, insert_after="cheque_no")],
		},
		ignore_validate=True,
	)


def setup_optional_custom_fields() -> None:
	"""Install Custom Fields whose targets are owned by optional sibling apps.

	Each block is gated on the target doctype existing. Re-runnable; the underlying
	`create_custom_fields` call is itself idempotent (updates existing fields in
	place, never duplicates).

	The Fleet Vehicle-parented fields below were previously shipped as fixtures
	(fixtures/custom_field.json) — the exact anti-pattern this module's docstring
	warns about: fixture sync runs unconditionally and inserts Custom Fields whose
	parent doctype ("Fleet Vehicle") doesn't exist on sites without fleet_management,
	breaking a standalone install. Moved here (gated) in the 2026-07 roadmap build.
	"""
	if frappe.db.exists("DocType", "Fleet Vehicle"):
		create_custom_fields(
			{
				"OCR Import": [
					{
						"fieldname": "fleet_vehicle",
						"label": "Fleet Vehicle (optional)",
						"fieldtype": "Link",
						"options": "Fleet Vehicle",
						"insert_after": "supplier",
						"description": (
							"Tag if this invoice is a vehicle-specific expense (repairs, tyres, "
							"service, etc.). Flows through to the Purchase Invoice on creation so "
							"it appears in per-vehicle cost reports."
						),
					}
				],
				"Fleet Vehicle": [
					{
						"fieldname": "custom_ocr_section",
						"label": "OCR Fleet Slip Settings",
						"fieldtype": "Section Break",
						"insert_after": "wesbank_cost_code",
						"collapsible": 1,
					},
					{
						"fieldname": "custom_fleet_card_provider",
						"label": "Fleet Card Provider",
						"fieldtype": "Link",
						"options": "Supplier",
						"insert_after": "custom_ocr_section",
						"description": (
							"Fleet card company (e.g., WesBank). If set, fleet slips create "
							"Purchase Invoices against this supplier."
						),
					},
					{
						"fieldname": "custom_fleet_control_account",
						"label": "Fleet Control Account",
						"fieldtype": "Link",
						"options": "Account",
						"insert_after": "custom_fleet_card_provider",
						"description": "Control/clearing account debited on fleet card Purchase Invoices",
					},
					{
						"fieldname": "custom_column_break_ocr",
						"fieldtype": "Column Break",
						"insert_after": "custom_fleet_control_account",
					},
					{
						"fieldname": "custom_cost_center",
						"label": "Cost Center",
						"fieldtype": "Link",
						"options": "Cost Center",
						"insert_after": "custom_column_break_ocr",
						"ignore_user_permissions": 1,
						"description": "Cost center for expense allocation on fleet slips for this vehicle",
					},
				],
			},
			ignore_validate=True,
		)
