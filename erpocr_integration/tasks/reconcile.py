"""Statement reconciliation logic.

Matches statement transaction lines against ERPNext Purchase Invoices,
then does a reverse check to find PIs not on the statement.
"""

import frappe

from erpocr_integration.tasks.matching import normalize_for_matching


def reconcile_statement(ocr_statement) -> None:
	"""Reconcile all transaction lines against ERPNext Purchase Invoices.

	Mutates ocr_statement.items in place — sets recon_status, matched_invoice,
	erp_amount, erp_outstanding, and difference on each item. Also adds
	reverse-check rows for PIs not appearing on the statement (only when
	period_from and period_to are both set).
	"""
	if not ocr_statement.supplier or not ocr_statement.company:
		return

	# Get all submitted PIs for this supplier
	pi_filters = {
		"supplier": ocr_statement.supplier,
		"company": ocr_statement.company,
		"docstatus": 1,
	}
	if ocr_statement.period_from and ocr_statement.period_to:
		pi_filters["posting_date"] = [
			"between",
			[ocr_statement.period_from, ocr_statement.period_to],
		]

	all_pis = frappe.get_all(
		"Purchase Invoice",
		filters=pi_filters,
		fields=["name", "bill_no", "grand_total", "outstanding_amount", "posting_date"],
		ignore_permissions=True,
		limit_page_length=0,
	)

	# Build lookup by normalized bill_no for fuzzy reference matching
	pi_by_normalized_ref = {}
	for pi in all_pis:
		if pi.get("bill_no"):
			normalized = normalize_for_matching(pi["bill_no"])
			pi_by_normalized_ref.setdefault(normalized, []).append(pi)

	# Track which PIs were matched (for reverse check)
	matched_pi_names = set()

	# Forward reconciliation: match each statement line to a PI
	for item in ocr_statement.items:
		# Credit lines are payments — mark and skip
		if (item.credit or 0) > 0 and (item.debit or 0) == 0:
			item.recon_status = "Payment"
			continue

		# Debit lines are invoices — try to match
		ref = (item.reference or "").strip()
		if not ref:
			item.recon_status = "Unreconciled"
			continue

		# Normalize reference for matching (handles INV/001 vs INV-001 vs INV 001)
		normalized_ref = normalize_for_matching(ref)
		candidates = pi_by_normalized_ref.get(normalized_ref, [])

		if not candidates:
			item.recon_status = "Missing from ERPNext"
			continue

		# Take the first match
		pi = candidates[0]
		item.matched_invoice = pi["name"]
		item.erp_amount = pi["grand_total"]
		item.erp_outstanding = pi["outstanding_amount"]
		matched_pi_names.add(pi["name"])

		# Compare amounts
		stmt_amount = item.debit or 0
		erp_amount = pi["grand_total"] or 0
		diff = abs(stmt_amount - erp_amount)

		if diff < 0.01:  # Float tolerance
			item.recon_status = "Matched"
			item.difference = 0
		else:
			item.recon_status = "Amount Mismatch"
			item.difference = round(stmt_amount - erp_amount, 2)

	# Reverse check: find PIs NOT on the statement
	# Only when period is trustworthy (both dates set)
	if ocr_statement.period_from and ocr_statement.period_to:
		statement_normalized_refs = {
			normalize_for_matching((item.reference or "").strip())
			for item in ocr_statement.items
			if (item.reference or "").strip()
		}
		for pi in all_pis:
			if pi["name"] in matched_pi_names:
				continue
			pi_normalized = normalize_for_matching(pi.get("bill_no", ""))
			if pi_normalized and pi_normalized in statement_normalized_refs:
				continue
			ocr_statement.append(
				"items",
				{
					"reference": pi.get("bill_no") or pi["name"],
					"transaction_date": pi.get("posting_date"),
					"description": "Not on statement (ERPNext PI exists)",
					"debit": pi["grand_total"],
					"credit": 0,
					"balance": 0,
					"recon_status": "Not in Statement",
					"matched_invoice": pi["name"],
					"erp_amount": pi["grand_total"],
					"erp_outstanding": pi["outstanding_amount"],
					"difference": 0,
				},
			)
	else:
		ocr_statement.reverse_check_skipped = 1

	# Update summary counts
	ocr_statement.total_lines = len(ocr_statement.items)
	ocr_statement.matched_count = sum(
		1 for i in ocr_statement.items if getattr(i, "recon_status", "") == "Matched"
	)
	ocr_statement.mismatch_count = sum(
		1 for i in ocr_statement.items if getattr(i, "recon_status", "") == "Amount Mismatch"
	)
	ocr_statement.missing_count = sum(
		1 for i in ocr_statement.items if getattr(i, "recon_status", "") == "Missing from ERPNext"
	)
	ocr_statement.not_in_statement_count = sum(
		1 for i in ocr_statement.items if getattr(i, "recon_status", "") == "Not in Statement"
	)
	ocr_statement.payment_count = sum(
		1 for i in ocr_statement.items if getattr(i, "recon_status", "") == "Payment"
	)
