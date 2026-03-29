"""End-to-end workflow tests for OCR Fleet Slip pipeline."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from erpocr_integration.erpnext_ocr.doctype.ocr_fleet_slip.ocr_fleet_slip import (
	OCRFleetSlip,
)
from erpocr_integration.fleet_api import (
	_apply_vehicle_config,
	_match_vehicle,
	_populate_ocr_fleet,
	_run_fleet_matching,
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
		default_cost_center="Main - TC",
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
	"""Lightweight mock for workflow tests."""

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
		self.company = kw.get("company", "")
		self.litres = kw.get("litres", 0)
		self.price_per_litre = kw.get("price_per_litre", 0)
		self.fuel_type = kw.get("fuel_type", "")
		self.odometer_reading = kw.get("odometer_reading", 0)
		self.toll_plaza_name = kw.get("toll_plaza_name", "")
		self.route = kw.get("route", "")
		self.unauthorized_flag = kw.get("unauthorized_flag", 0)
		self.raw_payload = ""
		self.tax_template = kw.get("tax_template", "")
		self.purchase_invoice = kw.get("purchase_invoice", None)
		self.drive_file_id = kw.get("drive_file_id", None)

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
# TestFuelSlipFleetCardWorkflow
# ---------------------------------------------------------------------------


class TestFuelSlipFleetCardWorkflow:
	"""Scenario A: Fuel slip + fleet card vehicle → PI."""

	def test_populate_then_match_fleet_card(self, mock_frappe):
		"""Full pipeline: populate → match → fleet card config applied."""
		mock_frappe.db.exists.return_value = True  # Fleet Vehicle DocType
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-001",
			registration="ABC 123 GP",
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="Transport - TC",
		)
		mock_frappe.get_all.return_value = []

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Fuel",
				"merchant_name": "Shell N1",
				"transaction_date": "2025-12-15",
				"vehicle_registration": "ABC 123 GP",
				"total_amount": 1125.00,
				"vat_amount": 0,
				"currency": "ZAR",
				"confidence": 0.92,
				"description": "",
			},
			"fuel_details": {
				"litres": 50,
				"price_per_litre": 22.50,
				"fuel_type": "Diesel",
				"odometer_reading": 125000,
			},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		# Verify extraction populated
		assert doc.slip_type == "Fuel"
		assert doc.merchant_name_ocr == "Shell N1"
		assert doc.litres == 50
		assert doc.fuel_type == "Diesel"

		# Verify matching applied fleet card config
		assert doc.fleet_vehicle == "VEH-001"
		assert doc.vehicle_match_status == "Auto Matched"
		assert doc.posting_mode == "Fleet Card"
		assert doc.fleet_card_supplier == "WesBank"
		assert doc.expense_account == "3100 - Fleet Control - TC"
		assert doc.cost_center == "Transport - TC"

	def test_pi_submit_completes_fleet_slip(self, mock_frappe):
		"""PI submit → fleet slip Completed."""
		mock_frappe.get_all.return_value = ["OCR-FS-00001"]

		pi_doc = SimpleNamespace(doctype="Purchase Invoice", name="PI-00001")
		update_ocr_fleet_on_submit(pi_doc, "on_submit")

		mock_frappe.db.set_value.assert_called_with("OCR Fleet Slip", "OCR-FS-00001", "status", "Completed")

	def test_pi_cancel_resets_fleet_slip(self, mock_frappe):
		"""PI cancel → fleet slip resets to Matched."""
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
		assert mock_fleet.status == "Matched"


# ---------------------------------------------------------------------------
# TestFuelSlipDirectExpenseWorkflow
# ---------------------------------------------------------------------------


class TestFuelSlipDirectExpenseWorkflow:
	"""Scenario B: Fuel slip + bank card vehicle → PI with default supplier."""

	def test_populate_then_match_direct_expense(self, mock_frappe):
		"""Full pipeline: populate → match → direct expense config with default supplier."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-002",
			registration="XYZ 789 GP",
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="Head Office - TC",
		)

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Fuel",
				"merchant_name": "Engen Garage",
				"transaction_date": "2025-12-15",
				"vehicle_registration": "XYZ 789 GP",
				"total_amount": 900.00,
				"vat_amount": 0,
				"currency": "ZAR",
				"confidence": 0.90,
				"description": "",
			},
			"fuel_details": {
				"litres": 40,
				"price_per_litre": 22.50,
				"fuel_type": "95 Unleaded",
				"odometer_reading": 45000,
			},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		# Verify direct expense config with default supplier
		assert doc.posting_mode == "Direct Expense"
		assert doc.fleet_card_supplier == "Default Supplier"
		assert doc.expense_account == "5000 - Fuel Expense - TC"
		assert doc.cost_center == "Head Office - TC"


# ---------------------------------------------------------------------------
# TestUnauthorizedPurchaseWorkflow
# ---------------------------------------------------------------------------


class TestUnauthorizedPurchaseWorkflow:
	"""Scenario C: Unauthorized purchase → No Action."""

	def test_other_slip_type_sets_unauthorized(self):
		"""Other slip type sets unauthorized flag."""
		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Other",
				"merchant_name": "Engen 1-Stop",
				"total_amount": 185.50,
				"confidence": 0.75,
				"description": "Doritos, Coca-Cola, Sandwich",
			},
			"fuel_details": {},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)

		assert doc.unauthorized_flag == 1
		assert doc.slip_type == "Other"
		assert doc.description == "Doritos, Coca-Cola, Sandwich"

	def test_no_action_preserves_status(self):
		"""No Action status is preserved by _update_status."""
		doc = OCRFleetSlip.__new__(OCRFleetSlip)
		doc.status = "No Action"
		doc.purchase_invoice = None
		doc.merchant_name_ocr = "Test"
		doc.total_amount = 100
		doc.slip_type = "Other"
		doc.fleet_vehicle = "VEH-001"
		doc.vehicle_registration = "ABC"
		doc.fleet_card_supplier = "WesBank"

		doc._update_status()
		assert doc.status == "No Action"


# ---------------------------------------------------------------------------
# TestTollSlipWorkflow
# ---------------------------------------------------------------------------


class TestTollSlipWorkflow:
	"""Scenario D: Toll slip → PI based on vehicle config."""

	def test_toll_with_fleet_card(self, mock_frappe):
		"""Toll slip + fleet card vehicle → fleet card posting mode."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-003",
			registration="DEF 456 GP",
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Fleet Control - TC",
			custom_cost_center="Transport - TC",
		)

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Toll",
				"merchant_name": "SANRAL",
				"transaction_date": "2025-12-16",
				"vehicle_registration": "DEF 456 GP",
				"total_amount": 95.00,
				"vat_amount": 14.13,
				"currency": "ZAR",
				"confidence": 0.88,
				"description": "",
			},
			"fuel_details": {},
			"toll_details": {
				"toll_plaza_name": "Huguenot Tunnel",
				"route": "N1",
			},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		assert doc.slip_type == "Toll"
		assert doc.toll_plaza_name == "Huguenot Tunnel"
		assert doc.route == "N1"
		assert doc.posting_mode == "Fleet Card"
		assert doc.tax_template == "SA VAT 15%"  # VAT detected

	def test_toll_with_direct_expense(self, mock_frappe):
		"""Toll slip + bank card vehicle → direct expense posting mode."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = _NS(
			name="VEH-004",
			registration="GHI 789 GP",
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Toll",
				"merchant_name": "SANRAL",
				"transaction_date": "2025-12-16",
				"vehicle_registration": "GHI 789 GP",
				"total_amount": 50.00,
				"vat_amount": 7.44,
				"currency": "ZAR",
				"confidence": 0.85,
				"description": "",
			},
			"fuel_details": {},
			"toll_details": {
				"toll_plaza_name": "Kranskop Toll",
				"route": "N2",
			},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		assert doc.posting_mode == "Direct Expense"
		assert doc.expense_account == "5000 - Fuel Expense - TC"


# ---------------------------------------------------------------------------
# TestUnmatchedVehicleWorkflow
# ---------------------------------------------------------------------------


class TestUnmatchedVehicleWorkflow:
	"""No vehicle match → manual config required."""

	def test_unmatched_vehicle_no_posting_mode(self, mock_frappe):
		"""No vehicle match means no posting mode auto-set."""
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = None  # No exact match
		mock_frappe.get_all.return_value = []  # No fuzzy match

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Fuel",
				"merchant_name": "Shell",
				"vehicle_registration": "UNKNOWN",
				"total_amount": 500,
				"confidence": 0.80,
			},
			"fuel_details": {"litres": 25},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		assert doc.vehicle_match_status == "Unmatched"
		assert doc.posting_mode == ""
		assert doc.fleet_vehicle == ""

	def test_no_fleet_management_installed(self, mock_frappe):
		"""Without fleet_management app, no vehicle matching occurs."""
		mock_frappe.db.exists.return_value = False  # No Fleet Vehicle DocType

		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Fuel",
				"merchant_name": "Shell",
				"vehicle_registration": "ABC 123",
				"total_amount": 500,
				"confidence": 0.80,
			},
			"fuel_details": {},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		_run_fleet_matching(doc, settings)

		assert doc.vehicle_match_status == "Unmatched"
		assert doc.fleet_vehicle == ""


# ---------------------------------------------------------------------------
# TestDriveScanDedup
# ---------------------------------------------------------------------------


class TestDriveScanDedup:
	"""Drive scan deduplication for fleet slip files."""

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	@patch("erpocr_integration.api.validate_file_magic_bytes")
	def test_skips_already_processed(self, mock_validate, mock_download, mock_frappe):
		"""Files already processed (non-Error) are skipped."""
		from erpocr_integration.tasks.drive_integration import _process_fleet_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-FS-00001", status="Matched", drive_retry_count=0)
		]

		settings = _make_settings()
		result = _process_fleet_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "fuel_slip.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is False
		mock_download.assert_not_called()

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	@patch("erpocr_integration.api.validate_file_magic_bytes")
	def test_retries_error_records(self, mock_validate, mock_download, mock_frappe):
		"""Error records under retry cap are retried."""
		from erpocr_integration.tasks.drive_integration import _process_fleet_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-FS-00001", status="Error", drive_retry_count=1)
		]
		mock_download.return_value = b"%PDF-1.4 test content"
		mock_validate.return_value = True

		mock_fleet = MagicMock()
		mock_fleet.name = "OCR-FS-00002"
		mock_frappe.get_doc.return_value = mock_fleet

		settings = _make_settings()
		result = _process_fleet_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "fuel_slip.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is True
		mock_frappe.delete_doc.assert_called_once()

	@patch("erpocr_integration.tasks.drive_integration._download_file")
	def test_gives_up_after_max_retries(self, mock_download, mock_frappe):
		"""Files exceeding MAX_DRIVE_RETRIES are not retried."""
		from erpocr_integration.tasks.drive_integration import _process_fleet_scan_file

		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="OCR-FS-00001", status="Error", drive_retry_count=3)
		]

		settings = _make_settings()
		result = _process_fleet_scan_file(
			service=MagicMock(),
			file_info={"id": "drive-123", "name": "fuel_slip.pdf", "mimeType": "application/pdf"},
			settings=settings,
		)

		assert result is False
		mock_download.assert_not_called()


# ---------------------------------------------------------------------------
# TestGeminiFleetExtraction
# ---------------------------------------------------------------------------


class TestGeminiFleetExtraction:
	"""Test Gemini extraction functions for fleet slips."""

	def test_fleet_schema_has_fuel_details(self):
		from erpocr_integration.tasks.gemini_extract import _build_fleet_extraction_schema

		schema = _build_fleet_extraction_schema()
		props = schema["properties"]

		assert "fuel_details" in props
		fuel_props = props["fuel_details"]["properties"]
		for field in ["litres", "price_per_litre", "fuel_type", "odometer_reading"]:
			assert field in fuel_props

	def test_fleet_schema_has_toll_details(self):
		from erpocr_integration.tasks.gemini_extract import _build_fleet_extraction_schema

		schema = _build_fleet_extraction_schema()
		props = schema["properties"]

		assert "toll_details" in props
		toll_props = props["toll_details"]["properties"]
		for field in ["toll_plaza_name", "route"]:
			assert field in toll_props

	def test_fleet_schema_has_slip_type(self):
		from erpocr_integration.tasks.gemini_extract import _build_fleet_extraction_schema

		schema = _build_fleet_extraction_schema()
		assert "slip_type" in schema["properties"]

	def test_fleet_schema_has_vehicle_registration(self):
		from erpocr_integration.tasks.gemini_extract import _build_fleet_extraction_schema

		schema = _build_fleet_extraction_schema()
		assert "vehicle_registration" in schema["properties"]

	def test_transform_fuel_slip(self):
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		raw = {
			"slip_type": "Fuel",
			"merchant_name": "Shell ( Pty ) Ltd",
			"transaction_date": "2025-12-15",
			"vehicle_registration": "abc 123 gp",
			"total_amount": 1125.00,
			"vat_amount": 0,
			"currency": "ZAR",
			"confidence": 0.92,
			"description": "",
			"fuel_details": {
				"litres": 50,
				"price_per_litre": 22.50,
				"fuel_type": "Diesel",
				"odometer_reading": 125000,
			},
			"toll_details": {},
		}
		result = _transform_to_fleet_format(raw, "fuel_slip.pdf")

		assert result["header_fields"]["slip_type"] == "Fuel"
		assert result["header_fields"]["vehicle_registration"] == "ABC 123 GP"  # uppercased
		assert result["fuel_details"]["litres"] == 50
		assert result["source_filename"] == "fuel_slip.pdf"

	def test_transform_toll_slip(self):
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		raw = {
			"slip_type": "Toll",
			"merchant_name": "SANRAL",
			"transaction_date": "2025-12-16",
			"vehicle_registration": "DEF 456 GP",
			"total_amount": 95.00,
			"vat_amount": 14.13,
			"currency": "ZAR",
			"confidence": 0.88,
			"description": "",
			"fuel_details": {},
			"toll_details": {
				"toll_plaza_name": "Huguenot Tunnel",
				"route": "N1",
			},
		}
		result = _transform_to_fleet_format(raw, "toll.pdf")

		assert result["header_fields"]["slip_type"] == "Toll"
		assert result["toll_details"]["toll_plaza_name"] == "Huguenot Tunnel"

	def test_transform_normalizes_slip_type(self):
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		# "petrol" → "Fuel"
		raw = {"slip_type": "petrol", "fuel_details": {}, "toll_details": {}}
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["slip_type"] == "Fuel"

		# "diesel" → "Fuel"
		raw["slip_type"] = "diesel"
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["slip_type"] == "Fuel"

		# "tolls" → "Toll"
		raw["slip_type"] = "tolls"
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["slip_type"] == "Toll"

		# "snacks" → "Other"
		raw["slip_type"] = "snacks"
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["slip_type"] == "Other"

	def test_transform_empty_slip_type(self):
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		raw = {"slip_type": "", "fuel_details": {}, "toll_details": {}}
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["slip_type"] == ""

	def test_transform_handles_missing_fuel_details(self):
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		raw = {"slip_type": "Fuel", "fuel_details": None, "toll_details": None}
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["fuel_details"]["litres"] == 0.0
		assert result["toll_details"]["toll_plaza_name"] == ""

	def test_transform_cleans_merchant_name(self):
		"""Merchant name cleanup: ( Pty ) → (Pty)."""
		from erpocr_integration.tasks.gemini_extract import _transform_to_fleet_format

		raw = {
			"slip_type": "Fuel",
			"merchant_name": "Shell ( Pty ) Ltd",
			"fuel_details": {},
			"toll_details": {},
		}
		result = _transform_to_fleet_format(raw, "test.pdf")
		assert result["header_fields"]["merchant_name"] == "Shell (Pty) Ltd"


# ---------------------------------------------------------------------------
# TestVATHandling
# ---------------------------------------------------------------------------


class TestVATHandling:
	"""Test VAT detection matches OCR Import behavior."""

	def test_diesel_no_vat(self):
		"""Diesel has no VAT — non-VAT template applied."""
		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Fuel",
				"total_amount": 1125.00,
				"vat_amount": 0,
				"confidence": 0.92,
			},
			"fuel_details": {"fuel_type": "Diesel"},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		assert doc.tax_template == "Non-VAT"

	def test_toll_with_vat(self):
		"""Toll with VAT amount → VAT template applied."""
		settings = _make_settings()
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {
				"slip_type": "Toll",
				"total_amount": 95.00,
				"vat_amount": 14.13,
				"confidence": 0.88,
			},
			"fuel_details": {},
			"toll_details": {"toll_plaza_name": "Test"},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		assert doc.tax_template == "SA VAT 15%"

	def test_no_vat_template_when_not_configured(self):
		"""No template set when settings don't have templates."""
		settings = _make_settings(default_tax_template="", non_vat_tax_template="")
		doc = MockFleetSlip()

		extracted = {
			"header_fields": {"vat_amount": 14.13, "confidence": 0.90},
			"fuel_details": {},
			"toll_details": {},
		}

		_populate_ocr_fleet(doc, extracted, settings)
		assert doc.tax_template == ""


# ---------------------------------------------------------------------------
# TestPostingModeFromVehicle
# ---------------------------------------------------------------------------


class TestPostingModeFromVehicle:
	"""Test that posting mode is correctly determined from vehicle config."""

	def test_fleet_card_provider_set(self):
		"""Vehicle with fleet_card_provider → Fleet Card mode."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - Control - TC",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.posting_mode == "Fleet Card"
		assert doc.fleet_card_supplier == "WesBank"

	def test_no_fleet_card_provider(self):
		"""Vehicle without fleet_card_provider → Direct Expense mode with default supplier."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider=None,
			custom_fleet_control_account=None,
			custom_cost_center=None,
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.posting_mode == "Direct Expense"
		assert doc.fleet_card_supplier == "Default Supplier"

	def test_control_account_from_vehicle(self):
		"""Fleet card mode uses vehicle's control account."""
		settings = _make_settings()
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="WesBank",
			custom_fleet_control_account="3100 - WesBank Control - TC",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.expense_account == "3100 - WesBank Control - TC"

	def test_direct_expense_uses_settings_accounts(self):
		"""Direct expense mode uses OCR Settings accounts and default supplier."""
		settings = _make_settings(
			fleet_expense_account="5100 - Vehicle Expense - TC",
			fleet_default_supplier="Cash Purchases",
		)
		doc = MockFleetSlip()
		vehicle = _NS(
			custom_fleet_card_provider="",
			custom_fleet_control_account="",
			custom_cost_center="",
		)
		_apply_vehicle_config(doc, vehicle, settings)

		assert doc.expense_account == "5100 - Vehicle Expense - TC"
		assert doc.fleet_card_supplier == "Cash Purchases"
