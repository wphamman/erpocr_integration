# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""
OCR Delivery Note API — background processing, PO matching, doc_event hooks.

Mirrors the structure of api.py but for the OCR Delivery Note workflow:
- Single DN per scan (not multi-invoice)
- No financial fields (no rate, amount, tax)
- PO matching is qty-focused (po_qty vs po_remaining_qty)
"""

import frappe
from frappe import _


def dn_gemini_process(
	file_content: bytes,
	filename: str,
	ocr_dn_name: str,
	mime_type: str = "application/pdf",
	queue_position: int = 0,
):
	"""
	Background job: extract delivery note data via Gemini and populate OCR DN.

	Single DN per scan — updates the existing placeholder record.

	Args:
		file_content: Raw file bytes (PDF or image)
		filename: Original filename
		ocr_dn_name: Name of the OCR Delivery Note record to update
		mime_type: MIME type for Gemini API
		queue_position: Position in queue for rate-limit staggering
	"""
	frappe.set_user("Administrator")

	try:
		# Stagger Gemini API calls
		if queue_position > 0:
			import time

			wait_seconds = min(queue_position * 5, 240)
			time.sleep(wait_seconds)

		frappe.db.set_value("OCR Delivery Note", ocr_dn_name, "status", "Pending")
		frappe.db.commit()  # nosemgrep

		# Call Gemini API for DN extraction
		from erpocr_integration.tasks.gemini_extract import extract_delivery_note_data

		extracted_data = extract_delivery_note_data(file_content, filename, mime_type=mime_type)

		settings = frappe.get_cached_doc("OCR Settings")
		ocr_dn = frappe.get_doc("OCR Delivery Note", ocr_dn_name)

		_populate_ocr_dn(ocr_dn, extracted_data, settings)
		_run_dn_matching(ocr_dn, settings)

		ocr_dn.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep

		# Move scan file to DN archive folder after successful extraction
		drive_file_id = ocr_dn.drive_file_id
		if drive_file_id:
			try:
				from erpocr_integration.tasks.drive_integration import move_file_to_archive

				dn_archive_folder = settings.get("dn_archive_folder_id")
				drive_result = move_file_to_archive(
					file_id=drive_file_id,
					supplier_name=ocr_dn.supplier_name_ocr or "",
					invoice_date=ocr_dn.delivery_date,
					archive_folder_id=dn_archive_folder,
				)
				if drive_result.get("folder_path"):
					frappe.db.set_value(
						"OCR Delivery Note",
						ocr_dn_name,
						{
							"drive_link": drive_result.get("shareable_link"),
							"drive_folder_path": drive_result.get("folder_path"),
						},
					)
					frappe.db.commit()  # nosemgrep
			except Exception as e:
				frappe.log_error(
					title="DN Drive Move Failed",
					message=f"Failed to move {filename} to DN archive: {e!s}",
				)

	except Exception:
		try:
			error_log = frappe.log_error(
				title="OCR DN Extraction Error",
				message=f"Gemini extraction failed for DN {filename}\n{frappe.get_traceback()}",
			)
			frappe.db.set_value(
				"OCR Delivery Note",
				ocr_dn_name,
				{"status": "Error", "error_log": error_log.name},
			)
			frappe.db.commit()  # nosemgrep
		except Exception:
			frappe.log_error(title="OCR DN Critical Error", message=frappe.get_traceback())


def _populate_ocr_dn(ocr_dn, extracted_data: dict, settings):
	"""Populate an OCR Delivery Note record with extracted data."""
	header = extracted_data.get("header_fields", {})
	line_items = extracted_data.get("line_items", [])

	ocr_dn.supplier_name_ocr = header.get("supplier_name", "")
	ocr_dn.delivery_note_number = header.get("delivery_note_number", "")
	ocr_dn.delivery_date = header.get("delivery_date") or None
	ocr_dn.vehicle_number = header.get("vehicle_number", "")
	ocr_dn.driver_name = header.get("driver_name", "")

	try:
		raw_confidence = float(header.get("confidence") or 0.0)
	except (ValueError, TypeError):
		raw_confidence = 0.0
	ocr_dn.confidence = max(0.0, min(100.0, raw_confidence * 100))

	ocr_dn.raw_payload = extracted_data.get("raw_response", "")

	# Add line items
	ocr_dn.items = []
	for line in line_items:
		description = line.get("description", "")
		product_code = line.get("product_code", "")
		ocr_dn.append(
			"items",
			{
				"description_ocr": description,
				"item_name": (product_code or description)[:140],
				"qty": line.get("quantity", 1.0),
				"uom": line.get("unit", ""),
				"match_status": "Unmatched",
			},
		)


def _run_dn_matching(ocr_dn, settings):
	"""Run supplier and item matching on an OCR Delivery Note record."""
	from erpocr_integration.tasks.matching import (
		match_item,
		match_item_fuzzy,
		match_supplier,
		match_supplier_fuzzy,
	)

	fuzzy_threshold = settings.matching_threshold or 80

	# Supplier matching
	if ocr_dn.supplier_name_ocr:
		matched_supplier, match_status = match_supplier(ocr_dn.supplier_name_ocr)
		if matched_supplier:
			ocr_dn.supplier = matched_supplier
			ocr_dn.supplier_match_status = match_status
		else:
			fuzzy_supplier, fuzzy_status, _score = match_supplier_fuzzy(
				ocr_dn.supplier_name_ocr, fuzzy_threshold
			)
			if fuzzy_supplier:
				ocr_dn.supplier = fuzzy_supplier
				ocr_dn.supplier_match_status = fuzzy_status
			else:
				ocr_dn.supplier_match_status = "Unmatched"
	else:
		ocr_dn.supplier_match_status = "Unmatched"

	# Item matching for each line
	for item in ocr_dn.items:
		matched_item, match_status = None, "Unmatched"

		# Try product_code first, then description
		if item.item_name and item.item_name != item.description_ocr:
			matched_item, match_status = match_item(item.item_name)
		if not matched_item and item.description_ocr:
			matched_item, match_status = match_item(item.description_ocr)

		# Fuzzy fallback
		if not matched_item and item.description_ocr:
			fuzzy_item, fuzzy_status, _score = match_item_fuzzy(item.description_ocr, fuzzy_threshold)
			if fuzzy_item:
				matched_item = fuzzy_item
				match_status = fuzzy_status

		if matched_item:
			item.item_code = matched_item
			item.match_status = match_status
		else:
			item.match_status = "Unmatched"


# ── Doc Events ───────────────────────────────────────────────────────


def update_ocr_dn_on_submit(doc, method):
	"""Hook: when a PO/PR is submitted, mark the linked OCR Delivery Note as Completed."""
	field_map = {
		"Purchase Order": "purchase_order_result",
		"Purchase Receipt": "purchase_receipt",
	}
	field = field_map.get(doc.doctype)
	if not field:
		return

	ocr_dns = frappe.get_all(
		"OCR Delivery Note",
		filters={field: doc.name, "status": "Draft Created"},
		pluck="name",
	)
	for name in ocr_dns:
		frappe.db.set_value("OCR Delivery Note", name, "status", "Completed")


def update_ocr_dn_on_cancel(doc, method):
	"""Hook: when a PO/PR is cancelled, clear the link and recompute OCR DN status."""
	field_map = {
		"Purchase Order": "purchase_order_result",
		"Purchase Receipt": "purchase_receipt",
	}
	field = field_map.get(doc.doctype)
	if not field:
		return

	ocr_dns = frappe.get_all(
		"OCR Delivery Note",
		filters={field: doc.name, "status": "Completed"},
		pluck="name",
	)
	for name in ocr_dns:
		ocr_doc = frappe.get_doc("OCR Delivery Note", name)
		ocr_doc.set(field, "")
		ocr_doc.document_type = ""
		ocr_doc.status = "Matched"  # _update_status() will recompute on save
		ocr_doc.save(ignore_permissions=True)


# ── Whitelisted Endpoints ────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def retry_dn_extraction(ocr_dn: str):
	"""Retry Gemini extraction for a failed OCR Delivery Note."""
	if not frappe.has_permission("OCR Delivery Note", "write", ocr_dn):
		frappe.throw(_("You don't have permission to retry this extraction."))

	ocr_dn_doc = frappe.get_doc("OCR Delivery Note", ocr_dn)

	if ocr_dn_doc.status != "Error":
		frappe.throw(_("Can only retry failed extractions."))

	# Get file content: try Drive first, then local attachment
	file_content = None

	if ocr_dn_doc.drive_file_id:
		from erpocr_integration.tasks.drive_integration import download_file_from_drive

		file_content = download_file_from_drive(ocr_dn_doc.drive_file_id)

	if not file_content:
		attached = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "OCR Delivery Note",
				"attached_to_name": ocr_dn_doc.name,
			},
			fields=["name"],
			limit=1,
			order_by="creation desc",
		)
		if attached:
			file_doc = frappe.get_doc("File", attached[0].name)
			file_content = file_doc.get_content()

	if not file_content:
		frappe.throw(_("Original file not available. Cannot retry."))

	# Determine MIME type from attached file
	from erpocr_integration.api import SUPPORTED_FILE_TYPES

	attached_files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "OCR Delivery Note",
			"attached_to_name": ocr_dn_doc.name,
		},
		fields=["file_name"],
		limit=1,
		order_by="creation desc",
	)
	source_filename = attached_files[0].file_name if attached_files else ""
	file_ext = "." + source_filename.rsplit(".", 1)[-1].lower() if "." in source_filename else ""
	file_mime_type = SUPPORTED_FILE_TYPES.get(file_ext, "application/pdf")

	# Reset status
	ocr_dn_doc.db_set("status", "Pending")
	frappe.db.commit()  # nosemgrep

	try:
		frappe.enqueue(
			"erpocr_integration.dn_api.dn_gemini_process",
			queue="long",
			timeout=300,
			file_content=file_content,
			filename=source_filename,
			ocr_dn_name=ocr_dn_doc.name,
			mime_type=file_mime_type,
			queue_position=0,
		)
	except Exception:
		ocr_dn_doc.db_set("status", "Error")
		frappe.db.commit()  # nosemgrep
		frappe.log_error(
			title="OCR DN Retry Enqueue Error",
			message=f"Failed to enqueue retry for {ocr_dn_doc.name}\n{frappe.get_traceback()}",
		)
		frappe.throw(_("Failed to start retry. Please try again."))

	return {"message": _("Retry queued. The extraction will run in the background.")}


@frappe.whitelist()
def get_open_purchase_orders_for_dn(supplier, company):
	"""Return open Purchase Orders for a given supplier and company (for DN matching)."""
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("You don't have permission to view Purchase Orders."))

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
def match_dn_po_items(ocr_dn, purchase_order):
	"""Match OCR DN items to PO items by item_code, with qty comparison.

	Returns proposed matches for user review, including remaining qty on PO.
	"""
	if not frappe.has_permission("OCR Delivery Note", "write", ocr_dn):
		frappe.throw(_("You don't have permission to modify this record."))
	if not frappe.has_permission("Purchase Order", "read", purchase_order):
		frappe.throw(_("You don't have permission to view this Purchase Order."))

	ocr_doc = frappe.get_doc("OCR Delivery Note", ocr_dn)
	po_doc = frappe.get_doc("Purchase Order", purchase_order)

	# Validate supplier and company match
	if po_doc.supplier != ocr_doc.supplier:
		frappe.throw(
			_("PO supplier '{0}' does not match DN supplier '{1}'.").format(po_doc.supplier, ocr_doc.supplier)
		)
	if po_doc.company != ocr_doc.company:
		frappe.throw(
			_("PO company '{0}' does not match DN company '{1}'.").format(po_doc.company, ocr_doc.company)
		)

	# Build pool of PO items with remaining qty
	po_items_pool = []
	for po_item in po_doc.items:
		received_qty = po_item.received_qty or 0
		remaining_qty = (po_item.qty or 0) - received_qty
		po_items_pool.append(
			{
				"name": po_item.name,
				"item_code": po_item.item_code,
				"item_name": po_item.item_name,
				"qty": po_item.qty,
				"received_qty": received_qty,
				"remaining_qty": remaining_qty,
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
						"po_remaining_qty": po_item["remaining_qty"],
					}
					break

		matches.append(
			{
				"idx": item.idx,
				"description_ocr": item.description_ocr,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"match": match,
			}
		)

	# Collect unmatched PO items
	unmatched_po = [p for p in po_items_pool if not p["matched"]]

	return {
		"matches": matches,
		"unmatched_po": unmatched_po,
	}
