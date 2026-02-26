# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _

MAX_PENDING_IMPORTS_PER_USER = 5

# Supported file types for upload (extension → MIME type for Gemini API)
SUPPORTED_FILE_TYPES = {
	".pdf": "application/pdf",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
}

# Magic byte signatures for file type validation
_MAGIC_BYTES = {
	"application/pdf": (b"%PDF-", 5),
	"image/jpeg": (b"\xff\xd8", 2),
	"image/png": (b"\x89PNG", 4),
}


def validate_file_magic_bytes(content: bytes, mime_type: str) -> bool:
	"""Check file content starts with the expected magic bytes for the given MIME type.

	Returns True if valid (or if MIME type has no known signature), False if mismatch.
	"""
	sig = _MAGIC_BYTES.get(mime_type)
	if not sig:
		return True
	magic, length = sig
	return content[:length] == magic


def _enforce_pending_import_limit(user: str | None):
	"""Reject if user already has too many in-flight OCR imports in the queue."""
	if not user or user == "Guest":
		return
	pending_count = frappe.db.count(
		"OCR Import",
		filters={"uploaded_by": user, "status": ["in", ["Pending", "Needs Review"]]},
	)
	if int(pending_count or 0) >= MAX_PENDING_IMPORTS_PER_USER:
		frappe.throw(
			_(
				"You already have {0} pending OCR imports. "
				"Please wait for some to finish before uploading more."
			).format(MAX_PENDING_IMPORTS_PER_USER)
		)


@frappe.whitelist(methods=["POST"])
def upload_pdf():
	"""
	Upload PDF or image for Gemini OCR extraction.

	URL: /api/method/erpocr_integration.api.upload_pdf

	Expects multipart/form-data with 'file' field.
	Accepts PDF (.pdf), JPEG (.jpg/.jpeg), and PNG (.png) files.

	Returns:
		dict: {"ocr_import": name, "status": "processing"}
	"""
	# Validate user has permission (Accounts User or System Manager)
	if not frappe.has_permission("OCR Import", "create"):
		frappe.throw(_("You do not have permission to upload invoices"))

	_enforce_pending_import_limit(frappe.session.user)

	# Get uploaded file
	if not frappe.request or not frappe.request.files:
		frappe.throw(_("No file uploaded"))

	file = frappe.request.files.get("file")
	if not file:
		frappe.throw(_("No file found in request"))

	# Validate file type
	filename = file.filename
	file_ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
	mime_type = SUPPORTED_FILE_TYPES.get(file_ext)
	if not mime_type:
		supported = ", ".join(SUPPORTED_FILE_TYPES.keys())
		frappe.throw(_("Unsupported file type. Accepted formats: {0}").format(supported))

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
	file_content = file.read()

	# Validate file magic bytes
	if not validate_file_magic_bytes(file_content, mime_type):
		frappe.throw(_("File content does not match its file type. The file may be corrupted."))

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

	# Save file as private attachment for retry capability
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": "OCR Import",
			"attached_to_name": ocr_import.name,
			"content": file_content,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()

	# Enqueue background processing
	try:
		frappe.enqueue(
			"erpocr_integration.api.gemini_process",
			queue="long",
			timeout=300,  # 5 minutes
			pdf_content=file_content,
			filename=filename,
			ocr_import_name=ocr_import.name,
			source_type="Gemini Manual Upload",
			uploaded_by=frappe.session.user,
			mime_type=mime_type,
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
	mime_type: str = "application/pdf",
):
	"""
	Background job to process PDF/image via Gemini API and create OCR Import(s).

	Supports multi-invoice PDFs — creates one OCR Import per invoice found.
	The first invoice updates the existing placeholder record (ocr_import_name).
	Additional invoices create new OCR Import records.

	Args:
		pdf_content: Raw file bytes (PDF or image)
		filename: Original filename
		ocr_import_name: Name of the OCR Import record to update
		source_type: "Gemini Manual Upload", "Gemini Email", or "Gemini Drive Scan"
		uploaded_by: User who initiated the upload
		mime_type: MIME type for Gemini API (e.g., "application/pdf", "image/jpeg")
	"""
	# Run as the uploading user (not Administrator) for audit trail.
	# Individual calls use ignore_permissions where needed.
	frappe.set_user(uploaded_by or "Administrator")

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

		invoice_list = extract_invoice_data(pdf_content, filename, mime_type=mime_type)

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

		# Process each invoice and collect all created OCR Import names
		placeholder_doc = frappe.get_doc("OCR Import", ocr_import_name)
		all_ocr_import_names = []
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
						"drive_file_id": placeholder_doc.drive_file_id,
						"drive_retry_count": placeholder_doc.drive_retry_count,
					}
				)

			_populate_ocr_import(ocr_import, extracted_data, settings, drive_result)
			_run_matching(ocr_import, extracted_data.get("header_fields", {}), settings)

			if idx == 0:
				ocr_import.save(ignore_permissions=True)
			else:
				ocr_import.insert(ignore_permissions=True)

			all_ocr_import_names.append(ocr_import.name)

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
						"OCR Import",
						filters={"drive_file_id": existing_drive_file_id},
						pluck="name",
						ignore_permissions=True,
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

		# Publish realtime update — no auto-creation, user reviews and creates
		ocr_import_first = frappe.get_doc("OCR Import", ocr_import_name)
		msg = "Extraction complete! Please review and confirm matches."
		if invoice_count > 1:
			msg = f"Extraction complete! {invoice_count} invoices created. Please review."
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
	ocr_import.supplier_tax_id = header_fields.get("supplier_tax_id", "")
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

	# document_type left blank — user selects before creating document


@frappe.whitelist(methods=["POST"])
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

	_enforce_pending_import_limit(frappe.session.user)

	# Get file content: try Drive first, then local attachment
	pdf_content = None

	if ocr_import_doc.drive_file_id:
		from erpocr_integration.tasks.drive_integration import download_file_from_drive

		pdf_content = download_file_from_drive(ocr_import_doc.drive_file_id)

	if not pdf_content:
		# Try reading from attached file
		attached = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "OCR Import",
				"attached_to_name": ocr_import_doc.name,
			},
			fields=["name", "file_url"],
			limit=1,
			order_by="creation desc",
		)
		if attached:
			file_doc = frappe.get_doc("File", attached[0].name)
			pdf_content = file_doc.get_content()

	if not pdf_content:
		frappe.throw(_("Original file not available. Please re-upload the file."))

	# Determine MIME type from original filename
	source_filename = ocr_import_doc.source_filename or ""
	file_ext = ("." + source_filename.rsplit(".", 1)[-1].lower()) if "." in source_filename else ""
	file_mime_type = SUPPORTED_FILE_TYPES.get(file_ext, "application/pdf")

	# Reset status and clear old data
	ocr_import_doc.db_set("status", "Pending")
	frappe.db.commit()

	# Re-enqueue extraction
	try:
		frappe.enqueue(
			"erpocr_integration.api.gemini_process",
			queue="long",
			timeout=300,
			pdf_content=pdf_content,
			filename=ocr_import_doc.source_filename,
			ocr_import_name=ocr_import_doc.name,
			source_type=ocr_import_doc.source_type,
			uploaded_by=frappe.session.user,
			mime_type=file_mime_type,
		)
	except Exception:
		# Enqueue failed — revert to Error so it doesn't sit as stale Pending
		ocr_import_doc.db_set("status", "Error")
		frappe.db.commit()
		frappe.log_error(
			title="OCR Retry Enqueue Error",
			message=f"Failed to enqueue retry for {ocr_import_doc.name}\n{frappe.get_traceback()}",
		)
		frappe.throw(_("Failed to start retry. Please try again."))

	return {"message": _("Retry queued. The extraction will run in the background.")}


@frappe.whitelist()
def get_open_purchase_orders(supplier, company):
	"""Return open Purchase Orders for a given supplier and company."""
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("You don't have permission to view Purchase Orders."))

	# Use get_list (not get_all) to respect user-permission restrictions
	return frappe.get_list(
		"Purchase Order",
		filters={
			"supplier": supplier,
			"company": company,
			"docstatus": 1,
			"status": ["in", ["To Receive and Bill", "To Receive", "To Bill"]],
		},
		fields=["name", "transaction_date", "grand_total", "status"],
		order_by="transaction_date desc",
		limit_page_length=20,
	)


@frappe.whitelist()
def get_purchase_receipts_for_po(purchase_order):
	"""Return Purchase Receipts that have items linked to the given PO.

	The PO reference lives on PR child rows (Purchase Receipt Item.purchase_order),
	not the PR header, so a plain Link filter won't work.
	"""
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("You don't have permission to view Purchase Orders."))
	if not frappe.has_permission("Purchase Order", "read", purchase_order):
		frappe.throw(_("You don't have permission to access this Purchase Order."))
	if not frappe.has_permission("Purchase Receipt", "read"):
		frappe.throw(_("You don't have permission to view Purchase Receipts."))

	pr_names = frappe.db.sql(
		"""
		SELECT DISTINCT pri.parent
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pri.purchase_order = %s
		  AND pr.docstatus = 1
		""",
		purchase_order,
		as_list=True,
	)

	if not pr_names:
		return []

	# Use get_list (not get_all) to respect user-permission restrictions
	pr_name_list = [r[0] for r in pr_names]
	return frappe.get_list(
		"Purchase Receipt",
		filters={"name": ["in", pr_name_list]},
		fields=["name", "posting_date", "status"],
		order_by="posting_date desc",
	)


@frappe.whitelist()
def purchase_receipt_link_query(doctype, txt, searchfield, start, page_len, filters):
	"""Frappe Link query for purchase_receipt_link field.

	Returns PRs linked to the selected PO. Required signature for set_query().
	Enforces read permission on both Purchase Receipt and Purchase Order,
	and scopes results to the PO's company. Per-document permission filtering
	ensures user-permission restrictions are respected.
	"""
	if not frappe.has_permission("Purchase Receipt", "read"):
		return []
	if not frappe.has_permission("Purchase Order", "read"):
		return []

	purchase_order = filters.get("purchase_order") if filters else None
	if not purchase_order:
		return []

	# Verify user can access this specific PO
	if not frappe.has_permission("Purchase Order", "read", purchase_order):
		return []

	# Scope to the PO's company to prevent cross-company enumeration
	po_company = frappe.db.get_value("Purchase Order", purchase_order, "company")
	if not po_company:
		return []

	txt = txt or ""

	# Fetch candidate PR names via SQL (needed for child-table JOIN)
	# then filter through per-document permission check
	candidates = frappe.db.sql(
		"""
		SELECT DISTINCT pr.name, pr.posting_date, pr.status
		FROM `tabPurchase Receipt` pr
		INNER JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
		WHERE pri.purchase_order = %(purchase_order)s
		  AND pr.docstatus = 1
		  AND pr.company = %(company)s
		  AND pr.name LIKE %(txt)s
		ORDER BY pr.posting_date DESC
		""",
		{
			"purchase_order": purchase_order,
			"txt": f"%{txt}%",
			"company": po_company,
		},
		as_dict=True,
	)

	# Per-document permission filter (respects user-permission restrictions)
	start = int(start or 0)
	page_len = int(page_len or 20)
	results = []
	for row in candidates:
		if frappe.has_permission("Purchase Receipt", "read", row.name):
			results.append([row.name, f"{row.posting_date} — {row.status}"])
			if len(results) >= start + page_len:
				break

	return results[start:]


@frappe.whitelist()
def match_po_items(ocr_import, purchase_order):
	"""Match OCR Import items to Purchase Order items by item_code.

	Validates supplier/company match, then returns proposed matches for user review.
	"""
	if not frappe.has_permission("OCR Import", "write", ocr_import):
		frappe.throw(_("You don't have permission to modify this OCR Import."))
	if not frappe.has_permission("Purchase Order", "read", purchase_order):
		frappe.throw(_("You don't have permission to view this Purchase Order."))

	ocr_doc = frappe.get_doc("OCR Import", ocr_import)
	po_doc = frappe.get_doc("Purchase Order", purchase_order)

	# Validate supplier and company match
	if po_doc.supplier != ocr_doc.supplier:
		frappe.throw(
			_("PO supplier '{0}' does not match OCR Import supplier '{1}'.").format(
				po_doc.supplier, ocr_doc.supplier
			)
		)
	if po_doc.company != ocr_doc.company:
		frappe.throw(
			_("PO company '{0}' does not match OCR Import company '{1}'.").format(
				po_doc.company, ocr_doc.company
			)
		)

	# Build a pool of PO items (FIFO for duplicate item_codes)
	po_items_pool = []
	for po_item in po_doc.items:
		po_items_pool.append(
			{
				"name": po_item.name,
				"item_code": po_item.item_code,
				"item_name": po_item.item_name,
				"qty": po_item.qty,
				"rate": po_item.rate,
				"matched": False,
			}
		)

	# Match OCR items to PO items by item_code (FIFO)
	matches = []
	for item in ocr_doc.items:
		match = None
		if item.item_code:
			for po_item in po_items_pool:
				if not po_item["matched"] and po_item["item_code"] == item.item_code:
					po_item["matched"] = True
					match = {
						"purchase_order_item": po_item["name"],
						"po_item_code": po_item["item_code"],
						"po_item_name": po_item["item_name"],
						"po_qty": po_item["qty"],
						"po_rate": po_item["rate"],
					}
					break

		matches.append(
			{
				"idx": item.idx,
				"description_ocr": item.description_ocr,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"rate": item.rate,
				"match": match,
			}
		)

	# Collect unmatched PO items
	unmatched_po = [p for p in po_items_pool if not p["matched"]]

	# Get available PRs for this PO
	purchase_receipts = get_purchase_receipts_for_po(purchase_order)

	return {
		"matches": matches,
		"unmatched_po": unmatched_po,
		"purchase_receipts": purchase_receipts,
	}


@frappe.whitelist()
def match_pr_items(ocr_import, purchase_receipt):
	"""Match OCR Import items to Purchase Receipt items by item_code.

	Validates that the PR belongs to the selected PO (server-side check).
	"""
	if not frappe.has_permission("OCR Import", "write", ocr_import):
		frappe.throw(_("You don't have permission to modify this OCR Import."))
	if not frappe.has_permission("Purchase Receipt", "read", purchase_receipt):
		frappe.throw(_("You don't have permission to view this Purchase Receipt."))

	ocr_doc = frappe.get_doc("OCR Import", ocr_import)
	pr_doc = frappe.get_doc("Purchase Receipt", purchase_receipt)

	# Validate the PR belongs to the selected PO
	if not ocr_doc.purchase_order:
		frappe.throw(_("Please select a Purchase Order first."))

	pr_has_po_link = False
	for pr_item in pr_doc.items:
		if pr_item.purchase_order == ocr_doc.purchase_order:
			pr_has_po_link = True
			break

	if not pr_has_po_link:
		frappe.throw(
			_("Purchase Receipt '{0}' is not linked to Purchase Order '{1}'.").format(
				purchase_receipt, ocr_doc.purchase_order
			)
		)

	# Build PR items pool (FIFO for duplicate item_codes)
	pr_items_pool = []
	for pr_item in pr_doc.items:
		if pr_item.purchase_order == ocr_doc.purchase_order:
			pr_items_pool.append(
				{
					"name": pr_item.name,
					"item_code": pr_item.item_code,
					"item_name": pr_item.item_name,
					"qty": pr_item.qty,
					"rate": pr_item.rate,
					"matched": False,
				}
			)

	# Match OCR items to PR items by item_code (FIFO)
	matches = []
	for item in ocr_doc.items:
		match = None
		if item.item_code:
			for pr_item in pr_items_pool:
				if not pr_item["matched"] and pr_item["item_code"] == item.item_code:
					pr_item["matched"] = True
					match = {
						"pr_detail": pr_item["name"],
						"pr_item_code": pr_item["item_code"],
						"pr_qty": pr_item["qty"],
						"pr_rate": pr_item["rate"],
					}
					break

		matches.append(
			{
				"idx": item.idx,
				"item_code": item.item_code,
				"match": match,
			}
		)

	return {"matches": matches}
