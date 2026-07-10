"""Auto-draft logic for high-confidence OCR Imports.

When extraction + matching produces high-confidence results (alias/exact matches,
not fuzzy), automatically creates the PI/PR draft — eliminating the manual
"review and click Create" ceremony.
"""

import frappe
from frappe.utils import flt

# High-confidence match statuses (NOT "Suggested" or "Unmatched")
_HIGH_CONFIDENCE_STATUSES = frozenset({"Auto Matched", "Confirmed"})

# Totals-reconciliation gate (Q11, v1.9.0). A PI's amounts build from qty x rate,
# so a globally-discounted invoice — where Gemini captured the discount in the
# extracted subtotal but NOT in the per-line rates (the schema has no discount
# field) — systematically over-drafts (live specimen: OCR-IMP-01918 auto-drafted
# R2,654.98 against a R2,522.22 invoice). Skip auto-draft when Σ(qty x rate)
# deviates from the extracted subtotal beyond tolerance and route to human review.
# Tolerance is max(relative %, absolute floor): the % catches ~1%+ discounts, the
# absolute floor absorbs multi-line rounding noise. Module constants, not a
# setting — no evidence a per-site knob is needed (revisit if one instance's
# extractions prove noisier).
_TOTALS_TOLERANCE_PCT = 0.01  # 1% of the effective subtotal
_TOTALS_TOLERANCE_ABS = 1.00  # absolute floor (rounding), in document currency


def _totals_reconcile(ocr_import) -> tuple[bool, str]:
	"""Verify the extracted line rates reconcile with the extracted subtotal.

	The PI draft posts Σ(qty x rate); if that disagrees with what the invoice
	says its pre-tax subtotal is (an unmodelled global discount, or an extraction
	error), the auto-draft would be numerically wrong. Compare the two and skip
	auto-draft — bidirectionally (over- OR under-draft) — when they diverge past
	tolerance. Manual creation is untouched: this gates AUTO-draft only.

	Tax-inclusive rates (a first-class path): when the extracted rates already
	include tax, Σ(qty*rate) reconciles against the tax-INCLUSIVE total, so the
	reference is `total_amount`, not `subtotal` — otherwise every inclusive invoice
	false-fails by the tax amount.

	Degenerate cases fall through as PASS (can't validate → don't block; the other
	gates still apply and the human review path is unchanged):
	  - extracted subtotal absent/0 (exclusive path) → fall back to (total - tax);
	  - that fallback also ≤ 0, or the line sum ≤ 0 → unverifiable, pass.

	Returns:
	    (reconciles, reason_if_not)
	"""
	line_sum = sum(flt(item.qty or 1) * flt(item.rate or 0) for item in ocr_import.items)
	if line_sum <= 0:
		return True, ""  # unverifiable — no positive line total to compare

	# Pick the reference the line rates should reconcile against. If the rates
	# already INCLUDE tax (a first-class path — see _detect_tax_inclusive_rates,
	# which the PI builder honours via included_in_print_rate), Σ(qty*rate) matches
	# the tax-INCLUSIVE total, not the pre-tax subtotal. Comparing an inclusive
	# line sum against the subtotal would false-fail every inclusive invoice by
	# the full tax amount and silently disable auto-draft for that whole class.
	# For the discount specimen (OCR-IMP-01918) the detector returns False
	# (exclusive), so the discount is still caught against the subtotal.
	from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
		_detect_tax_inclusive_rates,
	)

	if _detect_tax_inclusive_rates(ocr_import):
		reference = flt(ocr_import.total_amount)
		ref_label = "total"
	else:
		reference = flt(ocr_import.subtotal)
		ref_label = "subtotal"
		if reference <= 0:
			# Gemini emits subtotal "0 if not shown" — fall back to the pre-tax total.
			reference = flt(ocr_import.total_amount) - flt(ocr_import.tax_amount)
	if reference <= 0:
		return True, ""  # no usable reference — can't validate

	tolerance = max(_TOTALS_TOLERANCE_ABS, _TOTALS_TOLERANCE_PCT * reference)
	if abs(line_sum - reference) <= tolerance:
		return True, ""

	currency = (getattr(ocr_import, "currency", None) or "").strip()
	prefix = f"{currency} " if currency else ""
	return (
		False,
		f"Line total {prefix}{line_sum:,.2f} ≠ extracted {ref_label} {prefix}{reference:,.2f} "
		f"— possible invoice discount or extraction error; needs review",
	)


def _is_high_confidence(ocr_import) -> tuple[bool, str]:
	"""Check if an OCR Import has high-confidence matches suitable for auto-draft.

	Returns:
	    (is_high_confidence, reason_if_not)
	"""
	# Supplier must be resolved with high confidence
	if not ocr_import.supplier:
		return False, "No supplier matched"
	if ocr_import.supplier_match_status not in _HIGH_CONFIDENCE_STATUSES:
		return False, f"Supplier match is '{ocr_import.supplier_match_status}' (needs alias or exact)"

	# Must have at least one item
	if not ocr_import.items:
		return False, "No items extracted"

	# All items must be high-confidence matched
	for item in ocr_import.items:
		if item.match_status not in _HIGH_CONFIDENCE_STATUSES:
			return False, f"Item '{item.description_ocr or '?'}' match is '{item.match_status}'"
		if not item.item_code:
			return False, f"Item '{item.description_ocr or '?'}' has no item_code"

	return True, ""


def _auto_link_purchase_order(ocr_import) -> bool:
	"""Attempt to find and link an open PO for this OCR Import.

	Searches open POs by supplier + company, picks the one where all OCR item_codes
	appear in PO items. Sets `ocr_import.purchase_order` if found.

	Returns:
	    True if a PO was linked (or already linked), False otherwise.
	"""
	if ocr_import.purchase_order:
		return True  # Already linked

	if not ocr_import.supplier or not ocr_import.company:
		return False

	ocr_item_codes = {item.item_code for item in ocr_import.items if item.item_code}
	if not ocr_item_codes:
		return False

	# Find open POs for this supplier
	open_pos = frappe.get_list(
		"Purchase Order",
		filters={
			"supplier": ocr_import.supplier,
			"company": ocr_import.company,
			"docstatus": 1,
			"status": ["in", ["To Receive and Bill", "To Receive", "To Bill"]],
		},
		fields=["name", "transaction_date", "grand_total", "status"],
		order_by="transaction_date desc",
		limit_page_length=20,
		ignore_permissions=True,
	)

	if not open_pos:
		return False

	# Find PO where all OCR items have matching PO items
	best_po = None
	for po in open_pos:
		po_doc = frappe.get_doc("Purchase Order", po.name)
		po_item_codes = {item.item_code for item in po_doc.items}

		if ocr_item_codes.issubset(po_item_codes):
			best_po = po.name
			break  # First full match wins (most recent due to ordering)

	if best_po:
		ocr_import.purchase_order = best_po
		return True

	return False


def _auto_detect_document_type(ocr_import) -> str:
	"""Auto-detect the appropriate document type for this OCR Import.

	Current logic: always returns Purchase Invoice. PI is the safest default
	because it accepts unmatched items via default_item, doesn't require
	warehouse config, and is the most common document type.

	Future: could detect PR (all stock items + PO) or JE (expense receipts).
	"""
	return "Purchase Invoice"


def _invoice_date_in_fiscal_year(ocr_import) -> tuple[bool, str]:
	"""Verify the OCR invoice_date falls in an active Fiscal Year for the company.

	A Gemini date misread (e.g. year 2001 for 2026) makes create_purchase_invoice
	fail deep in ERPNext's Fiscal Year validation ("Date ... is not in any active
	Fiscal Year"). Catch it here so the record is cleanly routed to human review with
	a clear reason, instead of firing a doomed create that only surfaces as an Error
	Log entry. An empty invoice_date is fine — create falls back to today().
	"""
	invoice_date = getattr(ocr_import, "invoice_date", None)
	if not invoice_date:
		return True, ""

	# get_fiscal_year lives in erpnext.accounts.utils, NOT frappe.utils. The
	# original call (frappe.utils.get_fiscal_year) raised AttributeError on
	# every invocation, and the blanket except turned that into "outside any
	# active Fiscal Year" — silently blocking EVERY gate-passing auto-draft on
	# prod while valid Fiscal Years existed. The mocked test suite couldn't
	# catch it (a MagicMock attribute never raises); verified on a real bench.
	# Import separately so a missing function/module can never masquerade as
	# a fiscal-year rejection again.
	try:
		from erpnext.accounts.utils import get_fiscal_year
	except ImportError:
		# No ERPNext (not a real site shape — the app hard-links ERPNext
		# doctypes). Let create_purchase_invoice surface any FY problem.
		return True, ""

	try:
		get_fiscal_year(invoice_date, company=getattr(ocr_import, "company", None), verbose=0)
		return True, ""
	except Exception:
		return (
			False,
			f"Invoice date {invoice_date} is outside any active Fiscal Year "
			f"(likely an OCR misread) — needs review",
		)


def attempt_auto_draft(ocr_import, settings) -> bool:
	"""Attempt to auto-draft a document from a high-confidence OCR Import.

	Called after matching completes in gemini_process(). If confidence is high,
	sets document_type, links PO if possible, saves, and calls the create method.
	Falls back gracefully on any error — the record stays at its current status
	(Matched/Needs Review) and the user can handle it manually.

	Returns:
	    True if auto-draft succeeded, False otherwise.
	"""
	if not getattr(settings, "enable_auto_draft", 0):
		return False

	# Don't auto-draft if a document already exists
	if (
		getattr(ocr_import, "purchase_invoice", None)
		or getattr(ocr_import, "purchase_receipt", None)
		or getattr(ocr_import, "journal_entry", None)
	):
		return False

	# Check confidence
	is_high, reason = _is_high_confidence(ocr_import)
	if not is_high:
		frappe.db.set_value("OCR Import", ocr_import.name, "auto_draft_skipped_reason", reason)
		return False

	# Gate on "Matched" status — _update_status() already validates that
	# non-stock items have expense_account, etc. If the record didn't reach
	# "Matched" after save, it needs human attention even if matches look good.
	if ocr_import.status != "Matched":
		reason = f"Status is '{ocr_import.status}' (requires 'Matched')"
		frappe.db.set_value("OCR Import", ocr_import.name, "auto_draft_skipped_reason", reason)
		return False

	# Guard against Gemini date misreads (e.g. 2001 for 2026): such a date fails
	# create_purchase_invoice deep in ERPNext's Fiscal Year validation. Skip cleanly
	# to human review instead of firing a doomed create.
	date_ok, date_reason = _invoice_date_in_fiscal_year(ocr_import)
	if not date_ok:
		frappe.db.set_value("OCR Import", ocr_import.name, "auto_draft_skipped_reason", date_reason)
		return False

	# Totals-reconciliation gate (Q11): a PI builds from qty x rate, so a global
	# discount that Gemini folded into the subtotal but not the line rates would
	# auto-draft the wrong amount. Skip to human review when the two disagree.
	totals_ok, totals_reason = _totals_reconcile(ocr_import)
	if not totals_ok:
		frappe.db.set_value("OCR Import", ocr_import.name, "auto_draft_skipped_reason", totals_reason)
		return False

	try:
		# Auto-link PO if possible (sets ocr_import.purchase_order)
		_auto_link_purchase_order(ocr_import)

		# Auto-detect document type
		doc_type = _auto_detect_document_type(ocr_import)
		ocr_import.document_type = doc_type
		ocr_import.auto_drafted = 1

		# Save to persist document_type + PO link + auto_drafted flag
		# (create_purchase_invoice checks these fields)
		ocr_import.save(ignore_permissions=True)

		# Create the document
		if doc_type == "Purchase Invoice":
			ocr_import.create_purchase_invoice()
		elif doc_type == "Purchase Receipt":
			ocr_import.create_purchase_receipt()

		return True

	except Exception as e:
		# Fall back gracefully — record stays at Matched/Needs Review
		ocr_import.auto_draft_skipped_reason = f"Auto-draft failed: {e}"
		ocr_import.document_type = ""
		ocr_import.auto_drafted = 0
		try:
			ocr_import.save(ignore_permissions=True)
		except Exception:
			pass  # Best-effort — don't mask the original error
		frappe.log_error(
			title="Auto-Draft Failed",
			message=f"Auto-draft failed for {ocr_import.name}: {e}\n{frappe.get_traceback()}",
		)
		return False
