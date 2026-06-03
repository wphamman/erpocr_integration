"""Migrate the `fleet_vehicle` field on OCR Import from a hard DocType-JSON Link
to a conditionally-installed Custom Field.

v1.1.5 shipped `fleet_vehicle` as a Link → Fleet Vehicle field declared directly
in `ocr_import.json`. On sites without `fleet_management` installed, meta
resolution for OCR Import (triggered eagerly by workspace number cards) crashed
with "Field fleet_vehicle is referring to non-existing doctype Fleet Vehicle",
breaking the OCR workspace.

This patch:
  1. Cleans up any Property Setter created as a stopgap workaround that pointed
     the field's options at a different doctype (the documented manual fix for
     v1.1.5 — e.g. operators redirecting options to "User" to suppress the
     workspace crash).
  2. Re-runs the conditional install hook so sites with Fleet Vehicle get
     `fleet_vehicle` back as a proper Custom Field (idempotent; preserves
     existing column data).

The orphan DocField record (if any lingers from the v1.1.5 install) is removed
by Frappe's standard JSON-sync during `bench migrate` — no manual cleanup
needed here.

Sites without `fleet_management` end up with the field absent everywhere
(JSON, DocField, Custom Field) — restoring the soft-dependency promise.
"""

from __future__ import annotations

import frappe

from erpocr_integration.install import setup_optional_custom_fields


def execute() -> None:
	_clear_stopgap_property_setters()
	setup_optional_custom_fields()


def _clear_stopgap_property_setters() -> None:
	"""Remove Property Setter rows that overrode `fleet_vehicle.options` on
	OCR Import or OCR Fleet Slip.

	These could only have been added by an operator working around the v1.1.5
	bug. Anything matching the (doc_type, field_name, property) tuple is in
	scope. We leave Property Setters on other properties alone — those would
	be intentional customisations.
	"""
	stale = frappe.get_all(
		"Property Setter",
		filters={
			"doc_type": ("in", ("OCR Import", "OCR Fleet Slip")),
			"field_name": "fleet_vehicle",
			"property": "options",
		},
		pluck="name",
	)
	for ps_name in stale:
		frappe.delete_doc("Property Setter", ps_name, ignore_permissions=True, force=True)
