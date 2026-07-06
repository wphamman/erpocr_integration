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
):
	"""Background job: extract statement data via Gemini, populate OCR Statement, reconcile."""
	frappe.set_user(uploaded_by or "Administrator")

	try:
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


@frappe.whitelist(methods=["POST"])
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


def _reconcile_statements_for_pi(pi_doc) -> list[str]:
	"""Find Reconciled statements that reference this PI's supplier and re-run
	reconciliation on them. Intended to be called from doc_events on Purchase
	Invoice submit/cancel so a late-arriving PI (or a cancelled one) shows up
	in any open statement for the same supplier without manual Re-Reconcile.

	Only statements in status "Reconciled" are touched. "Reviewed" statements
	are intentionally left alone — the user has signed them off.

	Returns the names of the statements that were re-reconciled.
	"""
	if not pi_doc.supplier:
		return []

	from erpocr_integration.tasks.reconcile import reconcile_statement

	candidates = frappe.get_all(
		"OCR Statement",
		filters={"supplier": pi_doc.supplier, "status": "Reconciled"},
		fields=["name"],
		ignore_permissions=True,
	)
	touched = []
	for row in candidates:
		stmt = frappe.get_doc("OCR Statement", row["name"])
		# Drop any prior reverse-check rows so reconcile_statement can re-seed them
		stmt.items = [i for i in stmt.items if getattr(i, "recon_status", "") != "Not in Statement"]
		for item in stmt.items:
			item.recon_status = ""
			item.matched_invoice = ""
			item.erp_amount = 0
			item.erp_outstanding = 0
			item.difference = 0
		stmt.reverse_check_skipped = 0
		reconcile_statement(stmt)
		stmt.save(ignore_permissions=True)
		touched.append(stmt.name)
	return touched


def _enqueue_statement_refresh(supplier: str) -> None:
	"""Enqueue the statement refresh on the short queue so we don't block the
	user's PI submit. Running it synchronously could re-reconcile N statements
	inside the PI transaction path — for a supplier with 50 Reconciled
	statements that's 50x get_doc + reconcile + save before the submit returns.
	"""
	if not supplier:
		return
	frappe.enqueue(
		"erpocr_integration.statement_api._reconcile_statements_for_supplier",
		queue="short",
		timeout=300,
		supplier=supplier,
	)


def _reconcile_statements_for_supplier(supplier: str) -> list[str]:
	"""Background-job entry point — set admin user, then reconcile."""
	frappe.set_user("Administrator")
	# Rebuild a minimal pi_doc-shaped namespace so we can reuse the core helper
	from types import SimpleNamespace

	return _reconcile_statements_for_pi(SimpleNamespace(supplier=supplier))


def update_statements_on_pi_submit(doc, method=None):
	"""doc_events hook: refresh statements for this supplier after PI submit.

	Enqueued — must not block the PI submit transaction.
	"""
	try:
		_enqueue_statement_refresh(doc.supplier)
	except Exception:
		# Never let statement reconciliation block a PI submit
		frappe.log_error(
			title="OCR Statement refresh enqueue failed on PI submit",
			message=frappe.get_traceback(),
		)


def update_statements_on_pi_cancel(doc, method=None):
	"""doc_events hook: refresh statements for this supplier after PI cancel."""
	try:
		_enqueue_statement_refresh(doc.supplier)
	except Exception:
		frappe.log_error(
			title="OCR Statement refresh enqueue failed on PI cancel",
			message=frappe.get_traceback(),
		)
