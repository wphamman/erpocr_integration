"""Tests for poll_email_inbox's queue behaviour (read state, cap, dedup statuses)."""

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.tasks import email_monitor
from erpocr_integration.tasks.email_monitor import _process_email, poll_email_inbox


class FakeMail:
	"""Just enough IMAP for poll_email_inbox: records every uid() call."""

	def __init__(self, uids):
		self.uids = uids
		self.calls = []

	def uid(self, command, *args):
		self.calls.append((command, *args))
		if command == "search" and args[1] == "ALL":
			return "OK", [b" ".join(self.uids)]
		if command == "search":  # Phase 2 "UID <csv>" validation: all still present
			return "OK", [args[1].removeprefix("UID ").replace(",", " ").encode()]
		return "OK", [None]

	def close(self):
		pass

	def logout(self):
		pass

	def expunge(self):
		pass


@pytest.fixture
def poll(mock_frappe):
	mock_frappe.get_single.return_value = SimpleNamespace(
		email_monitoring_enabled=1, email_account="Invoices"
	)
	mock_frappe.log_error.reset_mock()

	def run(uids, processed=lambda uid: True):
		mail = FakeMail(uids)
		seen = []

		def fake_process(_mail, uid, *_a, **_k):
			seen.append(uid)
			return processed(uid)

		with (
			patch.object(email_monitor, "_connect_imap", return_value=mail),
			patch.object(email_monitor, "_select_folder", return_value='"OCR Invoices"'),
			patch.object(email_monitor, "_process_email", side_effect=fake_process),
			patch.object(email_monitor, "_move_to_processed_folder") as move,
		):
			poll_email_inbox()
		return mail, seen, [c.args[1] for c in move.call_args_list]

	return run


class TestPollQueue:
	def test_searches_all_not_unseen(self, poll):
		# Live SP 2026-10-27: mail forwarded to the own alias arrives read, and
		# the UNSEEN search skipped 153 of them.
		mail, seen, _ = poll([b"7"])
		searches = [c for c in mail.calls if c[0] == "search"]
		assert searches[0] == ("search", None, "ALL")
		assert seen == [b"7"]

	def test_oldest_first_and_capped(self, poll):
		uids = [str(n).encode() for n in (30, 2, 11, *range(100, 125))]
		_, seen, moved = poll(uids)
		assert len(seen) == email_monitor.MAX_EMAILS_PER_POLL
		assert seen[:3] == [b"2", b"11", b"30"]
		assert seen == sorted(seen, key=int)
		assert moved == seen

	def test_unfinished_email_stays_and_read_state_is_not_touched(self, poll):
		mail, _seen, moved = poll([b"1", b"2"], processed=lambda uid: uid == b"1")
		assert moved == [b"1"]
		assert not [c for c in mail.calls if c[0] == "store" and c[2] == "-FLAGS"]


def _email_with_pdf():
	msg = MIMEMultipart()
	msg["Subject"] = "Invoice"
	msg["Message-ID"] = "<abc@example.com>"
	part = MIMEApplication(b"%PDF-1.4 fake", Name="INV-1.pdf")
	part["Content-Disposition"] = 'attachment; filename="INV-1.pdf"'
	msg.attach(part)
	return msg.as_bytes()


class TestAlreadyImportedStatuses:
	@pytest.mark.parametrize("status", ["Needs Review", "Matched", "Draft Created", "Completed", "No Action"])
	def test_existing_import_in_any_success_status_is_not_reimported(self, mock_frappe, status):
		# With the ALL search an email can be read again; an import that moved on
		# to Draft Created / No Action must still count as done.
		mail = MagicMock()
		mail.uid.return_value = ("OK", [(b"1 (BODY[]", _email_with_pdf())])
		mock_frappe.get_all.return_value = [SimpleNamespace(name="OCR-IMP-1", status=status)]
		mock_frappe.enqueue.reset_mock()
		try:
			result = _process_email(mail, b"1", SimpleNamespace(email_id=None), MagicMock(), use_uid=True)
		finally:
			mock_frappe.get_all.return_value = []
		assert result is True
		mock_frappe.enqueue.assert_not_called()
