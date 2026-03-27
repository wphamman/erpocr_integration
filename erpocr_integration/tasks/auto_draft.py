"""Auto-draft logic for high-confidence OCR Imports.

When extraction + matching produces high-confidence results (alias/exact matches,
not fuzzy), automatically creates the PI/PR draft — eliminating the manual
"review and click Create" ceremony.
"""

import frappe

# High-confidence match statuses (NOT "Suggested" or "Unmatched")
_HIGH_CONFIDENCE_STATUSES = frozenset({"Auto Matched", "Confirmed"})


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
		ocr_import.auto_draft_skipped_reason = reason
		return False

	# Gate on "Matched" status — _update_status() already validates that
	# non-stock items have expense_account, etc. If the record didn't reach
	# "Matched" after save, it needs human attention even if matches look good.
	if ocr_import.status != "Matched":
		ocr_import.auto_draft_skipped_reason = f"Status is '{ocr_import.status}' (requires 'Matched')"
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
