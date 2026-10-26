# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""Gemini billing-failure handling (v1.14.0, Q23).

Google billing accounts are pre-paid, so the AI service can start refusing
payment (HTTP 402, or a 429 "credits depleted") at any time. That never fixes
itself, so retrying in the job is pointless. This module:

* recognises a billing failure from the response (``is_billing_failure``),
* records "OCR is stopped since <time>" and emails people at most once every
  6 hours (``record_billing_failure``),
* clears that state on the next successful Gemini call (``on_gemini_success``),
* feeds the red banner on the OCR lists (``get_billing_status``), and
* lets Drive scans heal once billing is fixed (``give_back_drive_attempt``).

State lives in two hidden OCR Settings fields (``billing_failure_since``,
``billing_alert_last_sent``), not in the cache: it must survive a cache flush
(a flush would otherwise re-send the email and blank the banner), and the
banner endpoint reads it.

Key discipline (same as the Jev trial): nothing in here ever receives, reads
or stores the API key, and nothing here calls ``frappe.get_traceback()`` or
logs an exception object. Every entry point swallows its own errors.
"""

from __future__ import annotations

import datetime
import re

import frappe
from frappe import _
from frappe.utils import now_datetime

ALERT_INTERVAL_HOURS = 6

# Words that mark a *prepay / credit* 429. Deliberately NOT bare "billing":
# Google's ordinary quota 429 reads "...please check your plan and billing
# details", and that one is a retryable rate limit.
_BILLING_WORDS = ("prepay", "credit", "billing account", "payment")


class GeminiBillingError(Exception):
	"""Google refused the call for payment reasons. Never retried in-job."""


def is_billing_failure(status_code, body_text) -> bool:
	"""True when an HTTP error response means "the billing account can't pay".

	* 402 is always a billing failure.
	* 429 counts only when the body says RESOURCE_EXHAUSTED *and* uses
	  prepay/credit wording. An ordinary rate-limit 429 stays retryable.
	"""
	if status_code == 402:
		return True
	if status_code == 429:
		body = (body_text or "").lower()
		return "resource_exhausted" in body and any(word in body for word in _BILLING_WORDS)
	return False


# --------------------------------------------------------------------- state


def _as_datetime(value) -> datetime.datetime:
	"""A Datetime setting may come back as a datetime or as its string form."""
	if isinstance(value, datetime.datetime):
		return value
	return datetime.datetime.fromisoformat(str(value))


def _read_dt(fieldname: str) -> datetime.datetime | None:
	"""Read a Datetime setting, or None when it is empty.

	Real Frappe casts an empty single Datetime (no row, NULL or "") to
	``datetime(1, 1, 1)``, which is truthy. Treat falsy values and anything
	before 1900 as "not set".
	"""
	value = frappe.db.get_single_value("OCR Settings", fieldname)
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		parsed = value
	elif isinstance(value, datetime.date):
		parsed = datetime.datetime(value.year, value.month, value.day)
	else:
		try:
			parsed = _as_datetime(value)
		except ValueError:
			return None
	return None if parsed.year < 1900 else parsed


def billing_is_down() -> bool:
	"""Cheap check used by the Drive pollers. Never raises."""
	try:
		return _read_dt("billing_failure_since") is not None
	except Exception:
		return False


def record_billing_failure() -> None:
	"""Mark billing as failing and email people, at most once per 6 hours. Never raises."""
	send_since = None
	try:
		now = now_datetime()
		since = _read_dt("billing_failure_since")
		if since is None:
			frappe.db.set_single_value("OCR Settings", "billing_failure_since", now, update_modified=False)
			since = now

		last_sent = _read_dt("billing_alert_last_sent")
		if last_sent is None or last_sent <= now - datetime.timedelta(hours=ALERT_INTERVAL_HOURS):
			# Stamp BEFORE sending so a mail failure can't turn into a mail storm.
			# Read-then-act with no lock: two jobs failing in the same instant can
			# both send. Accepted (one duplicate email at worst).
			frappe.db.set_single_value("OCR Settings", "billing_alert_last_sent", now, update_modified=False)
			send_since = since

		# Commit the STATE first: the caller rolls back on its way to marking the
		# record as Error, and the banner must survive that (and a mail failure).
		frappe.db.commit()  # nosemgrep
	except Exception as exc:
		_log_failure("Gemini billing alert: recording state failed", exc)
		return

	if send_since is None:
		return
	try:
		_send_alert(send_since)
		# A second commit so the queued email also survives the caller's rollback.
		frappe.db.commit()  # nosemgrep
	except Exception as exc:
		_log_failure("Gemini billing alert: sending email failed", exc)


def on_gemini_success() -> None:
	"""A Gemini call worked: clear the banner state. Never raises."""
	try:
		if _read_dt("billing_failure_since") is not None:
			frappe.db.set_single_value("OCR Settings", "billing_failure_since", None, update_modified=False)
			frappe.db.set_single_value("OCR Settings", "billing_alert_last_sent", None, update_modified=False)
			frappe.db.commit()  # nosemgrep
	except Exception as exc:
		_log_failure("Gemini billing alert: clearing state failed", exc)


def _log_failure(title: str, exc: BaseException | None = None) -> None:
	# The step (in `title`) and the exception CLASS NAME only: no message text, no
	# traceback, no locals (key-leak discipline).
	kind = type(exc).__name__ if exc is not None else "unknown"
	try:
		frappe.log_error(title=title, message=f"Exception type: {kind}")
	except Exception:
		pass


# --------------------------------------------------------------------- email


def get_alert_recipients() -> list[str]:
	"""System Managers (enabled, not Administrator/Guest) plus the settings list."""
	recipients: list[str] = []

	managers = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		fields=["parent"],
		order_by="parent asc",
	)
	names = sorted({m.parent for m in managers} - {"Administrator", "Guest"})
	if names:
		users = frappe.get_all(
			"User",
			filters={"name": ["in", names], "enabled": 1},
			fields=["name", "email"],
			order_by="name asc",
		)
		recipients.extend((u.email or u.name) for u in users)

	extra = frappe.db.get_single_value("OCR Settings", "billing_alert_recipients") or ""
	recipients.extend(parse_email_list(extra))

	seen, unique = set(), []
	for address in recipients:
		key = address.strip().lower()
		if key and key not in seen:
			seen.add(key)
			unique.append(address.strip())
	return unique


def parse_email_list(text: str) -> list[str]:
	"""Split a comma/semicolon/newline separated list of addresses."""
	return [part.strip() for part in re.split(r"[,;\s]+", text or "") if part.strip()]


def _send_alert(since) -> None:
	recipients = get_alert_recipients()
	if not recipients:
		return

	site = getattr(frappe.local, "site", "") or ""
	since_text = str(since)[:16]
	subject = _("OCR has stopped: Google is refusing payment for the AI service")
	message = _(
		"<p>The OCR system on {0} cannot read documents right now. Google refused payment for the AI "
		"service that reads them. This started at {1}.</p>"
		"<p><b>What to do:</b> top up, or fix the card on, the Google billing account for the project "
		"behind the OCR key. Reading documents starts again by itself once payment works.</p>"
		"<p><b>After that:</b> documents that failed in the meantime need to be run again. Open each "
		"one marked Error and press Retry Extraction. Scans dropped in the Google Drive folders are "
		"picked up again automatically.</p>"
		"<p>This email is sent at most once every {2} hours while the problem lasts.</p>"
	).format(site, since_text, ALERT_INTERVAL_HOURS)

	frappe.sendmail(recipients=recipients, subject=subject, message=message, now=False)


# -------------------------------------------------------------------- banner


@frappe.whitelist(methods=["GET"])
def get_billing_status() -> dict:
	"""Is Gemini billing failing? For the red banner on the OCR lists.

	Returns only ``{down, since}`` and is open to anyone who can read an OCR
	list. Nothing from the settings is exposed.
	"""
	if not (frappe.has_permission("OCR Import", "read") or frappe.has_permission("OCR Fleet Slip", "read")):
		frappe.throw(_("You do not have access to this."), frappe.PermissionError)

	since = _read_dt("billing_failure_since")
	return {"down": since is not None, "since": str(since) if since else None}


# ---------------------------------------------------------------- Drive heal


def drive_give_back_fields(exc, doctype: str, name: str) -> dict:
	"""Extra fields for the Error write so a billing failure does not count toward ``MAX_DRIVE_RETRIES``.

	The poller sets the next attempt's count to ``stored + 1``. Returning one less
	than this attempt's count makes the next attempt reuse the same number, so any
	number of billing failures leave the cap untouched. (-1 on a first attempt is
	intended: next attempt is 0 again.)

	Read-only: it writes and commits NOTHING. The caller merges the result into
	its single ``status = Error`` write, so there is never a committed state with a
	given-back count on a non-Error row (dedup would skip such a row forever).
	Returns ``{}`` for any other exception, a non-Drive record, or on any error.
	"""
	try:
		if not isinstance(exc, GeminiBillingError):
			return {}
		row = frappe.db.get_value(doctype, name, ["drive_file_id", "drive_retry_count"], as_dict=True)
		if row and row.get("drive_file_id"):
			return {"drive_retry_count": (row.get("drive_retry_count") or 0) - 1}
	except Exception as err:
		_log_failure("Gemini billing: Drive give-back lookup failed", err)
	return {}
