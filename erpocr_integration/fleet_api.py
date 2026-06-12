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

import frappe
from frappe import _

# ── source_type vocabulary (the cross-app source discriminator) ──────────────
# OCR Fleet Slip.source_type is a Data field that records HOW a slip was
# ingested. It is the contract's source discriminator — set server-side as a
# constant, NEVER from client input. Downstream consumers (fleet_management
# Wesbank recon, Fuel Efficiency Tracker) currently consume slips by record
# (fleet_vehicle + transaction_date + status) and do NOT read source_type
# (verified, P4 T1) — but if any consumer ever discriminates on it, these are
# the only two values it must know about. See CROSS_APP_SURFACE.md §6.
SOURCE_TYPE_DRIVE = "Gemini Drive Scan"  # 15-min Drive folder poll (poll_drive_fleet_folder)
SOURCE_TYPE_SHELL = "Gemini Shell Upload"  # phone capture via upload_fleet_slip (this contract)

# The upload contract enforces its OWN size boundary (2MB). The shell compresses
# to ≤1.5MB client-side; this is the server-side ceiling, deliberately tighter
# than the 10MB the manual invoice uploader allows — a fleet slip photo never
# legitimately needs more, and the cap caps Gemini base64-inlining cost on 3G.
MAX_FLEET_UPLOAD_SIZE = 2 * 1024 * 1024


def fleet_gemini_process(
	file_content: bytes,
	filename: str,
	ocr_fleet_name: str,
	mime_type: str = "application/pdf",
):
	"""
	Background job: extract fleet slip data via Gemini and populate the OCR Fleet Slip.

	Enqueued by poll_drive_fleet_folder. Runs on the 'long' queue.
	"""
	frappe.set_user("Administrator")

	try:
		frappe.db.set_value("OCR Fleet Slip", ocr_fleet_name, "status", "Pending")
		frappe.db.commit()  # nosemgrep

		from erpocr_integration.tasks.gemini_extract import extract_fleet_slip_data

		extracted_data = extract_fleet_slip_data(file_content, filename, mime_type=mime_type)

		settings = frappe.get_cached_doc("OCR Settings")
		ocr_fleet = frappe.get_doc("OCR Fleet Slip", ocr_fleet_name)

		_populate_ocr_fleet(ocr_fleet, extracted_data, settings)

		# Vehicle matching. A shell upload (source_type == Gemini Shell Upload)
		# where the driver already picked the vehicle arrives with
		# vehicle_match_status == "Confirmed" and its config applied (fail-safe)
		# at upload time — do NOT let OCR plate re-matching clobber the driver's
		# pick. Otherwise run matching; shell-sourced slips fail safe (a vehicle
		# with no fleet-card provider lands in Needs Review, never the invoice
		# path), Drive-sourced slips keep their original behaviour.
		if not (ocr_fleet.vehicle_match_status == "Confirmed" and ocr_fleet.fleet_vehicle):
			fail_safe = (ocr_fleet.source_type or "").startswith("Gemini Shell")
			_run_fleet_matching(ocr_fleet, settings, fail_safe=fail_safe)

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


def _run_fleet_matching(ocr_fleet, settings, fail_safe=False):
	"""Run vehicle matching on the OCR Fleet Slip.

	fail_safe is threaded to _apply_vehicle_config — see its docstring. True for
	shell/API-sourced slips (a provider-less vehicle lands in Needs Review, never
	the invoice path); False (default) keeps the Drive pipeline's behaviour.
	"""
	_match_vehicle(ocr_fleet, settings, fail_safe=fail_safe)


def _clear_vehicle_links(ocr_fleet):
	"""Clear all vehicle-derived fields (used on unmatched paths and retry)."""
	ocr_fleet.fleet_vehicle = ""
	ocr_fleet.vehicle_match_status = "Unmatched"
	ocr_fleet.fleet_card_supplier = ""
	ocr_fleet.posting_mode = ""
	ocr_fleet.expense_account = ""
	ocr_fleet.cost_center = ""


def _match_vehicle(ocr_fleet, settings, fail_safe=False):
	"""Match extracted vehicle_registration to Fleet Vehicle.

	fail_safe is passed through to _apply_vehicle_config on every match tier.
	"""
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
		_apply_vehicle_config(ocr_fleet, vehicle, settings, fail_safe=fail_safe)
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
			_apply_vehicle_config(ocr_fleet, v, settings, fail_safe=fail_safe)
			return

	# Fuzzy match tier 2: similarity score (catches Gemini character misreads
	# on photographed plates — L/1, 5/S, X/N, 0/O, etc.). High threshold +
	# unambiguous-best-match guard to avoid posting expenses to the wrong vehicle.
	candidate = _fuzzy_match_vehicle(normalized_reg, all_vehicles)
	if candidate:
		ocr_fleet.fleet_vehicle = candidate.name
		ocr_fleet.vehicle_match_status = "Suggested"
		_apply_vehicle_config(ocr_fleet, candidate, settings, fail_safe=fail_safe)
		return

	_clear_vehicle_links(ocr_fleet)


# OCR misreads commonly confuse these letter/digit pairs on number plates.
# Apply the same mapping to both the OCR'd registration and each candidate
# so confusable forms compare as equal during fuzzy matching.
_PLATE_OCR_CANONICAL = str.maketrans(
	{
		"O": "0",
		"Q": "0",
		"I": "1",
		"L": "1",
		"Z": "2",
		"S": "5",
		"G": "6",
		"B": "8",
	}
)


def _canonicalize_plate(s: str) -> str:
	"""Fold OCR-confusable letter↔digit pairs (S↔5, L↔1, B↔8, …) into a
	canonical form so that ``CXXS79C`` (Gemini misread of ``CXX579L``)
	compares as ``CXX579C`` against the canonical ``CXX5791``. The mapping
	is applied to both sides so the comparison stays symmetric."""
	return s.translate(_PLATE_OCR_CANONICAL)


def _fuzzy_match_vehicle(normalized_reg: str, all_vehicles: list, threshold: float = 0.78):
	"""
	Score each Fleet Vehicle's normalized registration against the OCR'd one
	using difflib.SequenceMatcher. Return the unambiguous best match.

	Each candidate is scored twice — once on the raw normalized form, and
	once on an OCR-canonicalized form that collapses common digit/letter
	confusables (S↔5, L↔1, B↔8, O↔0, Z↔2, G↔6, I↔1, Q↔0). The higher of
	the two ratios is used. This catches photographed plates where Gemini
	reads ``CXXS79C`` (S↔5 + L↔C) for the real ``CXX579L`` — the raw ratio
	is 0.71 but canonicalization brings it to 0.86.

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

	canonical_reg = _canonicalize_plate(normalized_reg)
	scores = []
	for v in all_vehicles:
		v_normalized = (v.registration or "").replace(" ", "").replace("-", "").replace("_", "").upper()
		if not v_normalized or abs(len(v_normalized) - len(normalized_reg)) > 2:
			continue
		raw_ratio = SequenceMatcher(None, normalized_reg, v_normalized).ratio()
		canon_ratio = SequenceMatcher(None, canonical_reg, _canonicalize_plate(v_normalized)).ratio()
		scores.append((max(raw_ratio, canon_ratio), v))

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


def _apply_vehicle_config(ocr_fleet, vehicle, settings, fail_safe=False):
	"""Set posting mode, supplier, and accounts from vehicle configuration.

	fail_safe (shell/API uploads): when the matched vehicle has NO fleet-card
	provider, do NOT fall through to Direct Expense + default supplier (the
	invoice path). Leave posting_mode/supplier/expense blank so the slip lands in
	Needs Review (no supplier) and the PI guard (posting_mode != "Direct Expense")
	blocks any invoice creation until an OCR Manager explicitly disposes it. The
	recon-vs-invoice fork must never depend on custom_fleet_card_provider being
	perfectly maintained (P4 fail-safe ruling — see CROSS_APP_SURFACE.md). Drive
	ingestion keeps the original Direct-Expense fallback (fail_safe=False).
	"""
	if vehicle.get("custom_fleet_card_provider"):
		ocr_fleet.posting_mode = "Fleet Card"
		ocr_fleet.fleet_card_supplier = vehicle.custom_fleet_card_provider
		ocr_fleet.expense_account = vehicle.get("custom_fleet_control_account") or ""
	elif fail_safe:
		# Provider missing on an API slip → fail safe to review, never invoice path.
		ocr_fleet.posting_mode = ""
		ocr_fleet.fleet_card_supplier = ""
		ocr_fleet.expense_account = ""
	else:
		ocr_fleet.posting_mode = "Direct Expense"
		ocr_fleet.fleet_card_supplier = settings.get("fleet_default_supplier") or ""
		ocr_fleet.expense_account = settings.get("fleet_expense_account") or ""

	if vehicle.get("custom_cost_center"):
		ocr_fleet.cost_center = vehicle.custom_cost_center


# ── Upload contract (driver shell, P4) ───────────────────────────────────────


def _shape_upload_response(ocr_fleet, *, duplicate: bool) -> dict:
	"""Minimal phone-renderable response for upload_fleet_slip.

	Same shape for a fresh upload and an idempotent replay so the shell renders
	one thing. The driver does NOT need the extracted data — the slip is a recon
	artifact reviewed by accounts later; the shell only needs to know it landed.
	"""
	return {
		"ocr_fleet_slip": ocr_fleet.name,
		"status": ocr_fleet.status,
		"client_request_id": ocr_fleet.client_request_id,
		"duplicate": duplicate,
	}


@frappe.whitelist(methods=["POST"])
def upload_fleet_slip(
	client_request_id: str,
	fleet_vehicle: str | None = None,
	vehicle_registration: str | None = None,
	captured_at: str | None = None,
):
	"""Land a phone-captured fleet slip as an OCR Fleet Slip recon record.

	The driver shell's fleet-slip write contract (P4). Multipart file upload;
	Gemini extraction is queued async (the driver's task ends at "queued"). The
	endpoint ONLY ever creates an OCR Fleet Slip — it is structurally incapable
	of creating or feeding a Purchase Invoice (the v1.2.0 recon invariant). A
	Drive-sourced slip and an API-sourced slip are indistinguishable to
	downstream consumers except for source_type.

	Request: multipart/form-data with a binary ``file`` field (JPEG/PNG/PDF,
	≤2MB, magic-byte validated) plus the form params below.

	Args:
		client_request_id: client-generated UUID, REQUIRED. Generated once per
			capture and reused verbatim on every retry — a replay returns the
			ORIGINAL slip with ``duplicate: true`` instead of creating a second
			(3G retries are the norm). The DB UNIQUE constraint on
			OCR Fleet Slip.client_request_id is the enforcement point.
		fleet_vehicle: optional Fleet Vehicle name. When supplied (shell
			pre-fills from the driver's vehicle, picker-overridable) the vehicle
			is set + Confirmed and OCR plate matching is skipped — far more
			reliable than guessing the plate off a photo.
		vehicle_registration: optional fallback plate string when no vehicle is
			picked; OCR matching runs async (fail-safe — see below).
		captured_at: optional ISO datetime of capture on the device, stored
			distinctly from the server-side creation timestamp (offline-queued
			uploads arrive late).

	Fail-safe fork: posting_mode is derived from the vehicle's
	custom_fleet_card_provider. If the provider is missing the slip lands in
	Needs Review with a blank posting_mode (and the PI guard blocks any invoice)
	— NEVER silently routed toward the invoice path. The recon-vs-invoice fork
	must not depend on a data field being perfectly maintained.

	Returns (same shape for fresh + replay):
		{"ocr_fleet_slip": <OCR-FS-…>, "status": <str>,
		 "client_request_id": <uuid>, "duplicate": bool}

	Raises: PermissionError for guests / callers without OCR Fleet Slip create;
	ValidationError for a missing key, a missing/oversize/wrong-type file, or an
	unknown vehicle.
	"""
	from erpocr_integration.api import SUPPORTED_FILE_TYPES, validate_file_magic_bytes

	# ── Permission: create-on-OCR-Fleet-Slip ONLY ──────────────────────────
	# Deliberately NOT gated on OCR Import create — the driver role grants
	# create on OCR Fleet Slip and nothing else, so this endpoint can never open
	# the invoice (OCR Import) surface. Guest is denied explicitly.
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in to upload a fleet slip."), frappe.PermissionError)
	if not frappe.has_permission("OCR Fleet Slip", "create"):
		frappe.throw(_("You do not have permission to upload fleet slips."), frappe.PermissionError)

	# ── Idempotency key (required) ─────────────────────────────────────────
	if not (client_request_id and str(client_request_id).strip()):
		frappe.throw(_("client_request_id is required (idempotency key)."))
	client_request_id = str(client_request_id).strip()

	# ── Multipart file: presence, type, size (own 2MB boundary), magic bytes ─
	if not frappe.request or not frappe.request.files:
		frappe.throw(_("No file uploaded."))
	file = frappe.request.files.get("file")
	if not file:
		frappe.throw(_("No file found in request."))

	filename = file.filename or ""
	file_ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
	mime_type = SUPPORTED_FILE_TYPES.get(file_ext)
	if not mime_type:
		supported = ", ".join(SUPPORTED_FILE_TYPES.keys())
		frappe.throw(_("Unsupported file type. Accepted formats: {0}").format(supported))

	file.seek(0, 2)
	file_size = file.tell()
	file.seek(0)
	if file_size > MAX_FLEET_UPLOAD_SIZE:
		frappe.throw(
			_(
				"File too large. Maximum size is 2MB. Your file is {0:.2f}MB. Compress before uploading."
			).format(file_size / (1024 * 1024))
		)
	file_content = file.read()
	if not validate_file_magic_bytes(file_content, mime_type):
		frappe.throw(_("File content does not match its file type. The file may be corrupted."))

	# ── Settings / company ─────────────────────────────────────────────────
	settings = frappe.get_cached_doc("OCR Settings")
	if not settings.get("default_company"):
		frappe.throw(_("Please set Default Company in OCR Settings."))

	# ── Vehicle resolution (driver-supplied beats plate-OCR) ───────────────
	vehicle_doc = None
	if fleet_vehicle:
		if not frappe.db.exists("DocType", "Fleet Vehicle"):
			frappe.throw(_("Fleet Vehicle support is not available on this site."))
		vehicle_doc = frappe.db.get_value(
			"Fleet Vehicle",
			{"name": fleet_vehicle, "is_active": 1},
			[
				"name",
				"registration",
				"custom_fleet_card_provider",
				"custom_fleet_control_account",
				"custom_cost_center",
			],
			as_dict=True,
		)
		if not vehicle_doc:
			frappe.throw(
				_("Fleet Vehicle {0} not found or inactive.").format(fleet_vehicle),
				frappe.DoesNotExistError,
			)

	# ── Build the slip (server-stamped identity + source) ──────────────────
	ocr_fleet = frappe.get_doc(
		{
			"doctype": "OCR Fleet Slip",
			"status": "Pending",
			# Constant source discriminator — NEVER from client input.
			"source_type": SOURCE_TYPE_SHELL,
			# Owner is auto-stamped to the session user by Frappe; uploaded_by
			# records it explicitly (the client cannot set either).
			"uploaded_by": frappe.session.user,
			"company": settings.default_company,
			"client_request_id": client_request_id,
		}
	)

	if captured_at:
		try:
			from frappe.utils import get_datetime

			ocr_fleet.captured_at = get_datetime(captured_at)
		except Exception:
			# A malformed device timestamp must never block the recon upload.
			frappe.logger().warning(f"upload_fleet_slip: ignoring unparseable captured_at {captured_at!r}")

	if vehicle_doc:
		# Driver-confirmed vehicle: set it + skip async plate matching. Apply the
		# vehicle config fail-safe so a provider-less vehicle lands in Needs
		# Review rather than the invoice path.
		ocr_fleet.fleet_vehicle = vehicle_doc.name
		ocr_fleet.vehicle_match_status = "Confirmed"
		_apply_vehicle_config(ocr_fleet, vehicle_doc, settings, fail_safe=True)
	elif vehicle_registration:
		# No confirmed vehicle — store the raw plate; extraction matches it async
		# (fail-safe, because source_type is a shell upload).
		ocr_fleet.vehicle_registration = str(vehicle_registration).strip()

	# ── Insert-and-catch (R-B house idempotency template, verbatim) ────────
	# The DB UNIQUE index on client_request_id (not an app-level pre-check) is
	# the enforcement point, so a concurrent 3G retry that loses the race returns
	# the original slip instead of creating a second. A FULL rollback (not
	# rollback-to-savepoint) is deliberate: under REPEATABLE READ this
	# transaction's snapshot was fixed before the winning insert committed, so a
	# same-transaction re-fetch would miss it — ending the transaction opens a
	# fresh snapshot that sees the committed original. Nothing else in this
	# request needs preserving (only our own failed insert ran).
	try:
		ocr_fleet.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError) as exc:
		frappe.db.rollback()
		frappe.clear_messages()
		try:
			existing = frappe.get_doc("OCR Fleet Slip", {"client_request_id": client_request_id})
		except frappe.DoesNotExistError:
			# No row carries this key → the violation was NOT the idempotency key
			# (an unexpected unique constraint). Surface the real error.
			raise exc from None
		return _shape_upload_response(existing, duplicate=True)

	# ── Attach the image + queue extraction ATOMICALLY with the slip insert ──
	# The slip row, its private File, and the extraction job all land on ONE
	# commit. Any failure before that commit rolls the whole unit back, so an
	# idempotent retry creates a fresh, COMPLETE slip rather than replaying a
	# half-built one (a committed keyed row with no image / no queued job) that
	# retries could never repair. enqueue_after_commit ties the job to the commit:
	# it fires only if the transaction lands, and the worker is then guaranteed to
	# find the committed slip (no worker-races-commit window).
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"content": file_content,
			"attached_to_doctype": "OCR Fleet Slip",
			"attached_to_name": ocr_fleet.name,
			"is_private": 1,  # office-visible via OCR Manager read; not publicly exposed
		}
	).insert(ignore_permissions=True)

	frappe.enqueue(
		"erpocr_integration.fleet_api.fleet_gemini_process",
		queue="long",
		timeout=600,
		enqueue_after_commit=True,
		file_content=file_content,
		filename=filename,
		ocr_fleet_name=ocr_fleet.name,
		mime_type=mime_type,
	)

	frappe.db.commit()  # nosemgrep — slip + File + queued job land atomically
	return _shape_upload_response(ocr_fleet, duplicate=False)


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
			timeout=600,
			file_content=file_content,
			filename=filename,
			ocr_fleet_name=ocr_fleet_name,
			mime_type=mime_type,
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
			timeout=600,
			pdf_content=file_content,
			filename=filename,
			ocr_import_name=ocr_import.name,
			source_type="Gemini Drive Scan",
			uploaded_by=frappe.session.user,
			mime_type=mime_type,
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
