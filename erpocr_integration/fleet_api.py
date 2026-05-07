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

	# Fuzzy match tier 1: normalize by stripping spaces, hyphens, underscores
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

	# Fuzzy match tier 2: similarity score (catches Gemini character misreads
	# on photographed plates — L/1, 5/S, X/N, 0/O, etc.). High threshold +
	# unambiguous-best-match guard to avoid posting expenses to the wrong vehicle.
	candidate = _fuzzy_match_vehicle(normalized_reg, all_vehicles)
	if candidate:
		ocr_fleet.fleet_vehicle = candidate.name
		ocr_fleet.vehicle_match_status = "Suggested"
		_apply_vehicle_config(ocr_fleet, candidate, settings)
		return

	_clear_vehicle_links(ocr_fleet)


def _fuzzy_match_vehicle(normalized_reg: str, all_vehicles: list, threshold: float = 0.78):
	"""
	Score each Fleet Vehicle's normalized registration against the OCR'd one
	using difflib.SequenceMatcher. Return the unambiguous best match.

	Args:
		normalized_reg: OCR registration, already stripped + uppercased
		all_vehicles: list of Fleet Vehicle dicts (from caller)
		threshold: minimum similarity ratio (0-1) for the best match

	Returns:
		Vehicle dict on a confident, unambiguous match; None otherwise.

	Three guards prevent posting expenses to the wrong vehicle (worse than
	asking the user to pick):
	  1. Length guard — skip candidates whose normalized length differs
	     by >2 from the OCR.
	  2. Plausibility-band guard — if more than one candidate scores
	     within 0.15 of the best, the input could plausibly belong to
	     either vehicle (e.g. sequential plates CXX578L vs CXX579L when
	     OCR yields CXX5781). Refuse the match.
	  3. Tight-ambiguity guard — if the second-best is within 0.05 of
	     the best, they're effectively tied; refuse.
	"""
	from difflib import SequenceMatcher

	if not normalized_reg or len(normalized_reg) < 4:
		return None

	scores = []
	for v in all_vehicles:
		v_normalized = (v.registration or "").replace(" ", "").replace("-", "").replace("_", "").upper()
		if not v_normalized or abs(len(v_normalized) - len(normalized_reg)) > 2:
			continue
		ratio = SequenceMatcher(None, normalized_reg, v_normalized).ratio()
		scores.append((ratio, v))

	if not scores:
		return None

	scores.sort(key=lambda x: x[0], reverse=True)
	best_ratio, best_vehicle = scores[0]
	if best_ratio < threshold:
		return None

	# Plausibility-band guard: more than one vehicle within 0.15 of best
	# means the OCR input could plausibly be EITHER. Sequential-plate fleets
	# (CXX578L, CXX579L) trigger this on a single-digit OCR slip — without
	# this guard, a misread of CXX579L → CXX5781 would silently match
	# CXX578L instead.
	plausible = [r for r, _v in scores if r >= best_ratio - 0.15]
	if len(plausible) > 1:
		return None

	# Tight-ambiguity guard: second-best within 0.05 → effectively tied
	if len(scores) > 1 and scores[1][0] >= best_ratio - 0.05:
		return None

	return best_vehicle


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


@frappe.whitelist(methods=["POST"])
def route_to_invoice_pipeline(ocr_fleet_name: str):
	"""
	Re-route a fleet slip scan into the regular OCR Import (invoice) pipeline.

	Used when a driver mistakenly drops a non-fleet-card slip (paid with a
	personal or company credit card) into the fleet Drive folder. The fleet
	pipeline assumes a fleet card supplier; for these slips we want the
	normal supplier-matching flow.

	Flow:
	  1. Read the original scan attachment from the fleet slip.
	  2. Create a new OCR Import placeholder + copy the scan to it.
	  3. Enqueue the standard `gemini_process` job (invoice extraction).
	  4. Mark the OCR Fleet Slip as "No Action" with a reason that links
	     to the new OCR Import.

	Permission posture: requires write on the OCR Fleet Slip AND create on
	OCR Import. A narrow Reader role (write on Fleet Slip only) cannot use
	this — they'd open a creation surface they shouldn't have.

	Returns: name of the newly created OCR Import (for client redirect).
	"""
	if not frappe.has_permission("OCR Fleet Slip", "write", ocr_fleet_name):
		frappe.throw(_("You don't have permission to modify this fleet slip."))
	if not frappe.has_permission("OCR Import", "create"):
		frappe.throw(_("You don't have permission to create OCR Imports."))

	ocr_fleet = frappe.get_doc("OCR Fleet Slip", ocr_fleet_name)

	# Guard: only allow re-routing when the slip is in a reviewable state.
	# Completed / Draft Created already have downstream documents and shouldn't
	# be re-routed; No Action records are already terminal.
	if ocr_fleet.status in ("Completed", "Draft Created", "No Action"):
		frappe.throw(_("Cannot re-route a fleet slip in '{0}' status.").format(ocr_fleet.status))

	# Find the original scan attachment
	files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "OCR Fleet Slip",
			"attached_to_name": ocr_fleet_name,
			"is_private": 1,
		},
		fields=["name", "file_name"],
		limit=1,
	)
	if not files:
		frappe.throw(_("No scan attachment found on this fleet slip."))

	source_file = frappe.get_doc("File", files[0].name)
	file_content = source_file.get_content()
	filename = source_file.file_name
	mime_type = _mime_type_from_filename(filename)

	# Create OCR Import placeholder
	ocr_import = frappe.get_doc(
		{
			"doctype": "OCR Import",
			"status": "Pending",
			"source_type": "Gemini Drive Scan",
			"uploaded_by": frappe.session.user,
			"company": ocr_fleet.company,
		}
	)
	ocr_import.insert(ignore_permissions=False)

	# Copy the scan to the new OCR Import as a private attachment
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"content": file_content,
			"attached_to_doctype": "OCR Import",
			"attached_to_name": ocr_import.name,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep

	# Enqueue invoice extraction
	try:
		frappe.enqueue(
			"erpocr_integration.api.gemini_process",
			queue="long",
			timeout=300,
			pdf_content=file_content,
			filename=filename,
			ocr_import_name=ocr_import.name,
			source_type="Gemini Drive Scan",
			uploaded_by=frappe.session.user,
			mime_type=mime_type,
			queue_position=0,
		)
	except Exception:
		frappe.db.set_value("OCR Import", ocr_import.name, "status", "Error")
		frappe.db.commit()  # nosemgrep
		frappe.log_error(
			title="Fleet Re-route Enqueue Error",
			message=f"Failed to enqueue re-routed extraction for {ocr_fleet_name} → {ocr_import.name}\n{frappe.get_traceback()}",
		)
		frappe.throw(
			_("Failed to start invoice extraction. The OCR Import was created but processing did not start.")
		)

	# Race guard: reload before the No Action save and re-check terminal statuses.
	# A concurrent doc_event (e.g. PI submit / cancel from a previously linked PI)
	# could have flipped the slip's status to Completed / Matched while we were
	# inserting the new OCR Import + enqueueing extraction. Overwriting Completed
	# with No Action would silently undo a valid downstream document.
	ocr_fleet.reload()
	if ocr_fleet.status in ("Completed", "Draft Created", "No Action"):
		frappe.log_error(
			title="Fleet Re-route Race Detected",
			message=(
				f"Fleet slip {ocr_fleet_name} status changed to '{ocr_fleet.status}' "
				f"during re-routing. New OCR Import {ocr_import.name} was created and "
				"left in its normal pipeline; source slip was NOT updated."
			),
		)
		frappe.throw(
			_(
				"Fleet slip status changed to '{0}' during re-routing. The new OCR Import "
				"{1} was created but the fleet slip was NOT updated. Please verify both records."
			).format(ocr_fleet.status, ocr_import.name)
		)

	# Mark the original fleet slip as No Action with a reason that points to the new record
	ocr_fleet.status = "No Action"
	ocr_fleet.no_action_reason = _("Moved to invoice pipeline as {0}").format(ocr_import.name)
	ocr_fleet.save(ignore_permissions=False)
	frappe.db.commit()  # nosemgrep

	return ocr_import.name


def _mime_type_from_filename(filename: str) -> str:
	"""Determine MIME type from filename extension."""
	ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
	return {
		".pdf": "application/pdf",
		".jpg": "image/jpeg",
		".jpeg": "image/jpeg",
		".png": "image/png",
	}.get(ext, "application/pdf")
