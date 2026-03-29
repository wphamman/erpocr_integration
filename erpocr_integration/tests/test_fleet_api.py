"""Tests for fleet_api.py — fleet processing, vehicle matching, doc events, retry."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.fleet_api import (
	_apply_vehicle_config,
	_match_vehicle,
	_populate_ocr_fleet,
	_run_fleet_matching,
	fleet_gemini_process,
	retry_fleet_extraction,
	update_ocr_fleet_on_cancel,
	update_ocr_fleet_on_submit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NS(SimpleNamespace):
	"""SimpleNamespace with .get() to mimic frappe._dict."""

	def get(self, key, default=None):
		return getattr(self, key, default)


class _MockSettings(_NS):
	pass


def _make_settings(**overrides):
	defaults = dict(
		default_company="Test Company",
		default_tax_template="SA VAT 15%",
		non_vat_tax_template="Non-VAT",
		fleet_fuel_item="FUEL-001",
		fleet_toll_item="TOLL-001",
		fleet_expense_account="5000 - Fuel Expense - TC",
		fleet_default_supplier="Default Supplier",
		default_item=None,
	)
	defaults.update(overrides)
	return _MockSettings(**defaults)


class MockFleetSlip:
	"""Lightweight mock for OCR Fleet Slip document."""

	def __init__(self, **kw):
		self.name = kw.get("name", "OCR-FS-00001")
		self.status = kw.get("status", "Pending")
		self.slip_type = kw.get("slip_type", "")
		self.merchant_name_ocr = kw.get("merchant_name_ocr", "")
		self.transaction_date = kw.get("transaction_date", None)
		self.total_amount = kw.get("total_amount", 0)
		self.vat_amount = kw.get("vat_amount", 0)
		self.currency = kw.get("currency", "")
		self.description = kw.get("description", "")
		self.confidence = kw.get("confidence", 0)
		self.vehicle_registration = kw.get("vehicle_registration", "")
		self.fleet_vehicle = kw.get("fleet_vehicle", None)
		self.vehicle_match_status = kw.get("vehicle_match_status", "Unmatched")
		self.posting_mode = kw.get("posting_mode", "")
		self.fleet_card_supplier = kw.get("fleet_card_supplier", "")
		self.expense_account = kw.get("expense_account", "")
		self.cost_center = kw.get("cost_center", "")
		self.company = kw.get("company", "Test Company")
		self.litres = kw.get("litres", 0)
		self.price_per_litre = kw.get("price_per_litre", 0)
		self.fuel_type = kw.get("fuel_type", "")
		self.odometer_reading = kw.get("odometer_reading", 0)
		self.toll_plaza_name = kw.get("toll_plaza_name", "")
		self.route = kw.get("route", "")
		self.unauthorized_flag = kw.get("unauthorized_flag", 0)
		self.raw_payload = kw.get("raw_payload", "")
		self.tax_template = kw.get("tax_template", "")
		self.drive_file_id = kw.get("drive_file_id", None)
		self.purchase_invoice = kw.get("purchase_invoice", None)
		self.source_type = kw.get("source_type", "Gemini Drive Scan")

	def get(self, key, default=None):
		return getattr(self, key, default)

	def save(self, **kw):
		pass

	def db_set(self, key, value=None, **kw):
		if isinstance(key, dict):
			for k, v in key.items():
				setattr(self, k, v)
		else:
			setattr(self, key, value)

	def reload(self):
		pass


# ---------------------------------------------------------------------------
# Sample fleet extraction data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_fuel_extracted():
	return {
		"header_fields": {
			"slip_type": "Fuel",
			"merchant_name": "Shell Garage N1",
			"transaction_date": "2025-12-15",
			"vehicle_registration": "ABC 123 GP",
			"total_amount": 1125.00,
			"vat_amount": 0,
			"currency": "ZAR",
			"confidence": 0.92,
			"description": "Diesel fill-up",
		},
		"fuel_details": {
			"litres": 50.0,
			"price_per_litre": 22.50,
			"fuel_type": "Diesel",
			"odometer_reading": 125000,
		},
		"toll_details": {
			"toll_plaza_name": "",
			"route": "",
		},
	}


@pytest.fixture
def sample_toll_extracted():
	return {
		"header_fields": {
			"slip_type": "Toll",
			"merchant_name": "SANRAL",
			"transaction_date": "2025-12-16",
			"vehicle_registration": "XYZ 789 GP",
			"total_amount": 95.00,
			"vat_amount": 14.13,
			"currency": "ZAR",
			"confidence": 0.88,
			"description": "",
		},
		"fuel_details": {
			"litres": 0,
			"price_per_litre": 0,
			"fuel_type": "",
			"odometer_reading": 0,
		},
		"toll_details": {
			"toll_plaza_name": "Huguenot Tunnel",
			"route": "N1",
		},
	}


@pytest.fixture
def sample_other_extracted():
	return {
		"header_fields": {
			"slip_type": "Other",
			"merchant_name": "Engen 1-Stop",
			"transaction_date": "2025-12-17",
			"vehicle_registration": "ABC 123 GP",
			"total_amount": 185.50,
			"vat_amount": 0,
			"currency": "ZAR",
			"confidence": 0.75,
			"description": "Doritos, Coca-Cola, Sandwich",
		},
		"fuel_details": {},
		"toll_details": {},
	}


# ---------------------------------------------------------------------------
# TestPopulateOcrFleet
# ---------------------------------------------------------------------------


class TestPopulateOcrFleet:
	def test_populates_fuel_slip(self, sample_fuel_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_fuel_extracted, settings)

		assert doc.slip_type == "Fuel"
		assert doc.merchant_name_ocr == "Shell Garage N1"
		assert doc.transaction_date == "2025-12-15"
		assert doc.vehicle_registration == "ABC 123 GP"
		assert doc.total_amount == 1125.00
		assert doc.vat_amount == 0
		assert doc.currency == "ZAR"
		assert doc.confidence == 92.0  # 0.92 * 100
		assert doc.litres == 50.0
		assert doc.price_per_litre == 22.50
		assert doc.fuel_type == "Diesel"
		assert doc.odometer_reading == 125000
		assert doc.unauthorized_flag == 0

	def test_populates_toll_slip(self, sample_toll_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_toll_extracted, settings)

		assert doc.slip_type == "Toll"
		assert doc.toll_plaza_name == "Huguenot Tunnel"
		assert doc.route == "N1"
		assert doc.total_amount == 95.00
		assert doc.vat_amount == 14.13
		assert doc.unauthorized_flag == 0

	def test_populates_other_with_unauthorized_flag(self, sample_other_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_other_extracted, settings)

		assert doc.slip_type == "Other"
		assert doc.unauthorized_flag == 1
		assert doc.description == "Doritos, Coca-Cola, Sandwich"

	def test_confidence_clamped_to_100(self):
		settings = _make_settings()
		doc = MockFleetSlip()
		data = {
			"header_fields": {"confidence": 1.5},
			"fuel_details": {},
			"toll_details": {},
		}
		_populate_ocr_fleet(doc, data, settings)
		assert doc.confidence == 100.0

	def test_confidence_clamped_to_zero(self):
		settings = _make_settings()
		doc = MockFleetSlip()
		data = {
			"header_fields": {"confidence": -0.5},
			"fuel_details": {},
			"toll_details": {},
		}
		_populate_ocr_fleet(doc, data, settings)
		assert doc.confidence == 0.0

	def test_company_from_settings(self, sample_fuel_extracted):
		settings = _make_settings()
		doc = MockFleetSlip(company="")
		_populate_ocr_fleet(doc, sample_fuel_extracted, settings)
		assert doc.company == "Test Company"

	def test_company_not_overwritten(self, sample_fuel_extracted):
		settings = _make_settings()
		doc = MockFleetSlip(company="Other Company")
		_populate_ocr_fleet(doc, sample_fuel_extracted, settings)
		assert doc.company == "Other Company"

	def test_vat_tax_template_applied(self, sample_toll_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_toll_extracted, settings)
		assert doc.tax_template == "SA VAT 15%"

	def test_non_vat_tax_template_applied(self, sample_fuel_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_fuel_extracted, settings)
		assert doc.tax_template == "Non-VAT"

	def test_raw_payload_stored(self, sample_fuel_extracted):
		settings = _make_settings()
		doc = MockFleetSlip()
		_populate_ocr_fleet(doc, sample_fuel_extracted, settings)
		assert doc.raw_payload  # non-empty JSON
		assert "Shell Garage" in doc.raw_payload

	def test_empty_fuel_details(self):
		settings = _make_settings()
		doc = MockFleetSlip()
		data = {
			"header_fields": {"slip_type": "Fuel"},
			"fuel_details": {},
			"toll_details": {},
		}
		_populate_ocr_fleet(doc, data, settings)
		assert doc.litres == 0
		assert doc.price_per_litre == 0

	def test_none_confidence_handled(self):
		settings = _make_settings()
		doc = MockFleetSlip()
		data = {
			"header_fields": {"confidence": None},
			"fuel_details": {},
			"toll_details": {},
		}
		_populate_ocr_fleet(doc, data, settings)
		assert doc.confidence == 0.0


# ---------------------------------------------------------------------------
# TestMatchVehicle
# ---------------------------------------------------------------------------


class TestMatchVehicle:
	def test_exact_match(self, mock_frappe):
		"""Exact registration match sets Auto Matched."""
		mock_frappe.db.exists.return_value = True  # Fleet Vehicle DocType exists
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-001",
			registration="ABC 123 GP",
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="Transport - TC",
		)

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="ABC 123 GP")
		_match_vehicle(doc, settings)

		assert doc.fleet_vehicle == "VEH-001"
		assert doc.vehicle_match_status == "Auto Matched"

	def test_normalized_fuzzy_match(self, mock_frappe):
		"""Fuzzy match (strip spaces/hyphens) produces Suggested status."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = None  # No exact match
		mock_frappe.get_all.return_value = [
			_NS(
				name="VEH-001",
				registration="ABC-123-GP",
				custom_fleet_card_provider="",
				custom_fleet_control_account="",
				custom_cost_center="",
			)
		]

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="ABC 123 GP")
		_match_vehicle(doc, settings)

		assert doc.fleet_vehicle == "VEH-001"
		assert doc.vehicle_match_status == "Suggested"

	def test_no_match(self, mock_frappe):
		"""No match leaves Unmatched status."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = None
		mock_frappe.get_all.return_value = [
			_NS(
				name="VEH-002",
				registration="XYZ 789 GP",
				custom_fleet_card_provider="",
				custom_fleet_control_account="",
				custom_cost_center="",
			)
		]

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="ABC 123 GP")
		_match_vehicle(doc, settings)

		assert doc.fleet_vehicle == ""
		assert doc.vehicle_match_status == "Unmatched"

	def test_empty_registration(self, mock_frappe):
		"""Empty registration is Unmatched."""
		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="")
		_match_vehicle(doc, settings)
		assert doc.vehicle_match_status == "Unmatched"

	def test_fleet_vehicle_not_installed(self, mock_frappe):
		"""No Fleet Vehicle DocType → Unmatched (graceful degradation)."""
		mock_frappe.db.exists.return_value = False

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="ABC 123 GP")
		_match_vehicle(doc, settings)

		assert doc.vehicle_match_status == "Unmatched"
		assert doc.fleet_vehicle == ""

	def test_registration_uppercased(self, mock_frappe):
		"""Registration is uppercased for matching."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-001",
			registration="abc 123 gp",
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="abc 123 gp")
		_match_vehicle(doc, settings)

		assert doc.fleet_vehicle == "VEH-001"

	def test_underscore_normalized(self, mock_frappe):
		"""Underscores are stripped in normalized match."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = None  # No exact match
		mock_frappe.get_all.return_value = [
			_NS(
				name="VEH-003",
				registration="ABC_123_GP",
				custom_fleet_card_provider="",
				custom_fleet_control_account="",
				custom_cost_center="",
			)
		]

		settings = _make_settings()
		doc = MockFleetSlip(vehicle_registration="ABC 123 GP")
		_match_vehicle(doc, settings)

		assert doc.fleet_vehicle == "VEH-003"
		assert doc.vehicle_match_status == "Suggested"


# ---------------------------------------------------------------------------
# TestApplyVehicleConfig
# ---------------------------------------------------------------------------


class TestApplyVehicleConfig:
	def test_fleet_card_mode(self):
		"""Vehicle with fleet card provider → Fleet Card posting mode."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="Transport - TC",
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.posting_mode == "Fleet Card"
		assert doc.fleet_card_supplier == "WesBank"
		assert doc.expense_account == "3100 - Fleet Control - TC"
		assert doc.cost_center == "Transport - TC"

	def test_direct_expense_mode(self):
		"""Vehicle without fleet card provider → Direct Expense mode."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="Head Office - TC",
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.posting_mode == "Direct Expense"
		assert doc.fleet_card_supplier == "Default Supplier"
		assert doc.expense_account == "5000 - Fuel Expense - TC"
		assert doc.cost_center == "Head Office - TC"

	def test_no_cost_center(self):
		"""No cost center on vehicle → cost_center not set."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, settings)
		assert doc.cost_center == ""  # unchanged from default

	def test_none_fleet_card_is_direct_expense(self):
		"""None value for fleet_card_provider → Direct Expense."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider=None,
			custom_fleet_control_account=None,
			custom_cost_center=None,
		)
		_apply_vehicle_config(doc, vehicle, settings)
		assert doc.posting_mode == "Direct Expense"


# ---------------------------------------------------------------------------
# TestDocEvents
# ---------------------------------------------------------------------------


class TestDocEvents:
	def test_pi_submit_marks_completed(self, mock_frappe):
		"""PI submit marks linked fleet slip as Completed."""
		mock_frappe.get_all.return_value = ["OCR-FS-00001"]

		pi_doc = SimpleNamespace(doctype="Purchase Invoice", name="PI-00001")
		update_ocr_fleet_on_submit(pi_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_once_with(
			"OCR Fleet Slip", "OCR-FS-00001", "status", "Completed"
		)

	def test_submit_no_linked_slips(self, mock_frappe):
		"""No linked fleet slips → no action."""
		mock_frappe.get_all.return_value = []

		pi_doc = SimpleNamespace(doctype="Purchase Invoice", name="PI-00001")
		update_ocr_fleet_on_submit(pi_doc, "on_submit")

		mock_frappe.db.set_value.assert_not_called()

	def test_pi_cancel_resets_to_matched(self, mock_frappe):
		"""PI cancel clears link and resets fleet slip."""
		mock_frappe.get_all.return_value = ["OCR-FS-00001"]
		mock_fleet = MockFleetSlip(
			name="OCR-FS-00001",
			status="Completed",
			purchase_invoice="PI-00001",
		)
		mock_frappe.get_doc.return_value = mock_fleet

		pi_doc = SimpleNamespace(doctype="Purchase Invoice", name="PI-00001")
		update_ocr_fleet_on_cancel(pi_doc, "on_cancel")

		assert mock_fleet.purchase_invoice == ""
		assert mock_fleet.document_type == ""
		assert mock_fleet.status == "Matched"

	def test_unrelated_doctype_ignored(self, mock_frappe):
		"""Non-PI doctypes are ignored."""
		doc = SimpleNamespace(doctype="Sales Invoice", name="SI-00001")
		update_ocr_fleet_on_submit(doc, "on_submit")
		mock_frappe.get_all.assert_not_called()

	def test_multiple_fleet_slips_completed(self, mock_frappe):
		"""Multiple fleet slips linked to same PI all get Completed."""
		mock_frappe.get_all.return_value = ["OCR-FS-00001", "OCR-FS-00002"]

		pi_doc = SimpleNamespace(doctype="Purchase Invoice", name="PI-00001")
		update_ocr_fleet_on_submit(pi_doc, "on_submit")

		assert mock_frappe.db.set_value.call_count == 2


# ---------------------------------------------------------------------------
# TestRetryFleetExtraction
# ---------------------------------------------------------------------------


class TestRetryFleetExtraction:
	def test_blocks_non_error(self, mock_frappe):
		"""Cannot retry non-Error records."""
		mock_fleet = MockFleetSlip(status="Matched")
		mock_frappe.get_doc.return_value = mock_fleet
		with pytest.raises(Exception):
			retry_fleet_extraction("OCR-FS-00001")

	def test_blocks_no_file(self, mock_frappe):
		"""Throws when no file is available for retry."""
		mock_fleet = MockFleetSlip(status="Error")
		mock_frappe.get_doc.return_value = mock_fleet
		mock_frappe.get_all.return_value = []
		with pytest.raises(Exception):
			retry_fleet_extraction("OCR-FS-00001")

	def test_retries_from_attachment(self, mock_frappe):
		"""Retries extraction from local file attachment."""
		mock_fleet = MockFleetSlip(status="Error", drive_file_id=None)
		mock_file = MagicMock()
		mock_file.get_content.return_value = b"%PDF-1.4 test"

		def get_doc_side_effect(doctype, name=None):
			if doctype == "OCR Fleet Slip":
				return mock_fleet
			if doctype == "File":
				return mock_file
			return MagicMock()

		mock_frappe.get_doc.side_effect = get_doc_side_effect
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="FILE-001", file_url="/private/files/scan.pdf", file_name="scan.pdf")
		]

		retry_fleet_extraction("OCR-FS-00001")
		mock_frappe.enqueue.assert_called_once()

	@patch("erpocr_integration.tasks.drive_integration.download_file_from_drive")
	def test_retries_from_drive(self, mock_download, mock_frappe):
		"""Retries extraction from Drive file."""
		mock_fleet = MockFleetSlip(status="Error", drive_file_id="drive-123")
		mock_frappe.get_doc.return_value = mock_fleet
		mock_frappe.get_all.return_value = []  # no attachment
		mock_download.return_value = b"%PDF-1.4 test"

		retry_fleet_extraction("OCR-FS-00001")
		mock_frappe.enqueue.assert_called_once()

	def test_permission_check(self, mock_frappe):
		"""Permission check blocks unauthorized users."""
		mock_frappe.has_permission.return_value = False
		with pytest.raises(Exception):
			retry_fleet_extraction("OCR-FS-00001")


# ---------------------------------------------------------------------------
# TestFleetGeminiProcess
# ---------------------------------------------------------------------------


class TestFleetGeminiProcess:
	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	@patch("erpocr_integration.fleet_api._run_fleet_matching")
	@patch("erpocr_integration.fleet_api._populate_ocr_fleet")
	def test_successful_extraction(self, mock_populate, mock_matching, mock_extract, mock_frappe):
		"""Successful extraction populates and saves fleet slip."""
		mock_extract.return_value = {
			"header_fields": {"slip_type": "Fuel"},
			"fuel_details": {},
			"toll_details": {},
		}
		mock_fleet = MockFleetSlip()
		mock_frappe.get_doc.return_value = mock_fleet
		mock_frappe.get_cached_doc.return_value = _make_settings()

		fleet_gemini_process(
			file_content=b"%PDF test",
			filename="fuel_slip.pdf",
			ocr_fleet_name="OCR-FS-00001",
		)

		mock_extract.assert_called_once()
		mock_populate.assert_called_once()
		mock_matching.assert_called_once()

	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	def test_extraction_failure_sets_error(self, mock_extract, mock_frappe):
		"""Failed extraction sets status to Error."""
		mock_extract.side_effect = Exception("API failure")

		fleet_gemini_process(
			file_content=b"%PDF test",
			filename="bad_slip.pdf",
			ocr_fleet_name="OCR-FS-00001",
		)

		mock_frappe.db.set_value.assert_any_call(
			"OCR Fleet Slip",
			"OCR-FS-00001",
			{"status": "Error", "error_log": "API failure"},
		)

	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	@patch("erpocr_integration.fleet_api._run_fleet_matching")
	@patch("erpocr_integration.fleet_api._populate_ocr_fleet")
	def test_sets_pending_before_extraction(self, mock_populate, mock_matching, mock_extract, mock_frappe):
		"""Status is set to Pending at the start."""
		mock_extract.return_value = {
			"header_fields": {},
			"fuel_details": {},
			"toll_details": {},
		}
		mock_frappe.get_doc.return_value = MockFleetSlip()
		mock_frappe.get_cached_doc.return_value = _make_settings()

		fleet_gemini_process(
			file_content=b"%PDF test",
			filename="slip.pdf",
			ocr_fleet_name="OCR-FS-00001",
		)

		# Verify Pending was set before processing
		mock_frappe.db.set_value.assert_any_call("OCR Fleet Slip", "OCR-FS-00001", "status", "Pending")

	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	@patch("erpocr_integration.fleet_api._run_fleet_matching")
	@patch("erpocr_integration.fleet_api._populate_ocr_fleet")
	@patch("erpocr_integration.tasks.drive_integration.move_file_to_archive")
	def test_archives_to_drive(self, mock_archive, mock_populate, mock_matching, mock_extract, mock_frappe):
		"""Successful extraction triggers Drive archive."""
		mock_extract.return_value = {
			"header_fields": {},
			"fuel_details": {},
			"toll_details": {},
		}
		mock_fleet = MockFleetSlip(drive_file_id="drive-123")
		mock_frappe.get_doc.return_value = mock_fleet
		mock_frappe.get_cached_doc.return_value = _make_settings()
		mock_archive.return_value = {
			"shareable_link": "https://drive.google.com/new-link",
			"file_id": "new-drive-id",
			"folder_path": "2025/12/Fleet Slips",
		}

		fleet_gemini_process(
			file_content=b"%PDF test",
			filename="slip.pdf",
			ocr_fleet_name="OCR-FS-00001",
		)

		mock_archive.assert_called_once()

	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	@patch("erpocr_integration.fleet_api._run_fleet_matching")
	@patch("erpocr_integration.fleet_api._populate_ocr_fleet")
	def test_stagger_with_queue_position(self, mock_populate, mock_matching, mock_extract, mock_frappe):
		"""Queue position > 0 triggers sleep stagger."""
		mock_extract.return_value = {
			"header_fields": {},
			"fuel_details": {},
			"toll_details": {},
		}
		mock_frappe.get_doc.return_value = MockFleetSlip()
		mock_frappe.get_cached_doc.return_value = _make_settings()

		with patch("time.sleep") as mock_sleep:
			fleet_gemini_process(
				file_content=b"%PDF test",
				filename="slip.pdf",
				ocr_fleet_name="OCR-FS-00001",
				queue_position=3,
			)
			mock_sleep.assert_called_once_with(15)  # 3 * 5 = 15

	@patch("erpocr_integration.tasks.gemini_extract.extract_fleet_slip_data")
	@patch("erpocr_integration.fleet_api._run_fleet_matching")
	@patch("erpocr_integration.fleet_api._populate_ocr_fleet")
	def test_stagger_capped_at_240(self, mock_populate, mock_matching, mock_extract, mock_frappe):
		"""Stagger delay is capped at 240 seconds."""
		mock_extract.return_value = {
			"header_fields": {},
			"fuel_details": {},
			"toll_details": {},
		}
		mock_frappe.get_doc.return_value = MockFleetSlip()
		mock_frappe.get_cached_doc.return_value = _make_settings()

		with patch("time.sleep") as mock_sleep:
			fleet_gemini_process(
				file_content=b"%PDF test",
				filename="slip.pdf",
				ocr_fleet_name="OCR-FS-00001",
				queue_position=100,
			)
			mock_sleep.assert_called_once_with(240)
