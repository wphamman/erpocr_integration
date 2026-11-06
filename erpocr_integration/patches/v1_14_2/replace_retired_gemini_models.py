"""Google retires gemini-2.5-flash on 2026-12-01 (auto-redirect to gemini-3.5-flash-lite);
gemini-2.0-flash was shut down on 2026-06-01 and gemini-2.5-pro redirects to a newer model.
Move a site still set to one of them onto its chosen replacement, so the model in use is the
one we tested and the dropdown can drop the retired names. Any other choice is left alone."""

import frappe

_REPLACEMENTS = {
	"gemini-2.5-flash": "gemini-3.5-flash-lite",
	"gemini-2.0-flash": "gemini-3.5-flash-lite",
	"gemini-2.5-pro": "gemini-3.6-flash",
}


def execute() -> None:
	current = frappe.db.get_single_value("OCR Settings", "gemini_model")
	if current in _REPLACEMENTS:
		frappe.db.set_single_value("OCR Settings", "gemini_model", _REPLACEMENTS[current])
