# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import email
import imaplib
from email.header import decode_header

import frappe
from frappe import _

# Every status an OCR Import reaches after a successful extraction. The poll takes
# every message in the label (not just unread), so an import that has moved on to
# Draft Created / No Action must still count as done or the PDF is imported again.
TERMINAL_SUCCESS_STATUSES = {"Needs Review", "Matched", "Draft Created", "Completed", "No Action"}
IN_PROGRESS_STATUSES = {"Pending", "Extracting", "Processing"}
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
# Emails handled per hourly poll, oldest first; the rest wait for the next poll.
MAX_EMAILS_PER_POLL = 20


def poll_email_inbox():
	"""
	Scheduled job to check email inbox for invoice PDFs.

	Runs hourly (configured in hooks.py).
	"""
	# Get OCR Settings
	settings = frappe.get_single("OCR Settings")

	if not settings.email_monitoring_enabled:
		return

	if not settings.email_account:
		frappe.log_error(
			title="Email Monitoring Error", message="Email monitoring enabled but no email account configured"
		)
		return

	mail = None
	try:
		# Get Email Account settings
		email_account = frappe.get_doc("Email Account", settings.email_account)

		# Connect to IMAP
		mail = _connect_imap(email_account)

		# Select the OCR Invoices label/folder (for Gmail labels with spaces, use quotes)
		folder_name = '"OCR Invoices"'
		resolved_folder = _select_folder(mail, folder_name, readonly=True)
		if not resolved_folder:
			return

		# Search ALL messages in the label, not just unread: Gmail delivers mail you
		# forward to your own alias already read, and those were silently skipped
		# (153 waiting on SP, 2026-10-27). The label is the queue — every exit below
		# either moves the email out or is deduped per Message-ID + filename.
		status, messages = mail.uid("search", None, "ALL")
		if status != "OK":
			frappe.log_error(title="Email Monitoring Error", message="Failed to search for emails")
			return

		# UIDs ascend with arrival in the folder: take the oldest first, capped.
		email_uids = sorted(messages[0].split(), key=int)[:MAX_EMAILS_PER_POLL]

		if not email_uids:
			# No emails in folder
			return

		frappe.logger().info(f"Email monitoring: Found {len(email_uids)} email(s) in OCR Invoices folder")

		# Phase 1: Read-only — fetch and process emails.
		# Folder is opened with EXAMINE (readonly=True) so no flags can be
		# modified.  This prevents Gmail from marking messages as \Seen which
		# would propagate to the Inbox (Gmail \Seen is per-message, not per-label).
		uids_to_move = []
		for email_uid in email_uids:
			try:
				should_move = _process_email(mail, email_uid, email_account, settings, use_uid=True)
				if should_move:
					uids_to_move.append(email_uid)
			except Exception:
				# Log error but continue with other emails
				frappe.log_error(
					title="Email Monitoring Error",
					message=f"Failed to process email UID {email_uid}\n{frappe.get_traceback()}",
				)

		# Phase 2: Read-write — move processed emails to "OCR Processed".
		# Emails that did not finish stay in the label; the next poll retries
		# them (read state no longer matters).
		if email_uids:
			mail.close()
			if not _select_folder(mail, folder_name, readonly=False):
				frappe.log_error(
					title="Email Monitoring Error",
					message="Could not re-select folder in read-write mode for moving emails",
				)
				return

			# Validate UIDs still exist after re-select.  Handles two edge cases:
			# - UIDVALIDITY changed between phases (all UIDs become invalid)
			# - Messages moved/deleted between phases (individual UIDs gone)
			uid_csv = b",".join(email_uids).decode()
			status, data = mail.uid("search", None, f"UID {uid_csv}")
			if status == "OK" and data[0]:
				valid_uids = set(data[0].split())
				stale = [u for u in uids_to_move if u not in valid_uids]
				if stale:
					frappe.logger().warning(
						f"Email monitoring: {len(stale)} email(s) no longer in folder "
						f"(moved between phases), skipping"
					)
				uids_to_move = [u for u in uids_to_move if u in valid_uids]
			else:
				# Search failed or returned empty — none of the UIDs are valid
				frappe.logger().warning(
					"Email monitoring: UID validation returned no results, skipping all moves"
				)
				uids_to_move = []

			for email_uid in uids_to_move:
				try:
					# Mark as \Seen (cosmetic since the ALL search). If the move
					# below fails, the next poll re-reads the email and the
					# per-PDF dedup skips it, so the move is simply retried.
					mail.uid("store", email_uid, "+FLAGS", "\\Seen")
				except Exception:
					frappe.log_error(
						title="Email Monitoring Error",
						message=f"Failed to mark email UID {email_uid} as Seen\n{frappe.get_traceback()}",
					)

				try:
					_move_to_processed_folder(mail, email_uid, use_uid=True)
				except Exception:
					frappe.log_error(
						title="Email Monitoring Error",
						message=f"Failed to move email UID {email_uid} to processed\n{frappe.get_traceback()}",
					)

			# Expunge deleted messages (if any were marked for deletion)
			try:
				mail.expunge()
			except Exception:
				pass  # Expunge might fail if no messages were deleted

		# Close connection
		mail.close()
		mail.logout()
		mail = None  # Mark as closed

	except Exception:
		frappe.log_error(
			title="Email Monitoring Error", message=f"Email monitoring failed\n{frappe.get_traceback()}"
		)
	finally:
		# Ensure IMAP connection is always closed, even on error
		if mail:
			try:
				mail.logout()
			except Exception:
				pass


def _select_folder(mail, folder_name, readonly=False):
	"""
	Select an IMAP folder, trying quoted and unquoted variants.

	Args:
		mail: IMAP connection.
		folder_name: Folder name (e.g. '"OCR Invoices"').
		readonly: If True, open with EXAMINE (no flag modifications possible).

	Returns:
		The folder name variant that worked, or None on failure.
	"""
	for variant in (folder_name, folder_name.strip('"')):
		try:
			status, _data = mail.select(variant, readonly=readonly)
			if status == "OK":
				return variant
		except Exception:
			continue

	# All variants failed
	try:
		_status, folders = mail.list()
	except Exception:
		folders = "(unavailable)"
	frappe.log_error(
		title="Email Monitoring Error",
		message=f"Failed to select folder '{folder_name}'. Make sure the Gmail label 'OCR Invoices' exists.\n\nAvailable folders: {folders}",
	)
	return None


def _connect_imap(email_account):
	"""Connect to IMAP server using Email Account settings."""
	# Get IMAP settings
	email_server = email_account.email_server
	port = email_account.incoming_port or 993
	email_id = email_account.email_id
	password = email_account.get_password()

	# Connect to IMAP
	if email_account.use_ssl:
		mail = imaplib.IMAP4_SSL(email_server, port)
	else:
		mail = imaplib.IMAP4(email_server, port)

	# Login
	mail.login(email_id, password)

	return mail


def _process_email(mail, email_id, email_account, settings, use_uid=False):
	"""
	Process a single email and extract PDF attachments.

	Returns:
		True if the email should be moved to "OCR Processed" (all PDFs handled),
		False if it should stay in the folder (failures or in-progress jobs).
	"""
	try:
		# Fetch email using BODY.PEEK[] to avoid marking as \Seen (read).
		# RFC822 is equivalent to BODY[] which auto-sets \Seen flag.
		# On Gmail, \Seen is global per message — marking read in "OCR Invoices"
		# also marks read in Inbox. PEEK prevents this side effect.
		if use_uid:
			status, msg_data = mail.uid("fetch", email_id, "(BODY.PEEK[])")
		else:
			status, msg_data = mail.fetch(email_id, "(BODY.PEEK[])")
		if status != "OK":
			frappe.logger().error(f"Email monitoring: Failed to fetch email {email_id}, status: {status}")
			return False

		# Initialize variables
		pdfs = []
		message_id = None
		subject = ""

		# Parse email message
		for response_part in msg_data:
			if isinstance(response_part, tuple):
				msg = email.message_from_bytes(response_part[1])

				# Get email subject and Message-ID
				subject = _decode_header_value(msg.get("Subject", ""))
				# Strip angle brackets from Message-ID to avoid Frappe
				# HTML-encoding <> to &lt;&gt; in Data fields, which
				# breaks the dedup query (raw '<' != stored '&lt;').
				message_id = msg.get("Message-ID", "").strip().strip("<>")

				frappe.logger().info(
					f"Email monitoring: Processing email '{subject}' (Message-ID: {message_id})"
				)

				# Extract PDF attachments
				pdfs = _extract_pdfs_from_email(msg)
				frappe.logger().info(f"Email monitoring: Found {len(pdfs)} PDF(s) in email '{subject}'")

		if not pdfs:
			# No PDFs in this email — should be moved to OCR Processed
			frappe.logger().info(
				f"Email monitoring: No PDFs found in email '{subject}', will move to OCR Processed"
			)
			return True

		# Determine uploaded_by — use email_id only if it's a valid User, else Administrator
		uploaded_by = "Administrator"
		if email_account.email_id and frappe.db.exists("User", email_account.email_id):
			uploaded_by = email_account.email_id

		# Process each PDF (with per-PDF duplicate checking)
		all_succeeded = True
		pdfs_to_process = 0
		enqueued_count = 0
		has_in_progress = False
		for pdf_content, filename, attachment_content_type in pdfs:
			# Per-PDF duplicate check: skip this PDF if already successfully processed
			if message_id:
				existing = frappe.get_all(
					"OCR Import",
					filters={"email_message_id": message_id, "source_filename": filename},
					fields=["name", "status"],
				)
				success_records = [r for r in existing if r.status in TERMINAL_SUCCESS_STATUSES]
				in_progress_records = [r for r in existing if r.status in IN_PROGRESS_STATUSES]
				error_count = len([r for r in existing if r.status == "Error"])

				if success_records:
					frappe.logger().info(f"Email monitoring: PDF '{filename}' already processed, skipping")
					continue

				if in_progress_records:
					has_in_progress = True
					frappe.logger().info(
						f"Email monitoring: PDF '{filename}' already in progress, skipping for now"
					)
					continue

				if error_count >= 3:
					frappe.logger().warning(
						f"Email monitoring: PDF '{filename}' failed {error_count} times, giving up"
					)
					continue

			# Enforce same size limit as manual upload
			if len(pdf_content) > MAX_PDF_SIZE_BYTES:
				frappe.log_error(
					title="Email Monitoring Error",
					message=f"PDF too large (>{MAX_PDF_SIZE_BYTES // (1024 * 1024)}MB): {filename}",
				)
				continue

			pdfs_to_process += 1
			ocr_import = None
			try:
				# Determine MIME type: prefer email Content-Type header, fall back to filename extension
				from erpocr_integration.api import (
					SUPPORTED_FILE_TYPES,
					is_image_decodable,
					validate_file_magic_bytes,
				)

				if attachment_content_type in _SUPPORTED_EMAIL_MIME_TYPES:
					file_mime_type = attachment_content_type
				else:
					file_ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
					file_mime_type = SUPPORTED_FILE_TYPES.get(file_ext, "application/pdf")

				# Validate magic bytes before creating placeholder or enqueuing
				if not validate_file_magic_bytes(pdf_content, file_mime_type):
					frappe.logger().warning(
						f"Email monitoring: Skipping '{filename}' — content does not match "
						f"expected file type ({file_mime_type})"
					)
					continue

				# Decode-verify images (v1.8.0, Q7b) — same gate as every other
				# ingest path; an undecodable body 500s in PIL later otherwise.
				if file_mime_type.startswith("image/") and not is_image_decodable(pdf_content):
					frappe.logger().warning(
						f"Email monitoring: Skipping '{filename}' — image content is not decodable"
					)
					continue

				# Create placeholder OCR Import record
				ocr_import = frappe.get_doc(
					{
						"doctype": "OCR Import",
						"status": "Pending",
						"source_filename": filename,
						"email_message_id": message_id,
						"source_type": "Gemini Email",
						"uploaded_by": uploaded_by,
						"company": settings.default_company,
					}
				)
				ocr_import.insert(ignore_permissions=True)

				# Save file as private attachment for retry capability
				frappe.get_doc(
					{
						"doctype": "File",
						"file_name": filename,
						"attached_to_doctype": "OCR Import",
						"attached_to_name": ocr_import.name,
						"content": pdf_content,
						"is_private": 1,
					}
				).insert(ignore_permissions=True)

				frappe.db.commit()  # nosemgrep

				# Caller-side stagger: sleep 5s before every enqueue after the
				# first SUCCESSFUL one, so background workers don't all hit
				# Gemini at once. enqueued_count only counts successful enqueues,
				# so earlier-skipped attachments (magic-byte fail, etc.) don't
				# trigger an unnecessary sleep.
				if enqueued_count > 0:
					import time

					time.sleep(5)

				frappe.enqueue(
					"erpocr_integration.api.gemini_process",
					queue="long",
					timeout=600,
					pdf_content=pdf_content,
					filename=filename,
					ocr_import_name=ocr_import.name,
					source_type="Gemini Email",
					uploaded_by=uploaded_by,
					mime_type=file_mime_type,
				)
				enqueued_count += 1

				frappe.logger().info(
					f"Email monitoring: Created OCR Import {ocr_import.name} and enqueued '{filename}' from email '{subject}'"
				)

			except Exception:
				all_succeeded = False
				error_msg = frappe.get_traceback()
				frappe.log_error(
					title="Email Monitoring Error", message=f"Failed to process PDF {filename}\n{error_msg}"
				)

				# Mark the existing placeholder as Error (don't create a second record)
				try:
					if ocr_import and ocr_import.name:
						frappe.db.set_value("OCR Import", ocr_import.name, "status", "Error")
					else:
						# Placeholder wasn't created — create an Error record
						frappe.get_doc(
							{
								"doctype": "OCR Import",
								"status": "Error",
								"source_filename": filename,
								"email_message_id": message_id,
								"source_type": "Gemini Email",
								"uploaded_by": uploaded_by,
								"company": settings.default_company,
							}
						).insert(ignore_permissions=True)
					frappe.db.commit()  # nosemgrep
				except Exception:
					frappe.log_error(
						title="Email Monitoring Error",
						message=f"Failed to update error status for {filename}",
					)

		# Decide whether the email should be moved to "OCR Processed"
		if all_succeeded and not has_in_progress:
			return True
		if pdfs_to_process == 0 and not has_in_progress:
			# All PDFs were already processed or permanently failed — move on
			return True

		frappe.logger().warning(
			f"Email monitoring: Not moving email '{subject}' to processed due to failures or in-progress jobs "
			f"(will retry next poll)"
		)
		return False
	except Exception:
		frappe.log_error(
			title="Email Monitoring Error",
			message=f"Failed to process email {email_id}\n{frappe.get_traceback()}",
		)
		return False


def _move_to_processed_folder(mail, email_id, use_uid=False):
	"""
	Move email from 'OCR Invoices' folder to 'OCR Processed' folder.

	Uses standard IMAP COPY + STORE \\Deleted (expunge happens at end of the
	poll loop). This works reliably across Gmail, Google Workspace, and any
	other IMAP provider. Falls back to Gmail's X-GM-LABELS extension if the
	standard COPY fails — which preserves the previous behaviour for label-only
	setups that never had actual folders.

	Gmail note: Gmail exposes labels as IMAP folders, so COPY "OCR Invoices" →
	"OCR Processed" adds the destination label. STORE \\Deleted on the source
	folder then removes the source label (Gmail does not move to Trash from a
	non-Inbox label). Net effect: same as the old label-manipulation path, but
	via standard IMAP verbs.
	"""
	# Try the standard IMAP verbs first
	if _imap_copy_and_delete(mail, email_id, use_uid):
		return

	# Fall back to Gmail-specific X-GM-LABELS extension
	_gmail_label_move(mail, email_id, use_uid)


def _imap_copy_and_delete(mail, email_id, use_uid) -> bool:
	"""Try standard IMAP COPY + STORE \\Deleted. Returns True on success."""
	dest_attempts = ('"OCR Processed"', "OCR Processed", "OCR_Processed")
	for dest in dest_attempts:
		try:
			if use_uid:
				status, _ = mail.uid("copy", email_id, dest)
			else:
				status, _ = mail.copy(email_id, dest)
			if status != "OK":
				continue

			# Mark source copy for deletion. EXPUNGE runs once at end of poll.
			if use_uid:
				status, _ = mail.uid("store", email_id, "+FLAGS", "\\Deleted")
			else:
				status, _ = mail.store(email_id, "+FLAGS", "\\Deleted")
			if status == "OK":
				return True
		except Exception:
			continue
	return False


def _gmail_label_move(mail, email_id, use_uid) -> None:
	"""Fallback: Gmail X-GM-LABELS extension for label-only setups."""
	try:
		label_attempts = [
			('"OCR Processed"', '"OCR Invoices"'),
			("OCR_Processed", "OCR_Invoices"),
			("(OCR\\ Processed)", "(OCR\\ Invoices)"),
		]
		for idx, (add_label, remove_label) in enumerate(label_attempts, 1):
			try:
				if use_uid:
					status, _ = mail.uid("store", email_id, "+X-GM-LABELS", add_label)
				else:
					status, _ = mail.store(email_id, "+X-GM-LABELS", add_label)
				if status != "OK":
					raise Exception(f"Add label returned {status}")

				if use_uid:
					status, _ = mail.uid("store", email_id, "-X-GM-LABELS", remove_label)
				else:
					status, _ = mail.store(email_id, "-X-GM-LABELS", remove_label)
				if status != "OK":
					raise Exception(f"Remove label returned {status}")

				return
			except Exception as e:
				if idx == len(label_attempts):
					frappe.log_error(
						title="Email Label Manipulation Failed",
						message=(
							f"Both standard IMAP COPY+DELETE and X-GM-LABELS fallback failed "
							f"for email {email_id}\n\nLast error: {e!s}"
						),
					)
	except Exception:
		frappe.log_error(
			title="Email Monitoring Error",
			message=f"Failed to move email {email_id} to OCR Processed\n{frappe.get_traceback()}",
		)


# MIME types accepted from email attachments
_SUPPORTED_EMAIL_MIME_TYPES = {
	"application/pdf",
	"image/jpeg",
	"image/png",
}
_SUPPORTED_EMAIL_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _is_supported_attachment(content_type: str, filename: str | None) -> bool:
	"""Check if an email attachment is a supported file type."""
	if content_type in _SUPPORTED_EMAIL_MIME_TYPES:
		return True
	if filename:
		ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
		return ext in _SUPPORTED_EMAIL_EXTENSIONS
	return False


def _extract_pdfs_from_email(msg) -> list[tuple[bytes, str, str]]:
	"""
	Extract PDF and image attachments from email message.

	Returns:
		list: [(file_content, filename, content_type), ...]
	"""
	pdfs = []

	# Walk through email parts
	if msg.is_multipart():
		for part in msg.walk():
			# Get content type
			content_type = part.get_content_type()
			content_disposition = str(part.get("Content-Disposition", ""))

			# Check if it's an attachment (skip inline images — logos, signatures, tracking pixels)
			is_attachment = "attachment" in content_disposition
			is_inline_pdf = "inline" in content_disposition and content_type == "application/pdf"
			if is_attachment or is_inline_pdf:
				filename = part.get_filename()

				if _is_supported_attachment(content_type, filename) and filename:
					# Decode filename if needed
					filename = _decode_header_value(filename)

					# Get file content
					pdf_content = part.get_payload(decode=True)
					if pdf_content:
						pdfs.append((pdf_content, filename, content_type))
	else:
		# Single part email — require attachment disposition to avoid processing
		# bare image emails (e.g. camera snapshots sent without context)
		content_type = msg.get_content_type()
		content_disposition = str(msg.get("Content-Disposition", ""))
		if content_type in _SUPPORTED_EMAIL_MIME_TYPES and "attachment" in content_disposition:
			filename = msg.get_filename() or "invoice.pdf"
			filename = _decode_header_value(filename)
			pdf_content = msg.get_payload(decode=True)
			if pdf_content:
				pdfs.append((pdf_content, filename, content_type))

	return pdfs


def _decode_header_value(header_value: str) -> str:
	"""Decode email header value (subject, filename, etc.)."""
	if not header_value:
		return ""

	try:
		decoded_parts = decode_header(header_value)
		result = []
		for part, encoding in decoded_parts:
			if isinstance(part, bytes):
				if encoding:
					result.append(part.decode(encoding))
				else:
					result.append(part.decode("utf-8", errors="ignore"))
			else:
				result.append(part)
		return "".join(result)
	except Exception:
		return header_value


@frappe.whitelist(methods=["POST"])
def trigger_email_check():
	"""Manual trigger for email monitoring (for testing)."""
	frappe.only_for("System Manager")
	frappe.enqueue("erpocr_integration.tasks.email_monitor.poll_email_inbox", queue="long")
	return {"message": _("Email check triggered. Check background jobs for status.")}
