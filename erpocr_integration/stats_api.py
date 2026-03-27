"""OCR stats API — aggregation endpoint for the stats dashboard.

Role-gated to System Manager. Not visible to regular accounts users.
"""

import frappe
from frappe import _


@frappe.whitelist()
def get_ocr_stats(from_date=None, to_date=None):
	"""Return OCR processing statistics for the stats dashboard.

	Args:
	    from_date: Start date filter (default: 90 days ago)
	    to_date: End date filter (default: today)
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can view OCR stats."))

	if not from_date:
		from_date = frappe.utils.add_days(frappe.utils.today(), -90)
	if not to_date:
		to_date = frappe.utils.today()

	records = frappe.get_all(
		"OCR Import",
		filters={"creation": ["between", [from_date, to_date]]},
		fields=[
			"name",
			"status",
			"auto_drafted",
			"source_type",
			"supplier",
			"supplier_match_status",
			"creation",
			"auto_draft_skipped_reason",
		],
		limit_page_length=0,
		ignore_permissions=True,
	)

	stats = _compute_stats(records)
	stats["from_date"] = str(from_date)
	stats["to_date"] = str(to_date)
	return stats


def _compute_stats(records: list[dict]) -> dict:
	"""Compute aggregate stats from a list of OCR Import records."""
	total = len(records)
	if total == 0:
		return {
			"total": 0,
			"touchless_draft_rate": 0.0,
			"exception_rate": 0.0,
			"by_status": {},
			"by_source": {},
			"auto_drafted_count": 0,
			"manual_count": 0,
		}

	auto_drafted = sum(1 for r in records if r.get("auto_drafted"))
	# Exception = anything that needs/needed manual intervention
	# (Needs Review, Matched without auto_drafted, Error)
	exceptions = sum(
		1
		for r in records
		if not r.get("auto_drafted") and r.get("status") in ("Needs Review", "Matched", "Error")
	)

	by_status = {}
	by_source = {}
	for r in records:
		status = r.get("status", "Unknown")
		by_status[status] = by_status.get(status, 0) + 1
		source = r.get("source_type", "Unknown")
		by_source[source] = by_source.get(source, 0) + 1

	return {
		"total": total,
		"touchless_draft_rate": round(auto_drafted / total * 100, 1) if total else 0.0,
		"exception_rate": round(exceptions / total * 100, 1) if total else 0.0,
		"by_status": by_status,
		"by_source": by_source,
		"auto_drafted_count": auto_drafted,
		"manual_count": total - auto_drafted,
	}
