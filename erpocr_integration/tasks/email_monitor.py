# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import email
import imaplib
from email.header import decode_header

import frappe
from frappe import _

TERMINAL_SUCCESS_STATUSES = {"Needs Review", "Matched", "Completed"}
IN_PROGRESS_STATUSES = {"Pending", "Extracting", "Processing"}
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


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

		# Search for UNSEEN emails only (reduces load on growing folders)
		# Duplicate check via Message-ID handles edge cases
		status, messages = mail.uid("search", None, "UNSEEN")
		if status != "OK":
			frappe.log_error(title="Email Monitoring Error", message="Failed to search for emails")
			return

		email_uids = messages[0].split()

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
		# Re-select the folder in read-write mode so STORE commands work.
		if uids_to_move:
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
			uid_csv = b",".join(uids_to_move).decode()
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
				message_id = msg.get("Message-ID", "").strip()

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
				from erpocr_integration.api import SUPPORTED_FILE_TYPES, validate_file_magic_bytes

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
				frappe.db.commit()  # nosemgrep

				# Enqueue Gemini processing with stagger to avoid rate-limit stampede.
				# pdfs_to_process was already incremented, so subtract 1 for 0-based position.
				queue_pos = pdfs_to_process - 1
				stagger_delay = min(queue_pos * 5, 240)
				frappe.enqueue(
					"erpocr_integration.api.gemini_process",
					queue="long",
					timeout=300 + stagger_delay,
					pdf_content=pdf_content,
					filename=filename,
					ocr_import_name=ocr_import.name,
					source_type="Gemini Email",
					uploaded_by=uploaded_by,
					mime_type=file_mime_type,
					queue_position=queue_pos,
				)

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
	Move email from 'OCR Invoices' label to 'OCR Processed' label.

	In Gmail, labels work like folders in IMAP.
	We use Gmail's X-GM-LABELS extension for direct label manipulation.
	"""
	try:
		# Gmail X-GM-LABELS: labels with spaces must be in quotes AND parentheses
		# Try different formats to find what works
		label_attempts = [
			('"OCR Processed"', '"OCR Invoices"'),  # Quoted (standard for spaces)
			("OCR_Processed", "OCR_Invoices"),  # Underscores (if labels were created that way)
			("(OCR\\ Processed)", "(OCR\\ Invoices)"),  # Escaped spaces in parens
		]

		for idx, (add_label, remove_label) in enumerate(label_attempts, 1):
			try:
				# Add new label first
				if use_uid:
					status, _ = mail.uid("store", email_id, "+X-GM-LABELS", add_label)
				else:
					status, _ = mail.store(email_id, "+X-GM-LABELS", add_label)

				if status != "OK":
					raise Exception(f"Add label returned {status}")

				# Remove old label
				if use_uid:
					status, _ = mail.uid("store", email_id, "-X-GM-LABELS", remove_label)
				else:
					status, _ = mail.store(email_id, "-X-GM-LABELS", remove_label)

				if status != "OK":
					raise Exception(f"Remove label returned {status}")

				break  # If we got here without exception, it worked
			except Exception as e:
				if idx == len(label_attempts):
					# Last attempt failed, log comprehensive error
					frappe.log_error(
						title="Email Label Manipulation Failed",
						message=f"All label format attempts failed for email {email_id}\n\nLast error: {e!s}",
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
