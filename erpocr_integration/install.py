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
	setup_optional_custom_fields()


def after_migrate() -> None:
	"""Hook target: `after_migrate` in hooks.py.

	Idempotent — safe to run on every migrate.
	"""
	setup_optional_custom_fields()


def setup_optional_custom_fields() -> None:
	"""Install Custom Fields whose targets are owned by optional sibling apps.

	Each block is gated on the target doctype existing. Re-runnable; the underlying
	`create_custom_fields` call is itself idempotent (updates existing fields in
	place, never duplicates).
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
			},
			ignore_validate=True,
		)
