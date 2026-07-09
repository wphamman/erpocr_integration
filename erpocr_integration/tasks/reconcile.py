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

	# Get submitted PIs for this supplier.
	#
	# The candidate window for FORWARD matching is deliberately WIDER than the
	# statement period: open-item statements carry unpaid invoices from prior
	# months (brought forward), and a period-bounded pool mis-flagged those as
	# "Missing from ERPNext" (live-review finding R1). We look back 365 days
	# before the period start — same bound rationale as the no-period fallback
	# (a supplier with 10k+ PIs must not trigger an unbounded get_all); nothing
	# posted AFTER period_to can be on the statement, so that stays the upper
	# cap. The REVERSE check below remains strictly period-bounded.
	pi_filters = {
		"supplier": ocr_statement.supplier,
		"company": ocr_statement.company,
		"docstatus": 1,
	}
	window_anchor = ocr_statement.period_from or ocr_statement.period_to
	if ocr_statement.period_to:
		pi_filters["posting_date"] = [
			"between",
			[frappe.utils.add_days(window_anchor, -365), ocr_statement.period_to],
		]
	else:
		pi_filters["posting_date"] = [
			">=",
			frappe.utils.add_days(frappe.utils.today(), -365),
		]

	all_pis = frappe.get_all(
		"Purchase Invoice",
		filters=pi_filters,
		fields=["name", "bill_no", "grand_total", "outstanding_amount", "posting_date"],
		# Explicit, deterministic order: candidate selection and the v16 default-
		# sort flip (modified → creation) must not decide which PI a statement
		# line reconciles against (live-review finding R2/O2).
		order_by="posting_date asc, name asc",
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

	def _amount_matches(c, amount):
		return abs((c["grand_total"] or 0) - amount) < 0.01

	def _in_period(c):
		# getdate() both sides (v1.8.0 hardening): posting_date comes back as a
		# date object, period_from as a Data/Date field value — raw str()
		# comparison silently mis-orders on any format mismatch (and "None"
		# compared as a string). A candidate without a posting_date is not
		# provably in-period, so it is treated as brought-forward.
		if not ocr_statement.period_from:
			return True
		posting_date = c.get("posting_date")
		if not posting_date:
			return False
		return frappe.utils.getdate(posting_date) >= frappe.utils.getdate(ocr_statement.period_from)

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

		# Pick the best candidate, not blindly the first (finding R2): several
		# PIs can share a normalized bill_no (INV/100 vs INV-100, a re-used or
		# recycled supplier number, a recurring same-ref charge). Preference
		# order — in-period beats brought-forward at equal evidence, so a
		# recycled reference from last year can't shadow this period's invoice:
		#   1. amount match among not-yet-matched IN-PERIOD candidates
		#   2. amount match among not-yet-matched prior-period (brought-forward)
		#   3. amount match among already-matched (legit re-reference of one PI,
		#      e.g. the statement lists the same invoice twice)
		#   4. earliest not-yet-matched in-period candidate
		#   5. earliest not-yet-matched candidate
		#   6. first candidate (everything consumed)
		stmt_amount = item.debit or 0
		unconsumed = [c for c in candidates if c["name"] not in matched_pi_names]
		unconsumed_in_period = [c for c in unconsumed if _in_period(c)]
		pi = (
			next((c for c in unconsumed_in_period if _amount_matches(c, stmt_amount)), None)
			or next((c for c in unconsumed if _amount_matches(c, stmt_amount)), None)
			or next((c for c in candidates if _amount_matches(c, stmt_amount)), None)
			or (unconsumed_in_period[0] if unconsumed_in_period else None)
			or (unconsumed[0] if unconsumed else candidates[0])
		)
		item.matched_invoice = pi["name"]
		item.erp_amount = pi["grand_total"]
		item.erp_outstanding = pi["outstanding_amount"]
		matched_pi_names.add(pi["name"])

		# Compare amounts
		erp_amount = pi["grand_total"] or 0
		diff = abs(stmt_amount - erp_amount)

		if diff < 0.01:  # Float tolerance
			item.recon_status = "Matched"
			item.difference = 0
		else:
			item.recon_status = "Amount Mismatch"
			item.difference = round(stmt_amount - erp_amount, 2)

	# Reverse check: find PIs NOT on the statement
	# Only when period is trustworthy (both dates set). Strictly period-bounded:
	# all_pis now includes prior-period (brought-forward) candidates for forward
	# matching, but a prior-period PI absent from this statement is NOT a
	# discrepancy — only PIs posted inside the period belong here.
	if ocr_statement.period_from and ocr_statement.period_to:
		statement_normalized_refs = {
			normalize_for_matching((item.reference or "").strip())
			for item in ocr_statement.items
			if (item.reference or "").strip()
		}
		for pi in all_pis:
			# getdate() both sides (v1.8.0 hardening) — same rationale as
			# _in_period above; submitted PIs always carry a posting_date.
			if frappe.utils.getdate(pi.get("posting_date")) < frappe.utils.getdate(ocr_statement.period_from):
				continue
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
