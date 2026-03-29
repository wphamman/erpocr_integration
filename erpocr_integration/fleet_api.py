# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""
Fleet Slip processing pipeline.

Handles:
- Gemini extraction background job (fleet_gemini_process)
- Populating OCR Fleet Slip from extracted data (_populate_ocr_fleet)
- Vehicle matching (_match_vehicle, _apply_vehicle_config)
- Tax template selection
- doc_events hooks for PI submit/cancel
- Retry endpoint
"""

import json
import time

import frappe
from frappe import _


def fleet_gemini_process(
	file_content: bytes,
	filename: str,
	ocr_fleet_name: str,
	mime_type: str = "application/pdf",
	queue_position: int = 0,
):
	"""
	Background job: extract fleet slip data via Gemini and populate the OCR Fleet Slip.

	Enqueued by poll_drive_fleet_folder. Runs on the 'long' queue.
	"""
	frappe.set_user("Administrator")

	try:
		# Rate-limit stagger (capped at 240s)
		if queue_position > 0:
			stagger = min(queue_position * 5, 240)
			time.sleep(stagger)

		frappe.db.set_value("OCR Fleet Slip", ocr_fleet_name, "status", "Pending")
		frappe.db.commit()  # nosemgrep

		from erpocr_integration.tasks.gemini_extract import extract_fleet_slip_data

		extracted_data = extract_fleet_slip_data(file_content, filename, mime_type=mime_type)

		settings = frappe.get_cached_doc("OCR Settings")
		ocr_fleet = frappe.get_doc("OCR Fleet Slip", ocr_fleet_name)

		_populate_ocr_fleet(ocr_fleet, extracted_data, settings)
		_run_fleet_matching(ocr_fleet, settings)

		ocr_fleet.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep

		# Move Drive file to archive after successful extraction
		if ocr_fleet.drive_file_id:
			try:
				from erpocr_integration.tasks.drive_integration import move_file_to_archive

				# Use "Fleet Slips" as the supplier_name for archive folder structure
				archive_result = move_file_to_archive(
					file_id=ocr_fleet.drive_file_id,
					supplier_name="Fleet Slips",
					invoice_date=ocr_fleet.transaction_date,
				)
				if archive_result.get("shareable_link"):
					ocr_fleet.db_set("drive_link", archive_result["shareable_link"])
					ocr_fleet.db_set("drive_file_id", archive_result["file_id"])
				if archive_result.get("folder_path"):
					ocr_fleet.db_set("drive_folder_path", archive_result["folder_path"])
				frappe.db.commit()  # nosemgrep
			except Exception:
				frappe.log_error(
					title="Fleet Drive Archive Error",
					message=f"Failed to archive fleet scan {filename}: {frappe.get_traceback()}",
				)

		frappe.logger().info(f"Fleet: Processed {filename} → {ocr_fleet_name}")

	except Exception as e:
		frappe.db.set_value(
			"OCR Fleet Slip",
			ocr_fleet_name,
			{
				"status": "Error",
				"error_log": str(e)[:2000],
			},
		)
		frappe.db.commit()  # nosemgrep
		frappe.log_error(
			title="Fleet Extraction Error",
			message=f"Fleet extraction failed for {ocr_fleet_name}: {frappe.get_traceback()}",
		)


def _populate_ocr_fleet(ocr_fleet, extracted_data: dict, settings):
	"""Populate OCR Fleet Slip fields from Gemini extraction result."""
	header = extracted_data.get("header_fields", {})
	fuel = extracted_data.get("fuel_details", {})
	toll = extracted_data.get("toll_details", {})

	# Header fields
	ocr_fleet.slip_type = header.get("slip_type", "")
	ocr_fleet.merchant_name_ocr = header.get("merchant_name", "")
	ocr_fleet.transaction_date = header.get("transaction_date", "")
	ocr_fleet.total_amount = header.get("total_amount", 0)
	ocr_fleet.vat_amount = header.get("vat_amount", 0)
	ocr_fleet.currency = header.get("currency", "")
	ocr_fleet.description = header.get("description", "")
	ocr_fleet.vehicle_registration = header.get("vehicle_registration", "")

	# Confidence: convert 0.0-1.0 to 0-100 percent
	try:
		raw_confidence = float(header.get("confidence") or 0.0)
	except (ValueError, TypeError):
		raw_confidence = 0.0
	ocr_fleet.confidence = max(0.0, min(100.0, raw_confidence * 100))

	# Fuel details
	ocr_fleet.litres = fuel.get("litres", 0)
	ocr_fleet.price_per_litre = fuel.get("price_per_litre", 0)
	ocr_fleet.fuel_type = fuel.get("fuel_type", "")
	ocr_fleet.odometer_reading = fuel.get("odometer_reading", 0)

	# Toll details
	ocr_fleet.toll_plaza_name = toll.get("toll_plaza_name", "")
	ocr_fleet.route = toll.get("route", "")

	# Unauthorized flag
	ocr_fleet.unauthorized_flag = 1 if ocr_fleet.slip_type == "Other" else 0

	# Raw payload
	ocr_fleet.raw_payload = json.dumps(extracted_data, indent=2, default=str)

	# Company (from settings if not already set)
	if not ocr_fleet.company:
		ocr_fleet.company = settings.default_company

	# Tax template based on VAT detection
	vat_amount = ocr_fleet.vat_amount or 0
	if vat_amount > 0 and settings.get("default_tax_template"):
		ocr_fleet.tax_template = settings.default_tax_template
	elif settings.get("non_vat_tax_template"):
		ocr_fleet.tax_template = settings.non_vat_tax_template


def _run_fleet_matching(ocr_fleet, settings):
	"""Run vehicle matching on the OCR Fleet Slip."""
	_match_vehicle(ocr_fleet, settings)


def _clear_vehicle_links(ocr_fleet):
	"""Clear all vehicle-derived fields (used on unmatched paths and retry)."""
	ocr_fleet.fleet_vehicle = ""
	ocr_fleet.vehicle_match_status = "Unmatched"
	ocr_fleet.fleet_card_supplier = ""
	ocr_fleet.posting_mode = ""
	ocr_fleet.expense_account = ""
	ocr_fleet.cost_center = ""


def _match_vehicle(ocr_fleet, settings):
	"""Match extracted vehicle_registration to Fleet Vehicle."""
	reg = (ocr_fleet.vehicle_registration or "").strip().upper()
	if not reg:
		_clear_vehicle_links(ocr_fleet)
		return

	# Check if Fleet Vehicle DocType exists (fleet_management app may not be installed)
	if not frappe.db.exists("DocType", "Fleet Vehicle"):
		_clear_vehicle_links(ocr_fleet)
		return

	# Exact match on registration
	vehicle = frappe.db.get_value(
		"Fleet Vehicle",
		{"registration": reg, "is_active": 1},
		[
			"name",
			"registration",
			"custom_fleet_card_provider",
			"custom_fleet_control_account",
			"custom_cost_center",
		],
		as_dict=True,
	)

	if vehicle:
		ocr_fleet.fleet_vehicle = vehicle.name
		ocr_fleet.vehicle_match_status = "Auto Matched"
		_apply_vehicle_config(ocr_fleet, vehicle, settings)
		return

	# Fuzzy match: normalize by stripping spaces, hyphens, underscores
	normalized_reg = reg.replace(" ", "").replace("-", "").replace("_", "")

	all_vehicles = frappe.get_all(
		"Fleet Vehicle",
		filters={"is_active": 1},
		fields=[
			"name",
			"registration",
			"custom_fleet_card_provider",
			"custom_fleet_control_account",
			"custom_cost_center",
		],
	)

	for v in all_vehicles:
		v_normalized = (v.registration or "").replace(" ", "").replace("-", "").replace("_", "").upper()
		if v_normalized and v_normalized == normalized_reg:
			ocr_fleet.fleet_vehicle = v.name
			ocr_fleet.vehicle_match_status = "Suggested"
			_apply_vehicle_config(ocr_fleet, v, settings)
			return

	_clear_vehicle_links(ocr_fleet)


def _apply_vehicle_config(ocr_fleet, vehicle, settings):
	"""Set posting mode, supplier, and accounts from vehicle configuration."""
	if vehicle.get("custom_fleet_card_provider"):
		ocr_fleet.posting_mode = "Fleet Card"
		ocr_fleet.fleet_card_supplier = vehicle.custom_fleet_card_provider
		ocr_fleet.expense_account = vehicle.get("custom_fleet_control_account") or ""
	else:
		ocr_fleet.posting_mode = "Direct Expense"
		ocr_fleet.fleet_card_supplier = settings.get("fleet_default_supplier") or ""
		ocr_fleet.expense_account = settings.get("fleet_expense_account") or ""

	if vehicle.get("custom_cost_center"):
		ocr_fleet.cost_center = vehicle.custom_cost_center


# ── doc_events hooks ──────────────────────────────────────────────


def update_ocr_fleet_on_submit(doc, method):
	"""Called when a PI is submitted — mark linked OCR Fleet Slip as Completed."""
	if doc.doctype != "Purchase Invoice":
		return
	field = "purchase_invoice"

	# Find OCR Fleet Slip where field == doc.name and status == "Draft Created"
	fleet_slips = frappe.get_all(
		"OCR Fleet Slip",
		filters={field: doc.name, "status": "Draft Created"},
		pluck="name",
	)

	for name in fleet_slips:
		frappe.db.set_value("OCR Fleet Slip", name, "status", "Completed")

	if fleet_slips:
		frappe.db.commit()  # nosemgrep


def update_ocr_fleet_on_cancel(doc, method):
	"""Called when a PI is cancelled — reset linked OCR Fleet Slip to Matched."""
	if doc.doctype != "Purchase Invoice":
		return
	field = "purchase_invoice"

	fleet_slips = frappe.get_all(
		"OCR Fleet Slip",
		filters={field: doc.name, "status": "Completed"},
		pluck="name",
	)

	for name in fleet_slips:
		ocr_doc = frappe.get_doc("OCR Fleet Slip", name)
		ocr_doc.db_set(field, "")
		ocr_doc.db_set("document_type", "")
		ocr_doc.status = "Matched"  # _update_status() will recompute on save
		ocr_doc.save(ignore_permissions=True)

	if fleet_slips:
		frappe.db.commit()  # nosemgrep


# ── Retry endpoint ──────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def retry_fleet_extraction(ocr_fleet_name: str):
	"""Retry Gemini extraction on an Error-state OCR Fleet Slip."""
	if not frappe.has_permission("OCR Fleet Slip", "write", ocr_fleet_name):
		frappe.throw(_("You don't have permission to retry this extraction."))

	ocr_fleet = frappe.get_doc("OCR Fleet Slip", ocr_fleet_name)
	if ocr_fleet.status != "Error":
		frappe.throw(_("Can only retry extraction on records with Error status."))

	# Try to get content from attachment
	file_content = None
	filename = None
	mime_type = "application/pdf"

	files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "OCR Fleet Slip",
			"attached_to_name": ocr_fleet_name,
			"is_private": 1,
		},
		fields=["name", "file_url", "file_name"],
		limit=1,
	)

	if files:
		file_doc = frappe.get_doc("File", files[0].name)
		file_content = file_doc.get_content()
		filename = files[0].file_name
		mime_type = _mime_type_from_filename(filename)
	elif ocr_fleet.drive_file_id:
		# Try to download from Drive
		from erpocr_integration.tasks.drive_integration import download_file_from_drive

		file_content = download_file_from_drive(ocr_fleet.drive_file_id)
		filename = f"fleet_scan_{ocr_fleet_name}.pdf"

	if not file_content:
		frappe.throw(_("No file found to retry extraction. Upload a new file or check Drive access."))

	# Reset status and clear stale links from previous run
	frappe.db.set_value(
		"OCR Fleet Slip",
		ocr_fleet_name,
		{
			"status": "Pending",
			"fleet_vehicle": "",
			"vehicle_match_status": "",
			"fleet_card_supplier": "",
			"posting_mode": "",
			"expense_account": "",
			"cost_center": "",
			"document_type": "",
		},
	)
	frappe.db.commit()  # nosemgrep

	try:
		frappe.enqueue(
			"erpocr_integration.fleet_api.fleet_gemini_process",
			queue="long",
			timeout=300,
			file_content=file_content,
			filename=filename,
			ocr_fleet_name=ocr_fleet_name,
			mime_type=mime_type,
			queue_position=0,
		)
	except Exception:
		# Enqueue failed — revert to Error so it doesn't sit as stale Pending
		frappe.db.set_value("OCR Fleet Slip", ocr_fleet_name, "status", "Error")
		frappe.db.commit()  # nosemgrep
		frappe.log_error(
			title="OCR Fleet Retry Enqueue Error",
			message=f"Failed to enqueue retry for {ocr_fleet_name}\n{frappe.get_traceback()}",
		)
		frappe.throw(_("Failed to start retry. Please try again."))

	frappe.msgprint(_("Retry extraction queued. Please wait a moment and refresh."), indicator="blue")


def _mime_type_from_filename(filename: str) -> str:
	"""Determine MIME type from filename extension."""
	ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
	return {
		".pdf": "application/pdf",
		".jpg": "image/jpeg",
		".jpeg": "image/jpeg",
		".png": "image/png",
	}.get(ext, "application/pdf")
