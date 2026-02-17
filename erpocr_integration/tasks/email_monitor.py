# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import email
import imaplib
from email.header import decode_header

import frappe
from frappe import _

TERMINAL_SUCCESS_STATUSES = {"Needs Review", "Matched", "Completed"}
IN_PROGRESS_STATUSES = {"Pending", "Extracting", "Processing"}


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
		frappe.log_error("Email Monitoring Error", "Email monitoring enabled but no email account configured")
		return

	try:
		# Get Email Account settings
		email_account = frappe.get_doc("Email Account", settings.email_account)

		# Connect to IMAP
		mail = _connect_imap(email_account)

		# Debug: List all available folders
		status, folders = mail.list()
		if status == "OK":
			frappe.logger().info(f"Available IMAP folders: {folders}")

		# Select the OCR Invoices label/folder (for Gmail labels with spaces, use quotes)
		# Gmail labels appear as IMAP folders
		folder_name = '"OCR Invoices"'
		try:
			status, data = mail.select(folder_name)
			frappe.logger().info(f"Tried to select folder {folder_name}, status: {status}, data: {data}")
			if status != "OK":
				# Try without quotes as fallback
				status, data = mail.select("OCR Invoices")
				frappe.logger().info(f"Tried to select folder 'OCR Invoices' (no quotes), status: {status}, data: {data}")
				if status != "OK":
					frappe.log_error("Email Monitoring Error", f"Failed to select folder '{folder_name}'. Make sure the Gmail label 'OCR Invoices' exists.\n\nAvailable folders: {folders}")
					return
		except Exception as e:
			frappe.log_error("Email Monitoring Error", f"Failed to select folder '{folder_name}': {str(e)}\n\nAvailable folders: {folders}")
			return

		# Search for UNSEEN emails only (reduces load on growing folders)
		# Duplicate check via Message-ID handles edge cases
		status, messages = mail.uid('search', None, "UNSEEN")
		if status != "OK":
			frappe.log_error("Email Monitoring Error", "Failed to search for emails")
			return

		email_uids = messages[0].split()

		if not email_uids:
			# No emails in folder
			return

		frappe.logger().info(f"Email monitoring: Found {len(email_uids)} email(s) in OCR Invoices folder")

		# Process each email (using UIDs for better Gmail compatibility)
		for email_uid in email_uids:
			try:
				_process_email(mail, email_uid, email_account, settings, use_uid=True)
			except Exception:
				# Log error but continue with other emails
				frappe.log_error("Email Monitoring Error", f"Failed to process email UID {email_uid}\n{frappe.get_traceback()}")

		# Expunge deleted messages (if any were marked for deletion)
		try:
			mail.expunge()
		except Exception:
			pass  # Expunge might fail if no messages were deleted

		# Close connection
		mail.close()
		mail.logout()

	except Exception:
		frappe.log_error("Email Monitoring Error", f"Email monitoring failed\n{frappe.get_traceback()}")


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
	"""Process a single email and extract PDF attachments."""
	try:
		# Fetch email (use UID command if specified)
		if use_uid:
			status, msg_data = mail.uid('fetch', email_id, "(RFC822)")
		else:
			status, msg_data = mail.fetch(email_id, "(RFC822)")
		if status != "OK":
			frappe.logger().error(f"Email monitoring: Failed to fetch email {email_id}, status: {status}")
			return

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

				frappe.logger().info(f"Email monitoring: Processing email '{subject}' (Message-ID: {message_id})")

				# Extract PDF attachments
				pdfs = _extract_pdfs_from_email(msg)
				frappe.logger().info(f"Email monitoring: Found {len(pdfs)} PDF(s) in email '{subject}'")

		if not pdfs:
			# No PDFs in this email, move to OCR Processed
			frappe.logger().info(f"Email monitoring: No PDFs found in email '{subject}', moving to OCR Processed")
			_move_to_processed_folder(mail, email_id, use_uid=use_uid)
			return

		# Determine uploaded_by — use email_id only if it's a valid User, else Administrator
		uploaded_by = "Administrator"
		if email_account.email_id and frappe.db.exists("User", email_account.email_id):
			uploaded_by = email_account.email_id

		# Process each PDF (with per-PDF duplicate checking)
		all_succeeded = True
		pdfs_to_process = 0
		has_in_progress = False
		for pdf_content, filename in pdfs:
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
					frappe.logger().info(f"Email monitoring: PDF '{filename}' already in progress, skipping for now")
					continue

				if error_count >= 3:
					frappe.logger().warning(f"Email monitoring: PDF '{filename}' failed {error_count} times, giving up")
					continue

			pdfs_to_process += 1
			try:
				# Create placeholder OCR Import record
				ocr_import = frappe.get_doc({
					"doctype": "OCR Import",
					"status": "Pending",
					"source_filename": filename,
					"email_message_id": message_id,
					"source_type": "Gemini Email",
					"uploaded_by": uploaded_by,
					"company": settings.default_company,
				})
				ocr_import.insert(ignore_permissions=True)
				frappe.db.commit()  # nosemgrep

				# Enqueue Gemini processing
				frappe.enqueue(
					"erpocr_integration.api.gemini_process",
					queue="long",
					timeout=300,
					pdf_content=pdf_content,
					filename=filename,
					ocr_import_name=ocr_import.name,
					source_type="Gemini Email",
					uploaded_by=uploaded_by,
				)

				frappe.logger().info(f"Email monitoring: Created OCR Import {ocr_import.name} and enqueued PDF '{filename}' from email '{subject}'")

			except Exception:
				all_succeeded = False
				error_msg = frappe.get_traceback()
				frappe.log_error("Email Monitoring Error", f"Failed to process PDF {filename}\n{error_msg}")

				# Create Error OCR Import so the failure is visible in the list
				try:
					error_import = frappe.get_doc({
						"doctype": "OCR Import",
						"status": "Error",
						"source_filename": filename,
						"email_message_id": message_id,
						"source_type": "Gemini Email",
						"uploaded_by": uploaded_by,
						"company": settings.default_company,
					})
					error_import.insert(ignore_permissions=True)
					frappe.db.commit()  # nosemgrep
				except Exception:
					frappe.log_error("Email Monitoring Error", f"Failed to create error record for {filename}")

		# Move to processed if all PDFs were handled (success or skipped)
		if all_succeeded and not has_in_progress:
			_move_to_processed_folder(mail, email_id, use_uid=use_uid)
		elif pdfs_to_process == 0 and not has_in_progress:
			# All PDFs were already processed or permanently failed — move on
			_move_to_processed_folder(mail, email_id, use_uid=use_uid)
		else:
			frappe.logger().warning(
				f"Email monitoring: Not moving email '{subject}' to processed due to failures or in-progress jobs "
				f"(will retry next poll)"
			)
	except Exception:
		frappe.log_error("Email Monitoring Error", f"Failed to process email {email_id}\n{frappe.get_traceback()}")


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
			('OCR_Processed', 'OCR_Invoices'),  # Underscores (if labels were created that way)
			('(OCR\\ Processed)', '(OCR\\ Invoices)'),  # Escaped spaces in parens
		]

		success = False
		for idx, (add_label, remove_label) in enumerate(label_attempts, 1):
			try:
				# Add new label first
				if use_uid:
					result = mail.uid('store', email_id, '+X-GM-LABELS', add_label)
				else:
					result = mail.store(email_id, '+X-GM-LABELS', add_label)
				frappe.logger().info(f"Email monitoring: [Attempt {idx}] Added label {add_label}: {result}")

				# Remove old label
				if use_uid:
					result = mail.uid('store', email_id, '-X-GM-LABELS', remove_label)
				else:
					result = mail.store(email_id, '-X-GM-LABELS', remove_label)
				frappe.logger().info(f"Email monitoring: [Attempt {idx}] Removed label {remove_label}: {result}")

				success = True
				break  # If we got here without exception, it worked
			except Exception as e:
				frappe.logger().error(f"Email monitoring: [Attempt {idx}] Label format {add_label}/{remove_label} failed: {str(e)}")
				if idx == len(label_attempts):
					# Last attempt failed, log comprehensive error
					frappe.log_error("Email Label Manipulation Failed", f"All label format attempts failed for email {email_id}\n\nLast error: {str(e)}")

	except Exception:
		frappe.log_error("Email Monitoring Error", f"Failed to move email {email_id} to OCR Processed\n{frappe.get_traceback()}")


def _extract_pdfs_from_email(msg) -> list[tuple[bytes, str]]:
	"""
	Extract PDF attachments from email message.

	Returns:
		list: [(pdf_content, filename), ...]
	"""
	pdfs = []

	# Walk through email parts
	if msg.is_multipart():
		for part in msg.walk():
			# Get content type
			content_type = part.get_content_type()
			content_disposition = str(part.get("Content-Disposition", ""))

			# Check if it's an attachment
			if "attachment" in content_disposition or "inline" in content_disposition:
				filename = part.get_filename()
				# Check if it's a PDF
				is_pdf = content_type == "application/pdf"
				if not is_pdf and filename:
					is_pdf = filename.lower().endswith(".pdf")

				if is_pdf and filename:
					# Decode filename if needed
					filename = _decode_header_value(filename)

					# Get PDF content
					pdf_content = part.get_payload(decode=True)
					if pdf_content:
						pdfs.append((pdf_content, filename))
	else:
		# Single part email with PDF?
		content_type = msg.get_content_type()
		if content_type == "application/pdf":
			filename = msg.get_filename() or "invoice.pdf"
			filename = _decode_header_value(filename)
			pdf_content = msg.get_payload(decode=True)
			if pdf_content:
				pdfs.append((pdf_content, filename))

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
					result.append(part.decode('utf-8', errors='ignore'))
			else:
				result.append(part)
		return "".join(result)
	except Exception:
		return header_value


@frappe.whitelist()
def trigger_email_check():
	"""Manual trigger for email monitoring (for testing)."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"erpocr_integration.tasks.email_monitor.poll_email_inbox",
		queue="long"
	)
	return {"message": _("Email check triggered. Check background jobs for status.")}


@frappe.whitelist()
def list_imap_folders():
	"""Debug function to list all IMAP folders."""
	frappe.only_for("System Manager")

	settings = frappe.get_single("OCR Settings")
	if not settings.email_account:
		return {"error": "No email account configured"}

	email_account = frappe.get_doc("Email Account", settings.email_account)
	mail = _connect_imap(email_account)

	status, folders = mail.list()
	mail.logout()

	folder_list = [f.decode() for f in folders] if status == "OK" else []
	return {"folders": folder_list}
