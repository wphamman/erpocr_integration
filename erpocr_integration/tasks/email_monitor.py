# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import email
import imaplib
from email.header import decode_header

import frappe
from frappe import _


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

		# Select inbox
		mail.select("INBOX")

		# Search for unread emails
		status, messages = mail.search(None, "UNSEEN")
		if status != "OK":
			frappe.log_error("Email Monitoring Error", "Failed to search for unread emails")
			return

		email_ids = messages[0].split()

		if not email_ids:
			# No unread emails
			return

		frappe.logger().info(f"Email monitoring: Found {len(email_ids)} unread emails")

		# Process each email
		for email_id in email_ids:
			try:
				_process_email(mail, email_id, email_account)
			except Exception:
				# Log error but continue with other emails
				frappe.log_error("Email Monitoring Error", f"Failed to process email {email_id}\n{frappe.get_traceback()}")

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


def _process_email(mail, email_id, email_account):
	"""Process a single email and extract PDF attachments."""
	# Fetch email
	status, msg_data = mail.fetch(email_id, "(RFC822)")
	if status != "OK":
		return

	# Parse email message
	for response_part in msg_data:
		if isinstance(response_part, tuple):
			msg = email.message_from_bytes(response_part[1])

			# Get email subject
			subject = _decode_header_value(msg.get("Subject", ""))

			# Extract PDF attachments
			pdfs = _extract_pdfs_from_email(msg)

			if not pdfs:
				# No PDFs in this email, mark as read and skip
				mail.store(email_id, '+FLAGS', '\\Seen')
				frappe.logger().info(f"Email monitoring: No PDFs found in email '{subject}'")
				continue

			# Process each PDF
			for pdf_content, filename in pdfs:
				try:
					# Enqueue Gemini processing
					frappe.enqueue(
						"erpocr_integration.api.gemini_process",
						queue="long",
						timeout=300,
						pdf_content=pdf_content,
						filename=filename,
						ocr_import_name=None,  # Will be created
						source_type="Gemini Email",
						uploaded_by=email_account.email_id,
					)

					frappe.logger().info(f"Email monitoring: Enqueued PDF '{filename}' from email '{subject}'")

				except Exception:
					frappe.log_error("Email Monitoring Error", f"Failed to enqueue PDF {filename}\n{frappe.get_traceback()}")

			# Mark email as read
			mail.store(email_id, '+FLAGS', '\\Seen')


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
				# Check if it's a PDF
				if content_type == "application/pdf" or part.get_filename().lower().endswith(".pdf"):
					filename = part.get_filename()
					if filename:
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
