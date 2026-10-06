"""Tests for the duplicate supplier-invoice-number control (Q20, v1.13.0)."""

import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from erpocr_integration import duplicate_bill as db_mod
from erpocr_integration.duplicate_bill import (
	find_conflicts,
	get_status,
	normalize_bill_no,
	validate_purchase_invoice,
)

APPROVER = "dani@example.com"
OTHER = "clerk@example.com"
STAMP = datetime.datetime(2026, 10, 6, 9, 30, 0)


def _settings(on=True, approvers=(APPROVER,)):
	return SimpleNamespace(
		enable_duplicate_bill_check=1 if on else 0,
		duplicate_bill_approvers=[SimpleNamespace(user=u) for u in approvers],
		get=lambda k, d=None: {
			"enable_duplicate_bill_check": 1 if on else 0,
			"duplicate_bill_approvers": [SimpleNamespace(user=u) for u in approvers],
		}.get(k, d),
	)


def _pi(before=None, **kw):
	d = dict(
		name="ACC-PINV-2026-00010",
		bill_no="INV-000061",
		supplier="Acme Supplies",
		company="Star Pops",
		is_return=0,
		custom_duplicate_bill_override=0,
		custom_duplicate_bill_reason=None,
		custom_duplicate_bill_approved_by=None,
		custom_duplicate_bill_approved_at=None,
	)
	d.update(kw)
	doc = SimpleNamespace(**d)
	doc.get_doc_before_save = lambda: before
	return doc


CONFLICT_SAME = {
	"name": "ACC-PINV-2026-00001",
	"supplier": "Acme Supplies",
	"posting_date": datetime.date(2026, 9, 1),
	"grand_total": 1150.0,
	"docstatus": 1,
}
CONFLICT_OTHER = dict(CONFLICT_SAME, name="ACC-PINV-2026-00002", supplier="Bolt & Nut <Pty>")


@pytest.fixture
def env(mock_frappe):
	"""Feature on, approver configured, unique setting absent, session = clerk."""
	mock_frappe.throw.reset_mock()
	mock_frappe.db.sql.reset_mock()
	mock_frappe.get_cached_doc.return_value = _settings()
	mock_frappe.session.user = OTHER
	mock_frappe.db.get_value.return_value = None
	meta = mock_frappe.get_meta.return_value
	meta.get_field.return_value = SimpleNamespace(unique=0)
	mock_frappe.db.sql.return_value = []
	with patch.object(db_mod, "now_datetime", return_value=STAMP):
		yield mock_frappe
	mock_frappe.session.user = "Administrator"
	mock_frappe.db.sql.return_value = []
	mock_frappe.db.get_single_value.side_effect = None
	mock_frappe.get_roles.return_value = ["All"]


class TestNormalise:
	def test_punctuation_case_and_spaces_collapse(self):
		assert normalize_bill_no("INV-000061") == normalize_bill_no("inv 000061") == "INV000061"
		assert normalize_bill_no("INV000061") == "INV000061"

	def test_suffix_is_different(self):
		assert normalize_bill_no("INV-000061-1") != normalize_bill_no("INV-000061")

	def test_blank(self):
		assert normalize_bill_no(None) == "" and normalize_bill_no(" - ") == ""


class TestFindConflicts:
	def test_query_shape_and_params(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		rows = find_conflicts("inv 000061", "PI-X", "Star Pops")
		assert rows == [CONFLICT_SAME]
		sql, params = env.db.sql.call_args[0]
		assert params == {"company": "Star Pops", "name": "PI-X", "norm": "INV000061"}
		for frag in ("docstatus < 2", "is_return = 0", "company = %(company)s", "name != %(name)s"):
			assert frag in sql
		assert "ORDER BY posting_date DESC, name DESC" in sql
		assert "INV000061" not in sql and "Star Pops" not in sql  # parameterised

	def test_blank_bill_no_runs_no_query(self, env):
		assert find_conflicts("  -- ", "PI-X", "Star Pops") == []
		env.db.sql.assert_not_called()


class TestHook:
	def test_feature_off_no_query(self, env):
		env.get_cached_doc.return_value = _settings(on=False)
		validate_purchase_invoice(_pi())
		env.db.sql.assert_not_called()

	def test_blank_bill_no_and_return_no_query(self, env):
		validate_purchase_invoice(_pi(bill_no=""))
		validate_purchase_invoice(_pi(is_return=1))
		env.db.sql.assert_not_called()

	def test_no_conflict_passes(self, env):
		validate_purchase_invoice(_pi())
		env.throw.assert_not_called()

	def test_conflict_same_and_different_supplier_wording(self, env):
		env.db.sql.return_value = [CONFLICT_SAME, CONFLICT_OTHER]
		env.db.get_value.return_value = "Dani Approver"
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi())
		msg = env.throw.call_args[0][0]
		assert "same supplier, likely the same invoice entered twice" in msg
		assert "different supplier using the same number" in msg
		assert "ACC-PINV-2026-00001" in msg and "ACC-PINV-2026-00002" in msg
		assert "Dani Approver" in msg
		assert "Ask" in msg and "Approve duplicate invoice number" in msg

	def test_non_approver_tick_throws(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi(custom_duplicate_bill_override=1, custom_duplicate_bill_reason="r"))
		assert "named approver" in env.throw.call_args[0][0]

	def test_approver_blank_reason_throws(self, env):
		env.session.user = APPROVER
		env.db.sql.return_value = [CONFLICT_SAME]
		with pytest.raises(Exception):
			validate_purchase_invoice(
				_pi(custom_duplicate_bill_override=1, custom_duplicate_bill_reason="  ")
			)
		assert "reason" in env.throw.call_args[0][0]

	def test_approver_with_reason_passes_and_stamps_over_client_values(self, env):
		env.session.user = APPROVER
		env.db.sql.return_value = [CONFLICT_SAME]
		doc = _pi(
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="Genuine re-bill",
			custom_duplicate_bill_approved_by="someone.else@example.com",
			custom_duplicate_bill_approved_at=datetime.datetime(2001, 1, 1),
		)
		validate_purchase_invoice(doc)
		env.throw.assert_not_called()
		assert doc.custom_duplicate_bill_approved_by == APPROVER
		assert doc.custom_duplicate_bill_approved_at == STAMP

	def test_later_save_by_non_approver_keeps_approval(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		before = _pi(
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="Genuine re-bill",
			custom_duplicate_bill_approved_by=APPROVER,
			custom_duplicate_bill_approved_at=STAMP,
		)
		doc = _pi(
			before=before,
			bill_no="inv 000061",  # same normalised number
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="Genuine re-bill",
			custom_duplicate_bill_approved_by=APPROVER,
			custom_duplicate_bill_approved_at=STAMP,
		)
		validate_purchase_invoice(doc)
		env.throw.assert_not_called()
		assert doc.custom_duplicate_bill_override == 1
		assert doc.custom_duplicate_bill_approved_by == APPROVER

	@pytest.mark.parametrize("change", [{"bill_no": "INV-000062"}, {"supplier": "Other Supplier"}])
	def test_change_after_approval_clears_and_blocks(self, env, change):
		env.db.sql.return_value = [CONFLICT_SAME]
		before = _pi(
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="ok",
			custom_duplicate_bill_approved_by=APPROVER,
			custom_duplicate_bill_approved_at=STAMP,
		)
		doc = _pi(
			before=before,
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="ok",
			custom_duplicate_bill_approved_by=APPROVER,
			custom_duplicate_bill_approved_at=STAMP,
			**change,
		)
		with pytest.raises(Exception):
			validate_purchase_invoice(doc)
		assert doc.custom_duplicate_bill_override == 0
		assert doc.custom_duplicate_bill_reason is None
		assert doc.custom_duplicate_bill_approved_by is None
		assert doc.custom_duplicate_bill_approved_at is None
		assert "already used by" in env.throw.call_args[0][0]

	def _approved_pair(self, **doc_kw):
		kw = dict(
			custom_duplicate_bill_override=1,
			custom_duplicate_bill_reason="Genuine re-bill",
			custom_duplicate_bill_approved_by=APPROVER,
			custom_duplicate_bill_approved_at=STAMP,
		)
		before = _pi(**kw)
		return before, _pi(before=before, **dict(kw, **doc_kw))

	def test_non_approver_cannot_edit_reason_after_approval(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		_, doc = self._approved_pair(custom_duplicate_bill_reason="Changed by clerk")
		with pytest.raises(Exception):
			validate_purchase_invoice(doc)
		assert "change or withdraw" in env.throw.call_args[0][0]

	def test_non_approver_desk_save_with_string_stamp_passes(self, env):
		# A Desk save sends approved_at back as a STRING; the saved copy holds a datetime.
		# A non-approver editing an unrelated field must not be refused, and a forged
		# by/at must be replaced by the stored stamp.
		env.db.sql.return_value = [CONFLICT_SAME]
		_, doc = self._approved_pair(
			custom_duplicate_bill_approved_at=str(STAMP),
			custom_duplicate_bill_approved_by="someone.else@example.com",
		)
		validate_purchase_invoice(doc)
		env.throw.assert_not_called()
		assert doc.custom_duplicate_bill_approved_at == STAMP
		assert doc.custom_duplicate_bill_approved_by == APPROVER

	def test_non_approver_cannot_untick_approval(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		_, doc = self._approved_pair(custom_duplicate_bill_override=0)
		with pytest.raises(Exception):
			validate_purchase_invoice(doc)
		assert "change or withdraw" in env.throw.call_args[0][0]

	def test_approver_reason_edit_restamps(self, env):
		env.session.user = APPROVER
		env.db.sql.return_value = [CONFLICT_SAME]
		_, doc = self._approved_pair(custom_duplicate_bill_reason="Updated reason")
		validate_purchase_invoice(doc)
		env.throw.assert_not_called()
		assert doc.custom_duplicate_bill_approved_at == STAMP  # patched now_datetime
		assert doc.custom_duplicate_bill_approved_by == APPROVER
		assert doc.custom_duplicate_bill_reason == "Updated reason"

	def test_approver_untick_withdraws_and_blocks(self, env):
		env.session.user = APPROVER
		env.db.sql.return_value = [CONFLICT_SAME]
		_, doc = self._approved_pair(custom_duplicate_bill_override=0)
		with pytest.raises(Exception):
			validate_purchase_invoice(doc)
		assert doc.custom_duplicate_bill_override == 0
		assert doc.custom_duplicate_bill_reason is None
		assert doc.custom_duplicate_bill_approved_by is None
		assert doc.custom_duplicate_bill_approved_at is None

	def test_new_tick_with_no_conflict_throws(self, env):
		env.session.user = APPROVER
		env.db.sql.return_value = []
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi(custom_duplicate_bill_override=1, custom_duplicate_bill_reason="r"))
		assert "no duplicate invoice number to approve" in env.throw.call_args[0][0]

	def test_conflict_message_says_where_the_checkbox_is(self, env):
		env.db.sql.return_value = [CONFLICT_SAME]
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi())
		assert "in the Supplier Invoice section" in env.throw.call_args[0][0]

	def test_unique_setting_cancelled_exact_holder_gives_db_message(self, env):
		env.get_meta.return_value.get_field.return_value = SimpleNamespace(unique="1")
		env.db.sql.return_value = [{"name": "ACC-PINV-2026-00003", "docstatus": 2}]
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi(bill_no="INV-000061"))
		msg = env.throw.call_args[0][0]
		assert "cancelled" in msg and "ACC-PINV-2026-00003" in msg and "'Unique' setting" in msg
		sql, params = env.db.sql.call_args_list[0][0]
		assert params == {"bill_no": "INV-000061", "name": "ACC-PINV-2026-00010"}
		assert "bill_no = %(bill_no)s" in sql and "ORDER BY" in sql
		assert "docstatus <" not in sql and "company" not in sql and "is_return" not in sql
		assert len(env.db.sql.call_args_list) == 1  # blocked before the normal scan

	def test_unique_setting_blocks_even_when_approval_ticked(self, env):
		env.session.user = APPROVER
		env.get_meta.return_value.get_field.return_value = SimpleNamespace(unique="1")
		env.db.sql.return_value = [{"name": "ACC-PINV-2026-00003", "docstatus": 1}]
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi(custom_duplicate_bill_override=1, custom_duplicate_bill_reason="r"))
		assert "'Unique' setting" in env.throw.call_args[0][0]

	def test_unique_setting_normalised_only_match_uses_normal_flow(self, env):
		env.get_meta.return_value.get_field.return_value = SimpleNamespace(unique="1")
		# first call = exact DB-holder query (nothing), second = normalised scan (hit)
		env.db.sql.side_effect = [[], [CONFLICT_SAME]]
		with pytest.raises(Exception):
			validate_purchase_invoice(_pi(bill_no="INV 61"))
		msg = env.throw.call_args[0][0]
		assert "'Unique' setting" not in msg and "already used by" in msg
		env.db.sql.side_effect = None

	def test_supplier_name_is_escaped(self, env):
		env.db.sql.return_value = [CONFLICT_OTHER]
		with patch.object(db_mod, "escape_html", side_effect=lambda s: str(s).replace("<", "&lt;")):
			with pytest.raises(Exception):
				validate_purchase_invoice(_pi())
		assert "&lt;Pty>" in env.throw.call_args[0][0]


class TestGetStatus:
	def _run(self, env, feature, unique, builtin):
		env.get_roles.return_value = ["System Manager"]
		env.get_meta.return_value.get_field.return_value = SimpleNamespace(unique=1 if unique else 0)
		vals = {"enable_duplicate_bill_check": feature, "check_supplier_invoice_uniqueness": builtin}
		env.db.get_single_value.side_effect = lambda dt, f: vals[f]
		return get_status()

	@pytest.mark.parametrize("feature", [0, 1])
	@pytest.mark.parametrize("unique", [False, True])
	@pytest.mark.parametrize("builtin", [0, 1])
	def test_all_combinations(self, env, feature, unique, builtin):
		out = self._run(env, feature, unique, builtin)
		by = {m["color"]: m["text"] for m in out}
		colours = [m["color"] for m in out]
		assert ("red" in colours and "still blocks every" in " ".join(m["text"] for m in out)) == unique
		assert any("same financial year" in m["text"] for m in out) == bool(builtin)
		assert any("only caught by this check" in m["text"] for m in out) == (not builtin)
		no_protection = not feature and not unique and not builtin
		assert any("No duplicate protection is active" in m["text"] for m in out) == no_protection
		assert by  # never empty

	def test_requires_system_manager(self, env):
		env.get_roles.return_value = ["OCR Manager"]
		with pytest.raises(Exception):
			get_status()
		env.get_roles.return_value = ["All"]
