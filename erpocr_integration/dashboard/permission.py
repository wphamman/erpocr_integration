import frappe


def has_app_permission() -> bool:
	"""Hide/show the /apps launcher tile for the accounts dashboard.

	Wired only via `add_to_apps_screen[].has_permission` in hooks.py (Frappe has
	no top-level `has_app_permission` hook). Anyone with read on any of OCR
	Import / OCR Delivery Note / OCR Fleet Slip sees the tile; System Managers
	always pass. This gates the TILE only — the /accounts route is a public www
	shell; the authoritative check is Frappe's per-doctype permission on every
	get_list/get_count the SPA makes (run as the logged-in user).
	"""
	if frappe.session.user == "Administrator":
		return True
	if "System Manager" in frappe.get_roles():
		return True
	for doctype in ("OCR Import", "OCR Delivery Note", "OCR Fleet Slip"):
		if frappe.has_permission(doctype, "read"):
			return True
	return False
