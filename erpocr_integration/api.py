# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _


@frappe.whitelist(methods=["POST"])
def upload_pdf():
	"""
	Upload PDF for Gemini OCR extraction.

	URL: /api/method/erpocr_integration.api.upload_pdf

	Expects multipart/form-data with 'file' field.

	Returns:
		dict: {"ocr_import": name, "status": "processing"}
	"""
	# Validate user has permission (Accounts User or System Manager)
	if not frappe.has_permission("OCR Import", "create"):
		frappe.throw(_("You do not have permission to upload invoices"))

	# Get uploaded file
	if not frappe.request or not frappe.request.files:
		frappe.throw(_("No file uploaded"))

	file = frappe.request.files.get("file")
	if not file:
		frappe.throw(_("No file found in request"))

	# Validate file type
	filename = file.filename
	if not filename.lower().endswith(".pdf"):
		frappe.throw(_("Only PDF files are supported"))

	# Validate file size (10MB max)
	file.seek(0, 2)  # Seek to end
	file_size = file.tell()
	file.seek(0)  # Reset to beginning

	max_size = 10 * 1024 * 1024  # 10MB
	if file_size > max_size:
		frappe.throw(
			_("File too large. Maximum size is 10MB. Your file is {0:.2f}MB").format(
				file_size / (1024 * 1024)
			)
		)

	# Read file content
	pdf_content = file.read()

	# Get company from OCR Settings
	settings = frappe.get_single("OCR Settings")
	if not settings.default_company:
		frappe.throw(_("Please set Default Company in OCR Settings"))

	# Create placeholder OCR Import record
	ocr_import = frappe.get_doc(
		{
			"doctype": "OCR Import",
			"status": "Pending",
			"source_filename": filename,
			"source_type": "Gemini Manual Upload",
			"uploaded_by": frappe.session.user,
			"company": settings.default_company,
		}
	)
	ocr_import.insert(ignore_permissions=True)
	frappe.db.commit()

	# Enqueue background processing
	try:
		frappe.enqueue(
			"erpocr_integration.api.gemini_process",
			queue="long",
			timeout=300,  # 5 minutes
			pdf_content=pdf_content,
			filename=filename,
			ocr_import_name=ocr_import.name,
			source_type="Gemini Manual Upload",
			uploaded_by=frappe.session.user,
		)
	except Exception:
		# Enqueue failed — mark placeholder as Error so it doesn't sit as stale Pending
		frappe.db.set_value("OCR Import", ocr_import.name, "status", "Error")
		frappe.db.commit()
		frappe.log_error(
			title="OCR Upload Error",
			message=f"Failed to enqueue processing for {filename}\n{frappe.get_traceback()}",
		)
		frappe.throw(_("Failed to start processing. Please try again."))

	return {"ocr_import": ocr_import.name, "status": "processing"}


def gemini_process(
	pdf_content: bytes,
	filename: str,
	ocr_import_name: str,
	source_type: str = "Gemini Manual Upload",
	uploaded_by: str | None = None,
):
	"""
	Background job to process PDF via Gemini API and create OCR Import(s).

	Supports multi-invoice PDFs — creates one OCR Import per invoice found.
	The first invoice updates the existing placeholder record (ocr_import_name).
	Additional invoices create new OCR Import records.

	Args:
		pdf_content: Raw PDF file bytes
		filename: Original filename
		ocr_import_name: Name of the OCR Import record to update
		source_type: "Gemini Manual Upload", "Gemini Email", or "Gemini Drive Scan"
		uploaded_by: User who initiated the upload
	"""
	frappe.set_user("Administrator")

	try:
		# Update status to "Extracting"
		frappe.db.set_value("OCR Import", ocr_import_name, "status", "Pending")
		frappe.db.commit()

		# Publish realtime update
		frappe.publish_realtime(
			event="ocr_extraction_progress",
			message={
				"ocr_import": ocr_import_name,
				"status": "Extracting",
				"message": "Calling Gemini API...",
			},
			user=uploaded_by,
		)

		# Call Gemini API — returns list of invoices (usually 1, but may be multiple)
		from erpocr_integration.tasks.gemini_extract import extract_invoice_data

		invoice_list = extract_invoice_data(pdf_content, filename)

		# Publish realtime update
		invoice_count = len(invoice_list)
		msg = "Matching suppliers and items..."
		if invoice_count > 1:
			msg = f"Found {invoice_count} invoices. Matching suppliers and items..."
		frappe.publish_realtime(
			event="ocr_extraction_progress",
			message={"ocr_import": ocr_import_name, "status": "Processing", "message": msg},
			user=uploaded_by,
		)

		settings = frappe.get_cached_doc("OCR Settings")

		# Drive: upload new file now, or defer move for scan files until after processing
		drive_result = {"file_id": None, "shareable_link": None, "folder_path": None}
		existing_drive_file_id = frappe.db.get_value("OCR Import", ocr_import_name, "drive_file_id")
		first_header = invoice_list[0].get("header_fields", {}) if invoice_list else {}

		if not existing_drive_file_id:
			# Upload new file to Drive (manual upload / email)
			try:
				from erpocr_integration.tasks.drive_integration import upload_invoice_to_drive

				drive_result = upload_invoice_to_drive(
					pdf_content=pdf_content,
					filename=filename,
					supplier_name=first_header.get("supplier_name", ""),
					invoice_date=first_header.get("invoice_date"),
				)
				if drive_result.get("file_id"):
					frappe.logger().info(f"Uploaded {filename} to Drive: {drive_result['folder_path']}")
			except Exception as e:
				frappe.log_error(
					title="Drive Upload Failed", message=f"Failed to upload {filename} to Drive: {e!s}"
				)
		else:
			# Drive scan: keep file_id reference, move to archive after processing succeeds
			drive_result = {"file_id": existing_drive_file_id, "shareable_link": None, "folder_path": None}

		# Process each invoice
		placeholder_doc = frappe.get_doc("OCR Import", ocr_import_name)
		for idx, extracted_data in enumerate(invoice_list):
			if idx == 0:
				ocr_import = placeholder_doc
			else:
				# Additional invoices create new records — copy source metadata
				ocr_import = frappe.get_doc(
					{
						"doctype": "OCR Import",
						"status": "Pending",
						"source_filename": filename,
						"source_type": source_type,
						"uploaded_by": uploaded_by or frappe.session.user,
						"company": settings.default_company,
						"email_message_id": placeholder_doc.email_message_id,
					}
				)

			_populate_ocr_import(ocr_import, extracted_data, settings, drive_result)
			_run_matching(ocr_import, extracted_data.get("header_fields", {}), settings)

			if idx == 0:
				ocr_import.save(ignore_permissions=True)
			else:
				ocr_import.insert(ignore_permissions=True)

		frappe.db.commit()

		# Drive scan: move file to archive AFTER successful processing
		if existing_drive_file_id:
			try:
				from erpocr_integration.tasks.drive_integration import move_file_to_archive

				drive_result = move_file_to_archive(
					file_id=existing_drive_file_id,
					supplier_name=first_header.get("supplier_name", ""),
					invoice_date=first_header.get("invoice_date"),
				)
				if drive_result.get("folder_path"):
					frappe.logger().info(f"Moved {filename} to Drive archive: {drive_result['folder_path']}")
					# Update all OCR Imports from this PDF with archive info
					for doc_name in frappe.get_all(
						"OCR Import", filters={"drive_file_id": existing_drive_file_id}, pluck="name"
					):
						frappe.db.set_value(
							"OCR Import",
							doc_name,
							{
								"drive_link": drive_result.get("shareable_link"),
								"drive_folder_path": drive_result.get("folder_path"),
							},
						)
					frappe.db.commit()
			except Exception as e:
				frappe.log_error(
					title="Drive Move Failed", message=f"Failed to move {filename} to archive: {e!s}"
				)

		# Publish realtime update
		ocr_import_first = frappe.get_doc("OCR Import", ocr_import_name)
		msg = "Extraction complete!"
		if invoice_count > 1:
			msg = f"Extraction complete! {invoice_count} invoices created."
		frappe.publish_realtime(
			event="ocr_extraction_progress",
			message={"ocr_import": ocr_import_name, "status": ocr_import_first.status, "message": msg},
			user=uploaded_by,
		)

	except Exception as e:
		# Update status to Error
		try:
			error_log = frappe.log_error(
				title="OCR Integration Error",
				message=f"Gemini extraction failed for {filename}\n{frappe.get_traceback()}",
			)
			frappe.db.set_value(
				"OCR Import", ocr_import_name, {"status": "Error", "error_log": error_log.name}
			)
			frappe.db.commit()

			# Publish realtime update
			frappe.publish_realtime(
				event="ocr_extraction_progress",
				message={
					"ocr_import": ocr_import_name,
					"status": "Error",
					"message": f"Extraction failed: {e!s}",
				},
				user=uploaded_by,
			)
		except Exception:
			# Even error handling failed
			frappe.log_error(title="OCR Integration Critical Error", message=frappe.get_traceback())


def _populate_ocr_import(ocr_import, extracted_data: dict, settings, drive_result: dict):
	"""Populate an OCR Import record with extracted invoice data and Drive info."""
	header_fields = extracted_data.get("header_fields", {})
	line_items = extracted_data.get("line_items", [])

	ocr_import.extraction_time = extracted_data.get("extraction_time", 0.0)
	ocr_import.supplier_name_ocr = header_fields.get("supplier_name", "")
	ocr_import.invoice_number = header_fields.get("invoice_number", "")
	ocr_import.invoice_date = header_fields.get("invoice_date")
	ocr_import.due_date = header_fields.get("due_date")
	ocr_import.subtotal = header_fields.get("subtotal", 0.0)
	ocr_import.tax_amount = header_fields.get("tax_amount", 0.0)
	ocr_import.total_amount = header_fields.get("total_amount", 0.0)
	ocr_import.currency = header_fields.get("currency", "")
	try:
		raw_confidence = float(header_fields.get("confidence") or 0.0)
	except (ValueError, TypeError):
		raw_confidence = 0.0
	ocr_import.confidence = max(0.0, min(100.0, raw_confidence * 100))  # Clamp to 0-100
	ocr_import.raw_payload = extracted_data.get("raw_response", "")

	# Auto-set tax template based on whether tax was detected
	tax_amount = float(header_fields.get("tax_amount") or 0)
	if tax_amount > 0:
		ocr_import.tax_template = settings.default_tax_template
	else:
		ocr_import.tax_template = settings.non_vat_tax_template

	# Add line items
	ocr_import.items = []
	for line in line_items:
		description = line.get("description", "")
		product_code = line.get("product_code", "")
		ocr_import.append(
			"items",
			{
				"description_ocr": description,
				"item_name": product_code or description,
				"qty": line.get("quantity", 1.0),
				"rate": line.get("unit_price", 0.0),
				"amount": line.get("amount", 0.0),
				"match_status": "Unmatched",
			},
		)

	# Drive info (shared across all invoices from same PDF)
	if drive_result.get("file_id"):
		ocr_import.drive_file_id = drive_result["file_id"]
		ocr_import.drive_link = drive_result["shareable_link"]
		ocr_import.drive_folder_path = drive_result["folder_path"]


def _run_matching(ocr_import, header_fields: dict, settings):
	"""Run supplier and item matching on an OCR Import record."""
	from erpocr_integration.tasks.matching import (
		match_item,
		match_item_fuzzy,
		match_service_item,
		match_supplier,
		match_supplier_fuzzy,
	)

	fuzzy_threshold = settings.matching_threshold or 80

	# Supplier matching
	if ocr_import.supplier_name_ocr:
		matched_supplier, match_status = match_supplier(ocr_import.supplier_name_ocr)
		if matched_supplier:
			ocr_import.supplier = matched_supplier
			ocr_import.supplier_match_status = match_status
			# Update supplier tax_id if we have it and the supplier doesn't
			supplier_tax_id = header_fields.get("supplier_tax_id", "")
			if supplier_tax_id:
				existing_tax_id = frappe.db.get_value("Supplier", matched_supplier, "tax_id")
				if not existing_tax_id:
					frappe.db.set_value("Supplier", matched_supplier, "tax_id", supplier_tax_id)
		else:
			# Fuzzy fallback for supplier
			fuzzy_supplier, fuzzy_status, _score = match_supplier_fuzzy(
				ocr_import.supplier_name_ocr, fuzzy_threshold
			)
			if fuzzy_supplier:
				ocr_import.supplier = fuzzy_supplier
				ocr_import.supplier_match_status = fuzzy_status  # "Suggested"
			else:
				ocr_import.supplier_match_status = "Unmatched"
	else:
		ocr_import.supplier_match_status = "Unmatched"

	# Item matching for each line
	for item in ocr_import.items:
		matched_item, match_status = None, "Unmatched"

		# Try product_code first (direct item_code match), then description
		if item.item_name and item.item_name != item.description_ocr:
			matched_item, match_status = match_item(item.item_name)
		if not matched_item and item.description_ocr:
			matched_item, match_status = match_item(item.description_ocr)

		# If no item match, try service matching (pattern → item + name + GL + CC)
		if not matched_item and item.description_ocr:
			service_match = match_service_item(
				item.description_ocr, company=ocr_import.company, supplier=ocr_import.supplier
			)
			if service_match:
				matched_item = service_match["item_code"]
				match_status = service_match["match_status"]
				item.expense_account = service_match.get("expense_account")
				item.cost_center = service_match.get("cost_center")
				if service_match.get("item_name"):
					item.item_name = service_match["item_name"]

		# Fuzzy fallback for item
		if not matched_item and item.description_ocr:
			fuzzy_item, fuzzy_status, _score = match_item_fuzzy(item.description_ocr, fuzzy_threshold)
			if fuzzy_item:
				matched_item = fuzzy_item
				match_status = fuzzy_status  # "Suggested"

		if matched_item:
			item.item_code = matched_item
			item.match_status = match_status

			# Even when item matched via alias/fuzzy, check service mapping for accounting fields
			if not item.expense_account and item.description_ocr:
				service_match = match_service_item(
					item.description_ocr, company=ocr_import.company, supplier=ocr_import.supplier
				)
				if service_match:
					item.expense_account = service_match.get("expense_account")
					item.cost_center = service_match.get("cost_center")
					if service_match.get("item_name"):
						item.item_name = service_match["item_name"]
		else:
			item.match_status = "Unmatched"


@frappe.whitelist()
def retry_gemini_extraction(ocr_import: str):
	"""
	Retry Gemini extraction for a failed OCR Import.

	Args:
		ocr_import: Name of the OCR Import record
	"""
	if not frappe.has_permission("OCR Import", "write", ocr_import):
		frappe.throw(_("You do not have permission to retry this extraction"))

	ocr_import_doc = frappe.get_doc("OCR Import", ocr_import)

	if ocr_import_doc.status != "Error":
		frappe.throw(_("Can only retry failed extractions"))

	if ocr_import_doc.source_type not in ("Gemini Manual Upload", "Gemini Email", "Gemini Drive Scan"):
		frappe.throw(_("Can only retry Gemini extractions"))

	# Download PDF from Drive if available
	if not ocr_import_doc.drive_file_id:
		frappe.throw(_("Original PDF not available. Please re-upload the PDF."))

	from erpocr_integration.tasks.drive_integration import download_file_from_drive

	pdf_content = download_file_from_drive(ocr_import_doc.drive_file_id)
	if not pdf_content:
		frappe.throw(_("Failed to download PDF from Google Drive. Please re-upload the PDF."))

	# Reset status and clear old data
	ocr_import_doc.db_set("status", "Pending")
	frappe.db.commit()

	# Re-enqueue extraction
	frappe.enqueue(
		"erpocr_integration.api.gemini_process",
		queue="long",
		timeout=300,
		pdf_content=pdf_content,
		filename=ocr_import_doc.source_filename,
		ocr_import_name=ocr_import_doc.name,
		source_type=ocr_import_doc.source_type,
		uploaded_by=frappe.session.user,
	)

	return {"message": _("Retry queued. The extraction will run in the background.")}
