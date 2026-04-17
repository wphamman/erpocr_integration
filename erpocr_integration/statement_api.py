"""Statement processing pipeline — background job for statement extraction + reconciliation."""

import frappe
from frappe import _


def statement_gemini_process(
	file_content: bytes,
	filename: str,
	ocr_statement_name: str,
	source_type: str = "Gemini Drive Scan",
	uploaded_by: str | None = None,
	mime_type: str = "application/pdf",
	queue_position: int = 0,
):
	"""Background job: extract statement data via Gemini, populate OCR Statement, reconcile."""
	frappe.set_user(uploaded_by or "Administrator")

	try:
		if queue_position > 0:
			import time

			wait_seconds = min(queue_position * 5, 240)
			time.sleep(wait_seconds)

		frappe.db.set_value("OCR Statement", ocr_statement_name, "status", "Extracting")
		frappe.db.commit()  # nosemgrep

		from erpocr_integration.tasks.gemini_extract import extract_statement_data

		extracted = extract_statement_data(file_content, filename, mime_type=mime_type)

		ocr_statement = frappe.get_doc("OCR Statement", ocr_statement_name)
		_populate_ocr_statement(ocr_statement, extracted)
		_run_statement_matching(ocr_statement)
		ocr_statement.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep

		# Reconcile against ERPNext PIs (requires supplier to be matched)
		if ocr_statement.supplier:
			from erpocr_integration.tasks.reconcile import reconcile_statement

			reconcile_statement(ocr_statement)
			ocr_statement.status = "Reconciled"
		else:
			ocr_statement.status = "Pending"

		ocr_statement.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep

		# Move Drive file to archive
		if ocr_statement.drive_file_id:
			try:
				from erpocr_integration.tasks.drive_integration import move_file_to_archive

				drive_result = move_file_to_archive(
					file_id=ocr_statement.drive_file_id,
					supplier_name=extracted["header_fields"].get("supplier_name", ""),
					invoice_date=extracted["header_fields"].get("statement_date"),
				)
				if drive_result.get("folder_path"):
					ocr_statement.drive_link = drive_result.get("shareable_link")
					ocr_statement.drive_folder_path = drive_result.get("folder_path")
					ocr_statement.save(ignore_permissions=True)
					frappe.db.commit()  # nosemgrep
			except Exception as e:
				frappe.log_error(
					title="Drive Move Failed",
					message=f"Failed to move {filename} to archive: {e!s}",
				)

	except Exception:
		try:
			error_log = frappe.log_error(
				title="Statement Processing Error",
				message=f"Statement extraction failed for {filename}\n{frappe.get_traceback()}",
			)
			frappe.db.set_value(
				"OCR Statement",
				ocr_statement_name,
				{"status": "Error", "error_log": error_log.name},
			)
			frappe.db.commit()  # nosemgrep
		except Exception:
			frappe.log_error(title="Statement Critical Error", message=frappe.get_traceback())


def _populate_ocr_statement(ocr_statement, extracted: dict) -> None:
	"""Populate OCR Statement with extracted data."""
	header = extracted.get("header_fields", {})

	ocr_statement.supplier_name_ocr = header.get("supplier_name", "")
	ocr_statement.statement_date = header.get("statement_date")
	ocr_statement.period_from = header.get("period_from")
	ocr_statement.period_to = header.get("period_to")
	ocr_statement.opening_balance = header.get("opening_balance") or 0.0
	ocr_statement.closing_balance = header.get("closing_balance") or 0.0
	ocr_statement.currency = header.get("currency", "")
	ocr_statement.raw_payload = extracted.get("raw_response", "")

	ocr_statement.items = []
	for txn in extracted.get("transactions", []):
		ocr_statement.append(
			"items",
			{
				"reference": txn.get("reference", ""),
				"transaction_date": txn.get("date"),
				"description": txn.get("description", ""),
				"debit": txn.get("debit") or 0.0,
				"credit": txn.get("credit") or 0.0,
				"balance": txn.get("balance") or 0.0,
			},
		)


def _run_statement_matching(ocr_statement) -> None:
	"""Match the supplier name from the statement against ERPNext."""
	from erpocr_integration.tasks.matching import match_supplier, match_supplier_fuzzy

	if not ocr_statement.supplier_name_ocr:
		ocr_statement.supplier_match_status = "Unmatched"
		return

	matched, status = match_supplier(ocr_statement.supplier_name_ocr)
	if matched:
		ocr_statement.supplier = matched
		ocr_statement.supplier_match_status = status
		return

	settings = frappe.get_single("OCR Settings")
	threshold = settings.matching_threshold or 80
	fuzzy, fuzzy_status, _ = match_supplier_fuzzy(ocr_statement.supplier_name_ocr, threshold)
	if fuzzy:
		ocr_statement.supplier = fuzzy
		ocr_statement.supplier_match_status = fuzzy_status
	else:
		ocr_statement.supplier_match_status = "Unmatched"


@frappe.whitelist()
def rereconcile_statement(statement_name: str) -> None:
	"""Re-run reconciliation after manual supplier change."""
	if not frappe.has_permission("OCR Statement", "write", statement_name):
		frappe.throw(_("You don't have permission to re-reconcile this statement."))

	doc = frappe.get_doc("OCR Statement", statement_name)
	if not doc.supplier:
		frappe.throw(_("Please select a supplier first."))

	from erpocr_integration.tasks.reconcile import reconcile_statement

	# Remove reverse-check items (will be re-added by reconcile)
	doc.items = [i for i in doc.items if getattr(i, "recon_status", "") != "Not in Statement"]

	# Clear existing recon data
	for item in doc.items:
		item.recon_status = ""
		item.matched_invoice = ""
		item.erp_amount = 0
		item.erp_outstanding = 0
		item.difference = 0

	doc.reverse_check_skipped = 0
	reconcile_statement(doc)
	doc.status = "Reconciled"
	doc.save()
