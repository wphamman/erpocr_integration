# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class OCRFleetSlip(Document):
	def before_save(self):
		self._update_status()

	def _update_status(self):
		"""Auto-update status based on current state."""
		if self.status in ("Completed", "Draft Created", "No Action", "Error"):
			return

		# If PI already created, mark as Draft Created
		if self.purchase_invoice:
			self.status = "Draft Created"
			return

		# Check readiness for document creation
		has_data = bool(self.merchant_name_ocr or self.total_amount or self.slip_type)
		vehicle_matched = bool(self.fleet_vehicle)
		has_supplier = bool(self.fleet_card_supplier)

		if has_data and vehicle_matched and has_supplier:
			self.status = "Matched"
		elif has_data:
			self.status = "Needs Review"

	def on_update(self):
		"""Save vehicle alias when user confirms a vehicle match."""
		# When user manually links a Fleet Vehicle, re-apply config
		if self.has_value_changed("fleet_vehicle") and self.fleet_vehicle:
			if self.vehicle_match_status == "Confirmed":
				self._apply_vehicle_config_from_link()

		# Q8 (v1.8.0): a Desk-side edit that brings a Fleet Card slip to
		# "Matched" (e.g. operator confirms the vehicle) is an auto-record
		# trigger — same gate as the pipeline path. Cheap pre-checks here so
		# routine saves don't fetch settings; attempt_auto_record re-validates
		# everything (opt-in setting, confidence, ADR-0003 guards). Recursion
		# is bounded: mark_recorded's own save re-enters with status
		# "Completed" and auto_recorded=1, which both fail the gate.
		if self.status == "Matched" and self.posting_mode == "Fleet Card" and not self.auto_recorded:
			from erpocr_integration.tasks.auto_record import attempt_auto_record

			attempt_auto_record(self, frappe.get_cached_doc("OCR Settings"))

	def _apply_vehicle_config_from_link(self):
		"""Re-apply posting config when a Fleet Vehicle is (re-)linked on this slip.

		Runs from on_update whenever fleet_vehicle changes on a Confirmed slip —
		including the shell upload's own insert and any later Desk re-link.

		Shell/API-sourced slips FAIL SAFE (P4): a provider-less vehicle leaves
		posting_mode/supplier/expense BLANK (→ the slip stays in Needs Review and
		the PI guard `posting_mode != "Direct Expense"` blocks any invoice) rather
		than silently flipping to Direct Expense. This keeps the recon-vs-invoice
		fork from depending on `custom_fleet_card_provider` being maintained, and
		stops a Desk re-link (or the upload's own on_update) from undoing the
		upload-time fail-safe. The operator can still set Direct Expense explicitly
		(posting_mode is an editable Select). Drive slips keep the old fallback.
		"""
		if not frappe.db.exists("DocType", "Fleet Vehicle"):
			return

		vehicle = frappe.db.get_value(
			"Fleet Vehicle",
			self.fleet_vehicle,
			[
				"name",
				"registration",
				"custom_fleet_card_provider",
				"custom_fleet_control_account",
				"custom_cost_center",
			],
			as_dict=True,
		)
		if not vehicle:
			return

		settings = frappe.get_cached_doc("OCR Settings")
		fail_safe = (self.source_type or "").startswith("Gemini Shell")

		if vehicle.custom_fleet_card_provider:
			self.posting_mode = "Fleet Card"
			self.fleet_card_supplier = vehicle.custom_fleet_card_provider
			# Q6 (v1.8.0): no expense_account on the Fleet Card path — no PI is
			# ever created from a Fleet Card slip (ADR-0003), so the control
			# account copied here since v1.2.0 flowed nowhere.
			self.expense_account = ""
		elif fail_safe:
			# Provider missing on a shell slip → fail safe to review, never invoice.
			self.posting_mode = ""
			self.fleet_card_supplier = ""
			self.expense_account = ""
		else:
			self.posting_mode = "Direct Expense"
			self.fleet_card_supplier = settings.get("fleet_default_supplier") or ""
			self.expense_account = settings.get("fleet_expense_account") or ""

		if vehicle.custom_cost_center:
			self.cost_center = vehicle.custom_cost_center

	@frappe.whitelist(methods=["POST"])
	def create_purchase_invoice(self):
		"""Create a Purchase Invoice draft for a Direct Expense slip.

		As of v1.2.0, this path is restricted to `posting_mode == "Direct Expense"`
		— slips paid with a personal/business debit or credit card, where Star Pops
		owes the merchant directly and the slip is the source document for the AP
		entry. Fleet Card slips (paid through a fleet card provider like Wesbank)
		MUST NOT take this path: their cost is booked from the provider's monthly
		invoice import in fleet_management, and creating a PI here would double-
		count. Fleet Card slips close as control records via `mark_recorded()`.
		"""
		# Explicit source-doc write guard — run_doc_method only checks read by default.
		if not frappe.has_permission("OCR Fleet Slip", "write", self.name):
			frappe.throw(_("You don't have permission to modify this OCR Fleet Slip."))
		if not frappe.has_permission("Purchase Invoice", "create"):
			frappe.throw(_("You don't have permission to create Purchase Invoices."))

		if self.status not in ("Matched", "Needs Review"):
			frappe.throw(
				_("Cannot create Purchase Invoice from a record with status '{0}'.").format(self.status)
			)

		if self.posting_mode != "Direct Expense":
			frappe.throw(
				_(
					"Cannot create a Purchase Invoice for a Fleet Card slip. "
					"The provider's monthly invoice books the cost; this slip is a control record. "
					"Use Mark Recorded to close it. "
					"If this slip was actually paid on a business card, flip Posting Mode to Direct Expense first."
				)
			)

		if self.document_type != "Purchase Invoice":
			frappe.throw(_("Document Type must be 'Purchase Invoice' to create a Purchase Invoice."))

		# Row-lock to prevent duplicate creation
		current = frappe.db.get_value(
			"OCR Fleet Slip",
			self.name,
			["purchase_invoice"],
			as_dict=True,
			for_update=True,
		)
		if current.purchase_invoice:
			frappe.throw(_("A document has already been created for this fleet slip."))

		if not self.fleet_vehicle:
			frappe.throw(_("No Fleet Vehicle linked. Match a vehicle before creating a Purchase Invoice."))

		supplier = self.fleet_card_supplier
		if not supplier:
			frappe.throw(
				_(
					"No supplier set. Configure a fleet card provider on the Fleet Vehicle, "
					"or set Fleet Default Supplier in OCR Settings."
				)
			)

		settings = frappe.get_cached_doc("OCR Settings")

		# Determine item from slip_type
		item_code = self._resolve_item(settings)
		if not item_code:
			frappe.throw(_("No item configured. Set Fleet Fuel Item or Fleet Toll Item in OCR Settings."))

		pi_item = {
			"item_code": item_code,
			"qty": 1,
			"rate": flt(self.total_amount),
			"description": self._build_description(),
		}

		if self.expense_account:
			pi_item["expense_account"] = self.expense_account

		if self.cost_center:
			pi_item["cost_center"] = self.cost_center
		elif settings.get("default_cost_center"):
			pi_item["cost_center"] = settings.default_cost_center

		pi_dict = {
			"doctype": "Purchase Invoice",
			"supplier": supplier,
			"company": self.company,
			"currency": self.currency or frappe.get_cached_value("Company", self.company, "default_currency"),
			"set_posting_time": 1,
			"posting_date": self.transaction_date or frappe.utils.today(),
			"items": [pi_item],
		}

		# Optional fleet_management integration: when that app is installed it
		# plants `custom_fleet_vehicle` on Purchase Invoice and uses it for
		# vehicle-level cost reports + cost-centre auto-fill. Populate it here
		# so OCR-generated fleet PIs land in those reports without users having
		# to remember to tag the vehicle. Runtime feature-detect only — no
		# import/dependency on fleet_management.
		if self.fleet_vehicle and frappe.get_meta("Purchase Invoice").has_field("custom_fleet_vehicle"):
			pi_dict["custom_fleet_vehicle"] = self.fleet_vehicle

		# Apply tax template via the shared invoice-side builder (v1.8.0, Q7a):
		# an operator picking an Actual-type import template now gets the
		# extracted VAT injected into the Actual row instead of a 0-tax draft —
		# same pure-Actual-template scoping as the invoice path (a mixed template
		# must not double-tax; that bug was caught on the invoice side pre-v1.5.0).
		# The adapter maps the slip's flat fields onto the OCR Import shape the
		# builder reads; subtotal=0 short-circuits the tax-inclusive detector
		# (a slip is a single amount — no per-line rates to reclassify), so
		# included_in_print_rate passes through unchanged, as before.
		if self.tax_template:
			from types import SimpleNamespace

			from erpocr_integration.erpnext_ocr.doctype.ocr_import.ocr_import import (
				_build_taxes_from_template,
			)

			tax_proxy = SimpleNamespace(
				tax_template=self.tax_template,
				company=self.company,
				tax_amount=flt(self.vat_amount),
				subtotal=0,
				total_amount=flt(self.total_amount),
				items=[],
			)
			tax_template, taxes = _build_taxes_from_template(tax_proxy)
			if tax_template:
				pi_dict["taxes_and_charges"] = tax_template
				pi_dict["taxes"] = taxes

		pi = frappe.get_doc(pi_dict)
		pi.flags.ignore_mandatory = True
		pi.insert()

		# Restore description (ERPNext overwrites from Item master)
		desc = self._build_description()
		if desc and pi.items and desc != pi.items[0].item_name:
			pi.items[0].db_set({"item_name": desc, "description": desc})

		# Copy scan attachment to PI
		self._copy_scan_to_document("Purchase Invoice", pi.name)

		# Link back
		self.purchase_invoice = pi.name
		self.status = "Draft Created"
		self.save()

		frappe.msgprint(
			_("Purchase Invoice {0} created as draft.").format(
				frappe.utils.get_link_to_form("Purchase Invoice", pi.name)
			),
			indicator="green",
		)

		return pi.name

	@frappe.whitelist(methods=["POST"])
	def unlink_document(self):
		"""Unlink and delete the draft PI, resetting for re-use."""
		if not frappe.has_permission("OCR Fleet Slip", "write", self.name):
			frappe.throw(_("You don't have permission to modify this record."))

		if self.status != "Draft Created":
			frappe.throw(_("Can only unlink documents when status is 'Draft Created'."))

		linked_doctype = None
		linked_name = None
		link_field = None

		if self.purchase_invoice:
			linked_doctype = "Purchase Invoice"
			linked_name = self.purchase_invoice
			link_field = "purchase_invoice"

		if not linked_name:
			frappe.throw(_("No linked document found to unlink."))

		docstatus = frappe.db.get_value(linked_doctype, linked_name, "docstatus")
		if docstatus == 1:
			frappe.throw(
				_("{0} {1} is submitted. Amend or cancel it first.").format(linked_doctype, linked_name)
			)

		# Require delete permission on the linked document itself — prevents a user
		# with only OCR Fleet Slip write (e.g. OCR Fleet Slip Reader) from deleting
		# a draft Purchase Invoice they otherwise couldn't touch.
		if docstatus is not None:
			frappe.has_permission(linked_doctype, "delete", linked_name, throw=True)

		# Clear link FIRST via db_set (Frappe blocks deletion of docs with incoming Link refs)
		self.db_set(link_field, "")
		self.db_set("document_type", "")
		self.db_set("status", "Pending")

		deleted = False
		if docstatus is not None:
			frappe.delete_doc(linked_doctype, linked_name, force=True)
			deleted = True

		self.reload()
		self.save()

		if deleted:
			frappe.msgprint(
				_("{0} {1} deleted. You can now create a different document.").format(
					linked_doctype, linked_name
				),
				indicator="blue",
			)
		else:
			frappe.msgprint(
				_("Link cleared. {0} {1} was already deleted.").format(linked_doctype, linked_name),
				indicator="blue",
			)

	@frappe.whitelist(methods=["POST"])
	def mark_recorded(self):
		"""Fleet Card terminal disposition — close the slip as a control record (no PI).

		The Wesbank-style monthly provider invoice books the actual cost; this slip
		captures the per-transaction control evidence (litres, odometer, vehicle,
		date) that fleet_management cross-checks against the provider invoice. So a
		fully-verified Fleet Card slip just needs to flip to Completed — there's no
		downstream document to create.

		Refuses on Direct Expense slips: those need a PI via create_purchase_invoice().
		"""
		if not frappe.has_permission("OCR Fleet Slip", "write", self.name):
			frappe.throw(_("You don't have permission to modify this record."))

		if self.posting_mode != "Fleet Card":
			frappe.throw(
				_(
					"Mark Recorded is only for Fleet Card slips (paid via a fleet card provider). "
					"Direct Expense slips need a Purchase Invoice — use Create > Purchase Invoice instead."
				)
			)

		if self.status not in ("Matched", "Needs Review"):
			frappe.throw(_("Cannot mark as Recorded from status '{0}'.").format(self.status))

		if not self.fleet_vehicle:
			frappe.throw(_("No Fleet Vehicle linked. Match a vehicle before marking as Recorded."))

		self.status = "Completed"
		self.save()

		# quiet_mark_recorded: auto-record (background) and the bulk list action
		# suppress the per-slip toast — one message per slip is noise there.
		if not self.flags.get("quiet_mark_recorded"):
			frappe.msgprint(
				_(
					"Marked as Recorded. The cost is booked from the fleet card provider's "
					"monthly invoice; this slip is the control record."
				),
				indicator="green",
			)

	@frappe.whitelist(methods=["POST"])
	def mark_no_action(self, reason):
		"""Mark this fleet slip as No Action Required."""
		if not frappe.has_permission("OCR Fleet Slip", "write", self.name):
			frappe.throw(_("You don't have permission to modify this record."))

		if self.status in ("Completed", "Draft Created"):
			frappe.throw(_("Cannot mark as No Action when status is '{0}'.").format(self.status))

		reason = (reason or "").strip()
		if not reason:
			frappe.throw(_("Please provide a reason for marking as No Action."))

		self.status = "No Action"
		self.no_action_reason = reason
		self.save()

		frappe.msgprint(
			_("Marked as No Action: {0}").format(reason),
			indicator="blue",
		)

	def _resolve_item(self, settings):
		"""Get the appropriate item code based on slip_type."""
		if self.slip_type == "Fuel":
			return settings.get("fleet_fuel_item") or settings.get("default_item")
		elif self.slip_type == "Toll":
			return (
				settings.get("fleet_toll_item")
				or settings.get("fleet_fuel_item")
				or settings.get("default_item")
			)
		return settings.get("fleet_fuel_item") or settings.get("default_item")

	def _build_description(self):
		"""Build a human-readable description for the created document line item."""
		parts = []
		if self.slip_type:
			parts.append(self.slip_type)
		if self.merchant_name_ocr:
			parts.append(self.merchant_name_ocr)

		if self.slip_type == "Fuel":
			fuel_parts = []
			if self.litres:
				fuel_parts.append(f"{flt(self.litres, 2)}L")
			if self.fuel_type:
				fuel_parts.append(self.fuel_type)
			if self.price_per_litre:
				fuel_parts.append(f"@ {flt(self.price_per_litre, 2)}/L")
			if fuel_parts:
				parts.append(" ".join(fuel_parts))
		elif self.slip_type == "Toll" and self.toll_plaza_name:
			parts.append(self.toll_plaza_name)

		if self.vehicle_registration:
			parts.append(f"[{self.vehicle_registration}]")

		return " — ".join(parts) if parts else "Fleet Slip"

	def _copy_scan_to_document(self, doctype, docname):
		"""Copy the scan attachment from this fleet slip to the created document."""
		try:
			files = frappe.get_all(
				"File",
				filters={
					"attached_to_doctype": "OCR Fleet Slip",
					"attached_to_name": self.name,
					"is_private": 1,
				},
				fields=["name", "file_url", "file_name"],
				limit=1,
			)
			if files:
				frappe.get_doc(
					{
						"doctype": "File",
						"file_url": files[0].file_url,
						"file_name": files[0].file_name,
						"attached_to_doctype": doctype,
						"attached_to_name": docname,
						"is_private": 1,
					}
				).insert(ignore_permissions=True)

			# Add Drive link as comment
			if self.drive_link and self.drive_link.startswith("https://"):
				from frappe.utils import escape_html

				safe_link = escape_html(self.drive_link)
				safe_path = escape_html(self.drive_folder_path or "N/A")
				doc = frappe.get_doc(doctype, docname)
				doc.add_comment(
					"Comment",
					f"<b>Original Fleet Slip Scan:</b> "
					f"<a href='{safe_link}' target='_blank' rel='noopener noreferrer'>View in Google Drive</a><br>"
					f"<small>Archive path: {safe_path}</small>",
				)
		except Exception:
			# Non-critical — don't fail document creation
			frappe.log_error("Failed to copy scan attachment to fleet slip document")
