"""Install + migrate-time setup for erpocr_integration.

Conditionally provisions Custom Fields that reference doctypes owned by *optional*
sibling apps. Today the only one is `Fleet Vehicle` (from fleet_management) — but
the pattern is here for future optional integrations.

Why this lives in code and not in fixtures:
    fixtures get loaded unconditionally on install/migrate. A Custom Field whose
    `options` points at "Fleet Vehicle" raises a meta-resolution error on sites
    that don't have fleet_management installed (see v1.1.5 → v1.1.6 hotfix). Code-
    driven install lets us check first and skip cleanly.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install() -> None:
	"""Hook target: `after_install` in hooks.py."""
	setup_custom_fields()
	setup_optional_custom_fields()


def after_migrate() -> None:
	"""Hook target: `after_migrate` in hooks.py.

	Idempotent — safe to run on every migrate.
	"""
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
						"description": "Cost center for expense allocation on fleet slips for this vehicle",
					},
				],
			},
			ignore_validate=True,
		)
