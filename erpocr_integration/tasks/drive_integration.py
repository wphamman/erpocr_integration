# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""
Google Drive integration for archiving invoice files.

This module handles:
- Uploading invoice files (PDF/JPEG/PNG) to Google Drive
- Creating folder structures (Year/Month/Supplier)
- Generating shareable links
- Service account authentication
- Scanning a Drive inbox folder for new invoice files
"""

import json

import frappe
from frappe import _
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_DRIVE_RETRIES = 3  # Stop retrying Drive files after this many extraction failures


def _mime_type_from_filename(filename: str) -> str:
	"""Determine MIME type from filename extension."""
	ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
	return {
		".pdf": "application/pdf",
		".jpg": "image/jpeg",
		".jpeg": "image/jpeg",
		".png": "image/png",
	}.get(ext, "application/pdf")


def upload_invoice_to_drive(
	pdf_content: bytes, filename: str, supplier_name: str | None = None, invoice_date: str | None = None
) -> dict:
	"""
	Upload invoice file to Google Drive with organized folder structure.

	Args:
		pdf_content: File content as bytes (PDF or image)
		filename: Original filename (e.g., "invoice-5478129904.pdf" or "receipt.jpg")
		supplier_name: Supplier name for folder organization (optional)
		invoice_date: Invoice date for year/month folders (optional, format: YYYY-MM-DD)

	Returns:
		dict: {
			"file_id": Google Drive file ID,
			"shareable_link": Direct link to view file,
			"folder_path": Archive folder path (e.g., "2026/01-January/Google")
		}

	Raises:
		Exception: If Drive integration is disabled or credentials are invalid
	"""
	settings = frappe.get_single("OCR Settings")

	if not settings.drive_integration_enabled:
		frappe.log_error(
			title="Drive Integration Disabled",
			message="Attempted to upload to Drive but integration is disabled",
		)
		return {"file_id": None, "shareable_link": None, "folder_path": None}

	sa_json = settings.get_password("drive_service_account_json")
	if not sa_json or not settings.drive_archive_folder_id:
		frappe.log_error(
			title="Drive Configuration Missing",
			message="Drive integration enabled but credentials or folder ID not configured",
		)
		return {"file_id": None, "shareable_link": None, "folder_path": None}

	try:
		# Get authenticated Drive service
		service = _get_drive_service(sa_json)

		# Build folder path: Archive Root / Year / Month / Supplier
		folder_path, parent_folder_id = _build_folder_structure(
			service, settings.drive_archive_folder_id, supplier_name, invoice_date
		)

		# Upload PDF to the target folder
		file_metadata = {"name": filename, "parents": [parent_folder_id]}

		media = MediaInMemoryUpload(pdf_content, mimetype=_mime_type_from_filename(filename), resumable=True)

		file = (
			service.files()
			.create(body=file_metadata, media_body=media, fields="id, webViewLink", supportsAllDrives=True)
			.execute()
		)

		file_id = file.get("id")
		web_view_link = file.get("webViewLink")

		frappe.logger().info(f"Drive: Uploaded {filename} to {folder_path} (ID: {file_id})")

		return {"file_id": file_id, "shareable_link": web_view_link, "folder_path": folder_path}

	except HttpError as e:
		frappe.log_error(title="Drive Upload Failed", message=f"Google Drive API error: {e!s}")
		return {"file_id": None, "shareable_link": None, "folder_path": None}

	except Exception as e:
		frappe.log_error(
			title="Drive Upload Failed", message=f"Drive upload error: {e!s}\n{frappe.get_traceback()}"
		)
		return {"file_id": None, "shareable_link": None, "folder_path": None}


def _get_drive_service(service_account_json: str):
	"""
	Create authenticated Google Drive service using service account credentials.

	Args:
		service_account_json: JSON string containing service account credentials

	Returns:
		Google Drive API service object

	Raises:
		ValueError: If JSON is invalid
		Exception: If authentication fails
	"""
	try:
		credentials_dict = json.loads(service_account_json)
		credentials = service_account.Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
		service = build("drive", "v3", credentials=credentials)
		return service

	except json.JSONDecodeError as e:
		raise ValueError(f"Invalid service account JSON: {e!s}") from e

	except Exception as e:
		raise Exception(f"Failed to authenticate with Google Drive: {e!s}") from e


def _build_folder_structure(
	service, root_folder_id: str, supplier_name: str | None = None, invoice_date: str | None = None
) -> tuple:
	"""
	Create folder hierarchy: Archive Root / Year / Month / Supplier

	Args:
		service: Authenticated Drive service
		root_folder_id: Root archive folder ID
		supplier_name: Supplier name (optional)
		invoice_date: Invoice date string YYYY-MM-DD (optional)

	Returns:
		tuple: (folder_path_string, final_folder_id)
		Example: ("2026/01-January/Google", "folder-id-xyz")
	"""
	import datetime

	current_folder_id = root_folder_id
	path_parts = []

	# Create Year folder (e.g., "2026")
	if invoice_date:
		try:
			date_obj = datetime.datetime.strptime(invoice_date, "%Y-%m-%d")
			year = str(date_obj.year)
			month_name = date_obj.strftime("%m-%B")  # "01-January"
		except (ValueError, TypeError):
			year = str(datetime.datetime.now().year)
			month_name = datetime.datetime.now().strftime("%m-%B")
	else:
		year = str(datetime.datetime.now().year)
		month_name = datetime.datetime.now().strftime("%m-%B")

	year_folder_id = _get_or_create_folder(service, year, current_folder_id)
	path_parts.append(year)
	current_folder_id = year_folder_id

	# Create Month folder (e.g., "01-January")
	month_folder_id = _get_or_create_folder(service, month_name, current_folder_id)
	path_parts.append(month_name)
	current_folder_id = month_folder_id

	# Create Supplier folder (e.g., "Google") if supplier name provided
	if supplier_name:
		# Clean supplier name for folder (remove special characters)
		clean_supplier = "".join(c for c in supplier_name if c.isalnum() or c in (" ", "-", "_")).strip()
		if not clean_supplier:
			clean_supplier = "Unknown"

		supplier_folder_id = _get_or_create_folder(service, clean_supplier, current_folder_id)
		path_parts.append(clean_supplier)
		current_folder_id = supplier_folder_id

	folder_path = "/".join(path_parts)
	return folder_path, current_folder_id


def _get_or_create_folder(service, folder_name: str, parent_folder_id: str) -> str:
	"""
	Get existing folder or create new one.

	Handles race conditions: if two concurrent jobs try to create the same
	folder, one may fail. In that case, re-search for the folder.

	Args:
		service: Authenticated Drive service
		folder_name: Name of folder to find/create
		parent_folder_id: Parent folder ID

	Returns:
		str: Folder ID
	"""
	# Search for existing folder
	query = f"name='{folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"

	try:
		results = (
			service.files()
			.list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True)
			.execute()
		)
		files = results.get("files", [])

		if files:
			# Folder exists, return its ID
			return files[0]["id"]

		# Folder doesn't exist, create it
		file_metadata = {
			"name": folder_name,
			"mimeType": "application/vnd.google-apps.folder",
			"parents": [parent_folder_id],
		}

		try:
			folder = service.files().create(body=file_metadata, fields="id", supportsAllDrives=True).execute()
			return folder.get("id")
		except HttpError:
			# Race condition: another job may have created the folder — re-search
			results = (
				service.files()
				.list(
					q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True
				)
				.execute()
			)
			files = results.get("files", [])
			if files:
				return files[0]["id"]
			raise  # Re-raise if still no folder found

	except HttpError as e:
		frappe.log_error(
			title="Drive Folder Creation Error", message=f"Failed to create folder {folder_name}: {e!s}"
		)
		raise


def download_file_from_drive(file_id: str) -> bytes | None:
	"""
	Download a file from Google Drive by its file ID.

	Args:
		file_id: Google Drive file ID

	Returns:
		bytes: File content, or None on failure
	"""
	settings = frappe.get_single("OCR Settings")
	sa_json = settings.get_password("drive_service_account_json")

	if not sa_json:
		return None

	try:
		from io import BytesIO

		from googleapiclient.http import MediaIoBaseDownload

		service = _get_drive_service(sa_json)
		request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

		buffer = BytesIO()
		downloader = MediaIoBaseDownload(buffer, request)

		done = False
		while not done:
			_status, done = downloader.next_chunk()

		return buffer.getvalue()

	except Exception as e:
		frappe.log_error(title="Drive Download Failed", message=f"Failed to download file {file_id}: {e!s}")
		return None


def poll_drive_scan_folder():
	"""
	Scheduled job (every 15 min) — scan Drive inbox folder for new PDFs.

	Lists PDF files in the configured scan folder, skips any already processed
	(dedup via drive_file_id), downloads content, and enqueues Gemini extraction.
	Staggers enqueue calls by 5 seconds to avoid bursting the Gemini rate limit.
	"""
	import time

	settings = frappe.get_single("OCR Settings")
	if not settings.drive_integration_enabled or not settings.drive_scan_folder_id:
		return

	sa_json = settings.get_password("drive_service_account_json")
	if not sa_json:
		return

	try:
		service = _get_drive_service(sa_json)
		files = _list_pdf_files(service, settings.drive_scan_folder_id)
	except Exception as e:
		frappe.log_error(title="Drive Scan Error", message=f"Failed to list scan folder: {e!s}")
		return

	if not files:
		return

	frappe.logger().info(f"Drive scan: Found {len(files)} file(s) in scan folder")

	enqueued_count = 0
	for file_info in files:
		try:
			was_enqueued = _process_scan_file(service, file_info, settings, queue_position=enqueued_count)
			if was_enqueued:
				enqueued_count += 1
				# Stagger requests: wait 5s between enqueues so background workers
				# don't all hit Gemini at once (free tier = 15 RPM)
				time.sleep(5)
		except Exception as e:
			frappe.log_error(
				title="Drive Scan Error", message=f"Failed to process {file_info.get('name', '?')}: {e!s}"
			)
			continue

	if enqueued_count:
		frappe.logger().info(f"Drive scan: Enqueued {enqueued_count} file(s) for processing")


def _process_scan_file(service, file_info: dict, settings, queue_position: int = 0) -> bool:
	"""
	Process a single file (PDF or image) from the Drive scan folder.

	Handles dedup (skips files already processed successfully), auto-retries
	previously failed extractions, and enqueues new files for Gemini extraction.

	Returns:
		True if a Gemini extraction job was enqueued, False if file was skipped.
	"""
	drive_file_id = file_info["id"]
	filename = file_info["name"]
	file_mime_type = file_info.get("mimeType", _mime_type_from_filename(filename))

	# Track retry count (0 for first attempt, incremented on each retry)
	_next_retry_count = 0

	# Dedup: check ALL OCR Import rows for this drive_file_id
	# (multi-invoice PDFs create multiple rows with the same drive_file_id)
	existing_rows = frappe.get_all(
		"OCR Import",
		filters={"drive_file_id": drive_file_id},
		fields=["name", "status", "drive_retry_count"],
	)
	if existing_rows:
		all_error = all(row.status == "Error" for row in existing_rows)
		if all_error:
			# Check retry cap to prevent infinite Gemini calls on permanently bad files
			max_retry_count = max(getattr(row, "drive_retry_count", 0) or 0 for row in existing_rows)
			if max_retry_count >= MAX_DRIVE_RETRIES:
				frappe.logger().warning(
					f"Drive scan: {filename} failed {max_retry_count} time(s), giving up "
					f"(max retries: {MAX_DRIVE_RETRIES})"
				)
				return False

			# All records failed and under retry cap — delete so file can be retried
			for row in existing_rows:
				frappe.delete_doc("OCR Import", row.name, force=True, ignore_permissions=True)
			frappe.db.commit()  # nosemgrep
			_next_retry_count = max_retry_count + 1
			frappe.logger().info(
				f"Drive scan: Retrying previously failed {filename} "
				f"(attempt {_next_retry_count}/{MAX_DRIVE_RETRIES}, "
				f"{len(existing_rows)} record(s) cleared)"
			)
		else:
			# At least one record succeeded or is still processing — skip
			return False

	# Download file content
	pdf_content = _download_file(service, drive_file_id)
	if not pdf_content:
		frappe.log_error(title="Drive Scan Error", message=f"Empty content for {filename}")
		return False

	# Enforce same size limit as manual upload
	if len(pdf_content) > MAX_PDF_SIZE_BYTES:
		frappe.log_error(
			title="Drive Scan Error",
			message=f"File too large (>{MAX_PDF_SIZE_BYTES // (1024 * 1024)}MB): {filename}",
		)
		return False

	# Validate magic bytes before creating placeholder or enqueuing
	from erpocr_integration.api import validate_file_magic_bytes

	if not validate_file_magic_bytes(pdf_content, file_mime_type):
		frappe.log_error(
			title="Drive Scan Error",
			message=f"File '{filename}' content does not match expected type ({file_mime_type}). Skipping.",
		)
		return False

	# Create OCR Import placeholder with drive_file_id for dedup
	ocr_import = frappe.get_doc(
		{
			"doctype": "OCR Import",
			"status": "Pending",
			"source_filename": filename,
			"source_type": "Gemini Drive Scan",
			"uploaded_by": "Administrator",
			"company": settings.default_company,
			"drive_file_id": drive_file_id,
			"drive_retry_count": _next_retry_count,
		}
	)
	ocr_import.insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep

	# Enqueue Gemini extraction
	try:
		stagger_delay = min(queue_position * 5, 240)
		frappe.enqueue(
			"erpocr_integration.api.gemini_process",
			queue="long",
			timeout=300 + stagger_delay,
			pdf_content=pdf_content,
			filename=filename,
			ocr_import_name=ocr_import.name,
			source_type="Gemini Drive Scan",
			uploaded_by="Administrator",
			mime_type=file_mime_type,
			queue_position=queue_position,
		)
		frappe.logger().info(f"Drive scan: Queued {filename} for processing")
		return True
	except Exception as e:
		# Delete placeholder so next poll can retry this file
		frappe.delete_doc("OCR Import", ocr_import.name, force=True, ignore_permissions=True)
		frappe.db.commit()  # nosemgrep
		frappe.log_error(title="Drive Scan Enqueue Failed", message=f"Failed to enqueue {filename}: {e!s}")
		return False


def _list_pdf_files(service, folder_id: str) -> list[dict]:
	"""
	List all supported files (PDF + images) in a Google Drive folder (with pagination).

	Args:
		service: Authenticated Drive service
		folder_id: Google Drive folder ID

	Returns:
		list[dict]: Each dict has 'id', 'name', and 'mimeType' keys
	"""
	mime_filter = " or ".join(
		[
			"mimeType='application/pdf'",
			"mimeType='image/jpeg'",
			"mimeType='image/png'",
		]
	)
	query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"
	all_files = []
	page_token = None

	while True:
		results = (
			service.files()
			.list(
				q=query,
				fields="nextPageToken, files(id, name, mimeType)",
				pageSize=100,
				pageToken=page_token,
				supportsAllDrives=True,
				includeItemsFromAllDrives=True,
			)
			.execute()
		)

		all_files.extend(results.get("files", []))
		page_token = results.get("nextPageToken")
		if not page_token:
			break

	return all_files


def _download_file(service, file_id: str) -> bytes | None:
	"""
	Download a file from Google Drive using an existing service object.

	Args:
		service: Authenticated Drive service
		file_id: Google Drive file ID

	Returns:
		bytes: File content, or None on failure
	"""
	from io import BytesIO

	from googleapiclient.http import MediaIoBaseDownload

	request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

	buffer = BytesIO()
	downloader = MediaIoBaseDownload(buffer, request)

	done = False
	while not done:
		_status, done = downloader.next_chunk()

	return buffer.getvalue()


def move_file_to_archive(
	file_id: str, supplier_name: str | None = None, invoice_date: str | None = None
) -> dict:
	"""
	Move a file from the scan inbox folder to the archive folder structure.

	Used after Drive scan extraction — moves the processed PDF into
	Year/Month/Supplier archive hierarchy and returns updated metadata.

	Args:
		file_id: Google Drive file ID to move
		supplier_name: Supplier name for folder organization
		invoice_date: Invoice date for year/month folders (YYYY-MM-DD)

	Returns:
		dict: {"file_id", "shareable_link", "folder_path"}
	"""
	settings = frappe.get_single("OCR Settings")

	if not settings.drive_integration_enabled or not settings.drive_archive_folder_id:
		return {"file_id": file_id, "shareable_link": None, "folder_path": None}

	sa_json = settings.get_password("drive_service_account_json")
	if not sa_json:
		return {"file_id": file_id, "shareable_link": None, "folder_path": None}

	try:
		service = _get_drive_service(sa_json)

		# Build archive folder structure (Year/Month/Supplier)
		folder_path, target_folder_id = _build_folder_structure(
			service, settings.drive_archive_folder_id, supplier_name, invoice_date
		)

		# Get current parent(s) and link
		file_info = (
			service.files()
			.get(fileId=file_id, fields="parents, webViewLink", supportsAllDrives=True)
			.execute()
		)

		previous_parents = ",".join(file_info.get("parents", []))
		web_view_link = file_info.get("webViewLink")

		# Move file: remove old parent(s), add archive folder
		updated = (
			service.files()
			.update(
				fileId=file_id,
				addParents=target_folder_id,
				removeParents=previous_parents,
				supportsAllDrives=True,
				fields="id, webViewLink",
			)
			.execute()
		)

		web_view_link = updated.get("webViewLink") or web_view_link

		frappe.logger().info(f"Drive: Moved {file_id} to archive: {folder_path}")

		return {"file_id": file_id, "shareable_link": web_view_link, "folder_path": folder_path}

	except Exception as e:
		frappe.log_error(title="Drive Move Error", message=f"Failed to move {file_id} to archive: {e!s}")
		return {"file_id": file_id, "shareable_link": None, "folder_path": None}


@frappe.whitelist(methods=["POST"])
def test_drive_connection():
	"""Test Drive connection and credentials. Returns folder list or error."""
	frappe.only_for("System Manager")

	settings = frappe.get_single("OCR Settings")

	if not settings.drive_integration_enabled:
		return {"success": False, "message": "Drive integration is disabled"}

	sa_json = settings.get_password("drive_service_account_json")
	if not sa_json:
		return {"success": False, "message": "Service account JSON not configured"}

	if not settings.drive_archive_folder_id:
		return {"success": False, "message": "Archive folder ID not configured"}

	try:
		service = _get_drive_service(sa_json)

		# Try to get the root folder details
		folder = (
			service.files()
			.get(fileId=settings.drive_archive_folder_id, fields="id, name", supportsAllDrives=True)
			.execute()
		)

		return {
			"success": True,
			"message": f"Connection successful! Archive folder: {folder.get('name')} (ID: {folder.get('id')})",
		}

	except Exception as e:
		return {"success": False, "message": f"Connection failed: {e!s}"}
