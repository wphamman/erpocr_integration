"""Duplicate supplier-invoice-number control with a named override (Q20, v1.13.0).

A `validate` doc_event on EVERY Purchase Invoice (not only OCR-created ones).
Blocks a re-used supplier invoice number, but lets a named approver (OCR Settings
-> Duplicate supplier invoice numbers) override with a reason instead of staff
suffixing the number. ERPNext's own `check_supplier_invoice_uniqueness` runs in the
controller BEFORE doc_events and stays ON: same supplier + same bill_no + same fiscal
year is an absolute block that this module never sees and never bypasses.

The legacy database-level 'Unique' setting on Purchase Invoice.bill_no (a 2023
Customize Form change) is DETECTED and reported, never changed.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import escape_html, get_link_to_form, now_datetime


def normalize_bill_no(value) -> str:
	"""Uppercase with every non-alphanumeric character removed.

	`INV-000061` == `inv 000061` == `INV000061`; `INV-000061-1` is different.
	"""
	return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def find_conflicts(bill_no, exclude_name, company) -> list[dict]:
	"""Other live Purchase Invoices whose normalised bill_no equals this one's.

	Excludes cancelled (docstatus 2), returns (debit notes), the PI itself and
	other companies. Matches across suppliers on purpose.
	"""
	norm = normalize_bill_no(bill_no)
	if not norm:
		return []
	rows = frappe.db.sql(
		"""
		SELECT name, supplier, posting_date, grand_total, docstatus
		FROM `tabPurchase Invoice`
		WHERE docstatus < 2
			AND is_return = 0
			AND company = %(company)s
			AND name != %(name)s
			AND UPPER(REGEXP_REPLACE(bill_no, '[^A-Za-z0-9]', '')) = %(norm)s
		ORDER BY posting_date DESC, name DESC
		""",
		{"company": company or "", "name": exclude_name or "", "norm": norm},
		as_dict=True,
	)
	return list(rows or [])


def _as_flag(value) -> bool:
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def unique_setting_present() -> bool:
	"""True when Purchase Invoice.bill_no is unique at database level.

	`get_meta` merges Property Setters over the DocField, so this covers both.
	"""
	field = frappe.get_meta("Purchase Invoice").get_field("bill_no")
	return _as_flag(getattr(field, "unique", 0)) if field else False


def _approvers(settings) -> list[str]:
	return [r.user for r in (settings.get("duplicate_bill_approvers") or []) if r.user]


def _clear_override(doc) -> None:
	doc.custom_duplicate_bill_override = 0
	doc.custom_duplicate_bill_reason = None
	doc.custom_duplicate_bill_approved_by = None
	doc.custom_duplicate_bill_approved_at = None


def _conflict_message(doc, conflicts, approvers) -> str:
	lines = []
	for c in conflicts:
		same = (c.get("supplier") or "") == (doc.supplier or "")
		wording = (
			_("same supplier, likely the same invoice entered twice")
			if same
			else _("different supplier using the same number")
		)
		lines.append(
			"{} &ndash; {} &ndash; {} &ndash; {} &ndash; {}".format(
				get_link_to_form("Purchase Invoice", c["name"]),
				escape_html(c.get("supplier") or ""),
				c.get("posting_date"),
				c.get("grand_total"),
				wording,
			)
		)
	if approvers:
		names = ", ".join(escape_html(frappe.db.get_value("User", u, "full_name") or u) for u in approvers)
		ask = _(
			"Ask {0} to tick 'Approve duplicate invoice number' in the Supplier Invoice section and give a reason."
		).format(names)
	else:
		ask = _("No approvers are configured in OCR Settings; ask a System Manager to add one.")
	head = _("Supplier invoice number {0} is already used by:").format(escape_html(doc.bill_no))
	return head + "<br>" + "<br>".join(lines) + "<br><br>" + ask


def _db_unique_holder(bill_no, exclude_name) -> dict | None:
	"""Another PI the legacy DB 'Unique' index would collide with.

	The index is table-wide and compares with the column's own collation, so: exact
	`bill_no` match, ANY docstatus (cancelled included), ANY is_return, NO company filter.
	"""
	rows = frappe.db.sql(
		"""
		SELECT name, docstatus
		FROM `tabPurchase Invoice`
		WHERE bill_no = %(bill_no)s AND name != %(name)s
		ORDER BY creation DESC, name DESC
		LIMIT 1
		""",
		{"bill_no": bill_no, "name": exclude_name or ""},
		as_dict=True,
	)
	return rows[0] if rows else None


def _unique_blocker_message(holder) -> str:
	status = _("cancelled") if holder.get("docstatus") == 2 else _("existing")
	return _(
		"Supplier invoice number is already held by {0} PI {1}. The database 'Unique' setting on "
		"Supplier Invoice No (Customize Form) blocks re-using this number, even from a cancelled "
		"invoice. Ask a System Manager to remove that setting; after that an approver can approve "
		"the re-use if needed."
	).format(status, get_link_to_form("Purchase Invoice", holder["name"]))


def _override_changed(doc, before) -> bool:
	"""Did the user change the approval itself (the tick or the reason)?

	approved_by/at are server-stamped and restored by the caller, so they are NOT
	compared: a Desk save sends approved_at back as a string while the saved copy
	holds a datetime, and comparing them would refuse every save of an approved PI.
	"""
	if _as_flag(doc.custom_duplicate_bill_override) != _as_flag(before.custom_duplicate_bill_override):
		return True
	return (doc.custom_duplicate_bill_reason or "").strip() != (
		before.custom_duplicate_bill_reason or ""
	).strip()


def validate_purchase_invoice(doc, method=None) -> None:
	"""Purchase Invoice `validate` hook — see the module docstring."""
	settings = frappe.get_cached_doc("OCR Settings")
	if not _as_flag(settings.get("enable_duplicate_bill_check")):
		return
	norm = normalize_bill_no(doc.bill_no)
	if not norm or _as_flag(getattr(doc, "is_return", 0)):
		return

	# R3: while the legacy DB 'Unique' setting exists, MariaDB would reject a re-used
	# number (even from a cancelled PI) regardless of any approval; say so first.
	if unique_setting_present():
		holder = _db_unique_holder(doc.bill_no, doc.name)
		if holder:
			frappe.throw(_unique_blocker_message(holder), title=_("Duplicate supplier invoice number"))

	approvers = _approvers(settings)
	is_approver = frappe.session.user in approvers
	conflicts = find_conflicts(doc.bill_no, doc.name, doc.company)
	before = doc.get_doc_before_save()
	was_approved = bool(before) and _as_flag(getattr(before, "custom_duplicate_bill_override", 0))
	ticked = _as_flag(doc.custom_duplicate_bill_override)

	if was_approved and (
		normalize_bill_no(before.bill_no) != norm or (before.supplier or "") != (doc.supplier or "")
	):
		# Number or supplier changed after approval: fresh approval needed (anyone's save).
		_clear_override(doc)
		ticked = False
	elif was_approved:
		if not is_approver:
			if _override_changed(doc, before):
				frappe.throw(_("Only a named approver can change or withdraw an approval."))
			doc.custom_duplicate_bill_approved_by = before.custom_duplicate_bill_approved_by
			doc.custom_duplicate_bill_approved_at = before.custom_duplicate_bill_approved_at
		elif not ticked:
			_clear_override(doc)  # approver withdraws
		elif (doc.custom_duplicate_bill_reason or "").strip() != (
			before.custom_duplicate_bill_reason or ""
		).strip():
			if not (doc.custom_duplicate_bill_reason or "").strip():
				frappe.throw(_("A reason is required to approve a duplicate invoice number."))
			doc.custom_duplicate_bill_approved_by = frappe.session.user
			doc.custom_duplicate_bill_approved_at = now_datetime()
		else:
			doc.custom_duplicate_bill_approved_by = before.custom_duplicate_bill_approved_by
			doc.custom_duplicate_bill_approved_at = before.custom_duplicate_bill_approved_at
	elif ticked:
		if not is_approver:
			frappe.throw(_("Only a named approver (OCR Settings) can approve a duplicate invoice number."))
		if not (doc.custom_duplicate_bill_reason or "").strip():
			frappe.throw(_("A reason is required to approve a duplicate invoice number."))
		if not conflicts:
			frappe.throw(
				_(
					"There is no duplicate invoice number to approve; untick 'Approve duplicate invoice number'."
				)
			)
		doc.custom_duplicate_bill_approved_by = frappe.session.user
		doc.custom_duplicate_bill_approved_at = now_datetime()
	else:
		doc.custom_duplicate_bill_approved_by = None
		doc.custom_duplicate_bill_approved_at = None

	if not conflicts or ticked:
		return

	frappe.throw(_conflict_message(doc, conflicts, approvers), title=_("Duplicate supplier invoice number"))


@frappe.whitelist(methods=["GET"])
def get_status() -> list[dict]:
	"""Protection report for the OCR Settings form (System Manager only).

	Returns [{"color": "red"|"amber"|"green", "text": str}, ...].
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can view this."), frappe.PermissionError)

	feature_on = _as_flag(frappe.db.get_single_value("OCR Settings", "enable_duplicate_bill_check"))
	unique = unique_setting_present()
	builtin = _as_flag(frappe.db.get_single_value("Accounts Settings", "check_supplier_invoice_uniqueness"))

	out = []
	if unique:
		out.append(
			{
				"color": "red",
				"text": _(
					"A 'Unique' setting on Purchase Invoice → Supplier Invoice No still blocks every "
					"re-used number at database level, so approvals cannot take effect. Remove it in "
					"Customize Form after this check is switched on."
				),
			}
		)
	else:
		out.append(
			{"color": "green", "text": _("No database-level 'Unique' setting on Supplier Invoice No.")}
		)
	if builtin:
		out.append(
			{
				"color": "green",
				"text": _(
					"ERPNext blocks the same supplier using the same number in the same financial year; "
					"that case cannot be approved here."
				),
			}
		)
	else:
		out.append(
			{
				"color": "amber",
				"text": _(
					"Same-supplier, same-year duplicates are now only caught by this check and can be approved."
				),
			}
		)
	if not feature_on and not unique and not builtin:
		out.append({"color": "red", "text": _("No duplicate protection is active.")})
	return out
