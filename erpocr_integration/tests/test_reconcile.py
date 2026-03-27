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
