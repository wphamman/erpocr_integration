"""Tests for statement reconciliation logic."""

from types import SimpleNamespace

import pytest

from erpocr_integration.tasks.reconcile import reconcile_statement


def _make_statement(**overrides):
	defaults = dict(
		supplier="SUP-001",
		company="Test Co",
		period_from="2026-02-01",
		period_to="2026-02-28",
		items=[],
		reverse_check_skipped=0,
		total_lines=0,
		matched_count=0,
		mismatch_count=0,
		missing_count=0,
		not_in_statement_count=0,
		payment_count=0,
	)
	defaults.update(overrides)
	obj = SimpleNamespace(**defaults)
	obj.append = lambda table, row: obj.items.append(SimpleNamespace(**row))
	return obj


def _make_stmt_item(**overrides):
	defaults = dict(
		reference="INV-001",
		transaction_date="2026-02-15",
		description="Tax Invoice",
		debit=1000.0,
		credit=0.0,
		balance=1000.0,
		recon_status="",
		matched_invoice="",
		erp_amount=0,
		erp_outstanding=0,
		difference=0,
	)
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


class TestReconcileStatement:
	def test_matches_invoice_by_bill_no(self, mock_frappe):
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV-001", debit=1000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Matched"
		assert stmt.items[0].matched_invoice == "PI-001"
		assert stmt.items[0].erp_amount == 1000.0

	def test_matches_with_normalized_reference(self, mock_frappe):
		"""INV/001 on statement should match INV-001 in ERPNext."""
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV/001", debit=1000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Matched"

	def test_detects_amount_mismatch(self, mock_frappe):
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV-002", debit=1500.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-002",
				"bill_no": "INV-002",
				"grand_total": 1400.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Amount Mismatch"
		assert stmt.items[0].erp_amount == 1400.0
		assert stmt.items[0].difference == 100.0

	def test_marks_missing_from_erpnext(self, mock_frappe):
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV-UNKNOWN", debit=500.0)],
		)
		mock_frappe.get_all.return_value = []

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Missing from ERPNext"

	def test_marks_credit_as_payment(self, mock_frappe):
		stmt = _make_statement(
			items=[_make_stmt_item(reference="PMT-001", debit=0.0, credit=5000.0)],
		)
		mock_frappe.get_all.return_value = []

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Payment"

	def test_reverse_check_adds_not_in_statement(self, mock_frappe):
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV-001", debit=1000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
			{
				"name": "PI-099",
				"bill_no": "INV-099",
				"grand_total": 2000.0,
				"outstanding_amount": 2000.0,
				"posting_date": "2026-02-20",
			},
		]

		reconcile_statement(stmt)

		assert len(stmt.items) == 2
		reverse_item = stmt.items[1]
		assert reverse_item.recon_status == "Not in Statement"
		assert reverse_item.reference == "INV-099"
		assert reverse_item.debit == 2000.0

	def test_reverse_check_skipped_when_no_period(self, mock_frappe):
		"""No period_from → skip reverse check, set flag."""
		stmt = _make_statement(
			period_from=None,
			period_to=None,
			items=[_make_stmt_item(reference="INV-001", debit=1000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
			{
				"name": "PI-099",
				"bill_no": "INV-099",
				"grand_total": 2000.0,
				"outstanding_amount": 2000.0,
				"posting_date": "2026-02-20",
			},
		]

		reconcile_statement(stmt)

		# Forward match still works
		assert stmt.items[0].recon_status == "Matched"
		# But NO reverse check rows added
		assert len(stmt.items) == 1
		assert stmt.reverse_check_skipped == 1

	def test_updates_summary_counts(self, mock_frappe):
		stmt = _make_statement(
			items=[
				_make_stmt_item(reference="INV-001", debit=1000.0),
				_make_stmt_item(reference="PMT-001", debit=0.0, credit=500.0),
			],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-02-10",
			},
		]

		reconcile_statement(stmt)

		assert stmt.matched_count == 1
		assert stmt.payment_count == 1
		assert stmt.total_lines == 2


# ---------------------------------------------------------------------------
# Live-review fixes (2026-07): brought-forward invoices + duplicate bill_no
# ---------------------------------------------------------------------------


class TestBroughtForwardInvoices:
	"""Finding R1: open-item statements list unpaid prior-period invoices —
	the forward-match candidate pool must reach back beyond the period."""

	def test_prior_period_pi_matches_forward(self, mock_frappe):
		"""A statement line for an invoice posted BEFORE period_from must match,
		not be flagged 'Missing from ERPNext'."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="INV-APR", debit=500.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-APRIL",
				"bill_no": "INV-APR",
				"grand_total": 500.0,
				"outstanding_amount": 500.0,
				"posting_date": "2026-04-10",  # before period_from
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].recon_status == "Matched"
		assert stmt.items[0].matched_invoice == "PI-APRIL"

	def test_candidate_window_reaches_before_period(self, mock_frappe):
		"""The PI query must look back beyond period_from (365d window),
		capped at period_to, with a deterministic order (v16-safe)."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item()],
		)
		mock_frappe.get_all.return_value = []

		reconcile_statement(stmt)

		kwargs = mock_frappe.get_all.call_args[1]
		posting_filter = kwargs["filters"]["posting_date"]
		assert posting_filter[0] == "between"
		assert posting_filter[1][1] == "2026-06-30"  # upper cap = period_to
		# lower bound comes from add_days(period_from, -365) — assert the call
		mock_frappe.utils.add_days.assert_called_with("2026-06-01", -365)
		assert "posting_date" in kwargs["order_by"]

	def test_unmatched_prior_period_pi_not_flagged_not_in_statement(self, mock_frappe):
		"""Reverse check stays period-bounded: a prior-period PI absent from
		the statement is NOT a discrepancy."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="INV-JUN", debit=100.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-JUN",
				"bill_no": "INV-JUN",
				"grand_total": 100.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-06-15",
			},
			{
				# prior-period, unreferenced on the statement — must NOT appear
				"name": "PI-MAY-PAID",
				"bill_no": "INV-MAY",
				"grand_total": 999.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-05-05",
			},
		]

		reconcile_statement(stmt)

		assert stmt.not_in_statement_count == 0
		assert all(i.recon_status != "Not in Statement" for i in stmt.items)

	def test_in_period_unmatched_pi_still_flagged(self, mock_frappe):
		"""The reverse check itself still works for in-period PIs."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="INV-A", debit=100.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-A",
				"bill_no": "INV-A",
				"grand_total": 100.0,
				"outstanding_amount": 0.0,
				"posting_date": "2026-06-10",
			},
			{
				"name": "PI-FORGOTTEN",
				"bill_no": "INV-B",
				"grand_total": 250.0,
				"outstanding_amount": 250.0,
				"posting_date": "2026-06-20",
			},
		]

		reconcile_statement(stmt)

		assert stmt.not_in_statement_count == 1


class TestDuplicateBillNo:
	"""Finding R2: several PIs sharing a normalized bill_no must not attach the
	statement line to an arbitrary (query-order-dependent) PI."""

	def _two_pis(self):
		return [
			{
				"name": "PI-100A",
				"bill_no": "INV/100",
				"grand_total": 1000.0,
				"outstanding_amount": 1000.0,
				"posting_date": "2026-02-05",
			},
			{
				"name": "PI-100B",
				"bill_no": "INV-100",  # normalizes identically
				"grand_total": 2500.0,
				"outstanding_amount": 2500.0,
				"posting_date": "2026-02-20",
			},
		]

	def test_prefers_amount_matching_candidate(self, mock_frappe):
		"""Line debit 2500 must match PI-100B even though PI-100A sorts first."""
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV 100", debit=2500.0)],
		)
		mock_frappe.get_all.return_value = self._two_pis()

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-100B"
		assert stmt.items[0].recon_status == "Matched"

	def test_two_lines_consume_distinct_pis(self, mock_frappe):
		"""Two statement lines with the same normalized ref match two different
		PIs instead of both attaching to the first."""
		stmt = _make_statement(
			items=[
				_make_stmt_item(reference="INV/100", debit=1000.0),
				_make_stmt_item(reference="INV-100", debit=2500.0),
			],
		)
		mock_frappe.get_all.return_value = self._two_pis()

		reconcile_statement(stmt)

		matched = {stmt.items[0].matched_invoice, stmt.items[1].matched_invoice}
		assert matched == {"PI-100A", "PI-100B"}
		assert all(i.recon_status == "Matched" for i in stmt.items)

	def test_no_amount_match_falls_back_to_earliest_unconsumed(self, mock_frappe):
		"""Deviating amount → earliest-posted unconsumed candidate, flagged
		as Amount Mismatch (deterministic, not query-order-dependent)."""
		stmt = _make_statement(
			items=[_make_stmt_item(reference="INV-100", debit=1800.0)],
		)
		mock_frappe.get_all.return_value = self._two_pis()

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-100A"
		assert stmt.items[0].recon_status == "Amount Mismatch"

	def test_recycled_ref_prefers_in_period_on_mismatch(self, mock_frappe):
		"""A supplier recycling last year's invoice number: with no exact
		amount match (OCR noise), the IN-PERIOD candidate must win — not the
		earliest-posted prior-period one."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="INV-001", debit=1052.00)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-LAST-JULY",
				"bill_no": "INV-001",
				"grand_total": 980.0,
				"outstanding_amount": 0.0,
				"posting_date": "2025-07-15",  # earliest — must NOT win
			},
			{
				"name": "PI-THIS-JUNE",
				"bill_no": "INV-001",
				"grand_total": 1052.50,  # 0.50 off — defeats the amount gate
				"outstanding_amount": 1052.50,
				"posting_date": "2026-06-10",
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-THIS-JUNE"
		assert stmt.items[0].recon_status == "Amount Mismatch"

	def test_recurring_same_amount_prefers_in_period(self, mock_frappe):
		"""Recurring same-ref same-amount charge (monthly RENT): the amount
		matches every month — the in-period PI must win over older ones."""
		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="RENT", debit=5000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": f"PI-RENT-{m}",
				"bill_no": "RENT",
				"grand_total": 5000.0,
				"outstanding_amount": 0.0,
				"posting_date": f"2026-0{m}-01",
			}
			for m in range(1, 7)
		]

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-RENT-6"
		assert stmt.items[0].recon_status == "Matched"

	def test_full_amount_re_reference_reuses_consumed_pi(self, mock_frappe):
		"""The statement lists the SAME invoice twice at full amount while a
		different-amount PI shares the normalized ref — the second line must
		re-reference the consumed PI, not be forced onto the wrong one."""
		stmt = _make_statement(
			items=[
				_make_stmt_item(reference="INV/100", debit=1000.0),
				_make_stmt_item(reference="INV-100", debit=1000.0),
			],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-100A",
				"bill_no": "INV/100",
				"grand_total": 1000.0,
				"outstanding_amount": 1000.0,
				"posting_date": "2026-02-05",
			},
			{
				"name": "PI-100B",
				"bill_no": "INV-100",
				"grand_total": 2500.0,
				"outstanding_amount": 2500.0,
				"posting_date": "2026-02-20",
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-100A"
		assert stmt.items[1].matched_invoice == "PI-100A"  # re-reference, not PI-100B


class TestDateCompareHardening:
	"""v1.8.0 C(d): recon date compares run through getdate() on both sides.

	get_all really returns datetime.date objects for posting_date while
	period_from arrives as a string — the old str()-vs-str() comparison relied
	on both sides happening to serialize identically. These tests feed the
	REAL mixed types (ADR-0009: real values, not echo mocks).
	"""

	def test_in_period_preference_with_real_date_objects(self, mock_frappe):
		"""In-period preference still works when posting_date is a datetime.date
		(the real get_all return type) and period_from is a string."""
		import datetime

		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="RENT", debit=5000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-RENT-MAY",
				"bill_no": "RENT",
				"grand_total": 5000.0,
				"outstanding_amount": 0.0,
				"posting_date": datetime.date(2026, 5, 1),
			},
			{
				"name": "PI-RENT-JUNE",
				"bill_no": "RENT",
				"grand_total": 5000.0,
				"outstanding_amount": 5000.0,
				"posting_date": datetime.date(2026, 6, 1),
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-RENT-JUNE"
		assert stmt.items[0].recon_status == "Matched"

	def test_reverse_check_with_real_date_objects(self, mock_frappe):
		"""Reverse check period-bounds correctly on datetime.date posting dates:
		the prior-period PI is skipped, the in-period one is flagged."""
		import datetime

		stmt = _make_statement(
			period_from="2026-02-01",
			period_to="2026-02-28",
			items=[_make_stmt_item(reference="INV-001", debit=1000.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-001",
				"bill_no": "INV-001",
				"grand_total": 1000.0,
				"outstanding_amount": 0.0,
				"posting_date": datetime.date(2026, 2, 10),
			},
			{
				"name": "PI-PRIOR",
				"bill_no": "INV-OLD",
				"grand_total": 400.0,
				"outstanding_amount": 400.0,
				"posting_date": datetime.date(2026, 1, 20),  # before period → skip
			},
			{
				"name": "PI-UNLISTED",
				"bill_no": "INV-999",
				"grand_total": 750.0,
				"outstanding_amount": 750.0,
				"posting_date": datetime.date(2026, 2, 15),  # in period → flag
			},
		]

		reconcile_statement(stmt)

		flagged = [i for i in stmt.items if i.recon_status == "Not in Statement"]
		assert [i.matched_invoice for i in flagged] == ["PI-UNLISTED"]

	def test_missing_posting_date_treated_as_brought_forward(self, mock_frappe):
		"""A candidate without a posting_date is not provably in-period — the
		in-period candidate must win the equal-evidence preference. (The old
		str() compare ranked None as in-period: "None" > "2026-...".)"""
		import datetime

		stmt = _make_statement(
			period_from="2026-06-01",
			period_to="2026-06-30",
			items=[_make_stmt_item(reference="INV-7", debit=880.0)],
		)
		mock_frappe.get_all.return_value = [
			{
				"name": "PI-NO-DATE",
				"bill_no": "INV-7",
				"grand_total": 880.5,  # 0.50 off — defeats the amount gate
				"outstanding_amount": 880.5,
				"posting_date": None,
			},
			{
				"name": "PI-IN-PERIOD",
				"bill_no": "INV-7",
				"grand_total": 880.9,  # also off — equal evidence, period decides
				"outstanding_amount": 880.9,
				"posting_date": datetime.date(2026, 6, 12),
			},
		]

		reconcile_statement(stmt)

		assert stmt.items[0].matched_invoice == "PI-IN-PERIOD"
