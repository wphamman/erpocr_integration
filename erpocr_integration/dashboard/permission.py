import frappe


def has_app_permission() -> bool:
	"""Gate the /apps launcher tile + the SPA route at /accounts.

	The dashboard surfaces OCR review queues across OCR Import, OCR Delivery
	Note, and OCR Fleet Slip. Anyone with read on any of the three sees the
	tile. System Managers always pass. This is a UI gate — the authoritative
	check is Frappe's per-doctype permission on every API call the SPA makes
	(get_list/get_count run as the logged-in user).
	"""
	if frappe.session.user == "Administrator":
		return True
	if "System Manager" in frappe.get_roles():
		return True
	for doctype in ("OCR Import", "OCR Delivery Note", "OCR Fleet Slip"):
		if frappe.has_permission(doctype, "read"):
			return True
	return False
