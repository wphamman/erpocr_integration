"""Q19: re-anchor Fleet Vehicle's `custom_ocr_section` from `wesbank_cost_code`
(removed by fleet_management 2026-03-06, so the section rendered inside fleet's
collapsed "Cartrack Raw Data") to `driver_name`. Never clobbers a hand-fix."""

import frappe

_NAME = "Fleet Vehicle-custom_ocr_section"


def execute() -> None:
	if not frappe.db.exists("DocType", "Fleet Vehicle"):
		return
	if frappe.db.get_value("Custom Field", _NAME, "insert_after") != "wesbank_cost_code":
		return
	frappe.db.set_value("Custom Field", _NAME, "insert_after", "driver_name")
	frappe.clear_cache(doctype="Fleet Vehicle")
