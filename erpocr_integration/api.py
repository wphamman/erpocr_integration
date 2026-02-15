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
		frappe.throw(_("File too large. Maximum size is 10MB. Your file is {0:.2f}MB").format(file_size / (1024 * 1024)))

	# Read file content
	pdf_content = file.read()

	# Create placeholder OCR Import record
	ocr_import = frappe.get_doc({
		"doctype": "OCR Import",
		"status": "Pending",
		"source_filename": filename,
		"source_type": "Gemini Manual Upload",
		"uploaded_by": frappe.session.user,
	})
	ocr_import.insert(ignore_permissions=True)
	frappe.db.commit()

	# Enqueue background processing
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

	return {
		"ocr_import": ocr_import.name,
		"status": "processing"
	}


def gemini_process(pdf_content: bytes, filename: str, ocr_import_name: str, source_type: str = "Gemini Manual Upload", uploaded_by: str = None):
	"""
	Background job to process PDF via Gemini API and create OCR Import.

	Args:
		pdf_content: Raw PDF file bytes
		filename: Original filename
		ocr_import_name: Name of the OCR Import record to update
		source_type: "Gemini Manual Upload" or "Gemini Email"
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
			message={"ocr_import": ocr_import_name, "status": "Extracting", "message": "Calling Gemini API..."},
			user=uploaded_by
		)

		# Call Gemini API
		from erpocr_integration.tasks.gemini_extract import extract_invoice_data

		extracted_data = extract_invoice_data(pdf_content, filename)

		# Publish realtime update
		frappe.publish_realtime(
			event="ocr_extraction_progress",
			message={"ocr_import": ocr_import_name, "status": "Processing", "message": "Matching suppliers and items..."},
			user=uploaded_by
		)

		# Process extracted data
		from erpocr_integration.tasks.process_import import process_extracted_data

		# Update the existing OCR Import record instead of creating a new one
		ocr_import = frappe.get_doc("OCR Import", ocr_import_name)

		# Populate fields from extracted data
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
		ocr_import.raw_payload = extracted_data.get("raw_response", "")

		# Add line items
		ocr_import.items = []
		for line in line_items:
			description = line.get("description", "")
			product_code = line.get("product_code", "")
			ocr_import.append("items", {
				"description_ocr": description,
				"item_name": product_code or description,
				"qty": line.get("quantity", 1.0),
				"rate": line.get("unit_price", 0.0),
				"amount": line.get("amount", 0.0),
				"match_status": "Unmatched",
			})

		# Run supplier matching
		from erpocr_integration.tasks.matching import match_item, match_supplier

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
				ocr_import.supplier_match_status = "Unmatched"
		else:
			ocr_import.supplier_match_status = "Unmatched"

		# Run item matching for each line
		for item in ocr_import.items:
			matched_item, match_status = None, "Unmatched"
			# Try product_code first (direct item_code match), then description
			if item.item_name and item.item_name != item.description_ocr:
				matched_item, match_status = match_item(item.item_name)
			if not matched_item and item.description_ocr:
				matched_item, match_status = match_item(item.description_ocr)
			if matched_item:
				item.item_code = matched_item
				item.match_status = match_status
			else:
				item.match_status = "Unmatched"

		# Save the record — before_save will set the correct status
		ocr_import.save(ignore_permissions=True)
		frappe.db.commit()

		# Publish realtime update
		frappe.publish_realtime(
			event="ocr_extraction_progress",
			message={"ocr_import": ocr_import_name, "status": ocr_import.status, "message": "Extraction complete!"},
			user=uploaded_by
		)

		# If fully matched, auto-create PI draft
		if ocr_import.status == "Matched":
			try:
				ocr_import.create_purchase_invoice()
				frappe.db.commit()
				frappe.publish_realtime(
					event="ocr_extraction_progress",
					message={"ocr_import": ocr_import_name, "status": "Completed", "message": "Purchase Invoice created!"},
					user=uploaded_by
				)
			except Exception:
				# PI creation failed — log but don't fail the import
				frappe.log_error(
					"OCR Integration Error",
					f"Auto PI creation failed for {ocr_import.name}\n{frappe.get_traceback()}",
				)

	except Exception as e:
		# Update status to Error
		try:
			error_log = frappe.log_error("OCR Integration Error", f"Gemini extraction failed for {filename}\n{frappe.get_traceback()}")
			frappe.db.set_value("OCR Import", ocr_import_name, {
				"status": "Error",
				"error_log": error_log.name
			})
			frappe.db.commit()

			# Publish realtime update
			frappe.publish_realtime(
				event="ocr_extraction_progress",
				message={"ocr_import": ocr_import_name, "status": "Error", "message": f"Extraction failed: {str(e)}"},
				user=uploaded_by
			)
		except Exception:
			# Even error handling failed
			frappe.log_error("OCR Integration Critical Error", frappe.get_traceback())


@frappe.whitelist()
def retry_gemini_extraction(ocr_import: str):
	"""
	Retry Gemini extraction for a failed OCR Import.

	Args:
		ocr_import: Name of the OCR Import record
	"""
	ocr_import_doc = frappe.get_doc("OCR Import", ocr_import)

	if ocr_import_doc.status != "Error":
		frappe.throw(_("Can only retry failed extractions"))

	if ocr_import_doc.source_type not in ("Gemini Manual Upload", "Gemini Email"):
		frappe.throw(_("Can only retry Gemini extractions"))

	# TODO: Implement retry logic
	# Would need to store the original PDF content somewhere (File doctype?)
	# For now, just throw an error
	frappe.throw(_("Retry not yet implemented. Please re-upload the PDF."))
