# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class OCRImport(Document):
	def before_save(self):
		self._update_status()

	def _update_status(self):
		"""Auto-update status based on match states."""
		# Don't change status if already completed or in error
		if self.status in ("Completed", "Error"):
			return

		# If PI already created, mark completed
		if self.purchase_invoice:
			self.status = "Completed"
			return

		# Check supplier match
		supplier_matched = self.supplier and self.supplier_match_status in ("Auto Matched", "Confirmed")

		# Check item matches
		all_items_matched = True
		for item in self.items:
			if item.match_status == "Unmatched" and not item.item_code:
				all_items_matched = False
				break

		if supplier_matched and all_items_matched:
			self.status = "Matched"
		elif self.status != "Pending":
			self.status = "Needs Review"

	def on_update(self):
		"""Save aliases when user confirms a supplier or item match."""
		if self.has_value_changed("supplier") and self.supplier and self.supplier_name_ocr:
			if self.supplier_match_status == "Confirmed":
				self._save_supplier_alias()

		for item in self.items:
			if item.item_code and item.description_ocr and item.match_status == "Confirmed":
				self._save_item_alias(item)

	def _save_supplier_alias(self):
		"""Save supplier alias for future auto-matching."""
		ocr_text = self.supplier_name_ocr.strip()
		if not ocr_text:
			return

		if not frappe.db.exists("OCR Supplier Alias", ocr_text):
			frappe.get_doc({
				"doctype": "OCR Supplier Alias",
				"ocr_text": ocr_text,
				"supplier": self.supplier,
				"source": "Auto",
			}).insert(ignore_permissions=True)

	def _save_item_alias(self, item):
		"""Save item alias for future auto-matching."""
		ocr_text = item.description_ocr.strip()
		if not ocr_text:
			return

		if not frappe.db.exists("OCR Item Alias", ocr_text):
			frappe.get_doc({
				"doctype": "OCR Item Alias",
				"ocr_text": ocr_text,
				"item_code": item.item_code,
				"source": "Auto",
			}).insert(ignore_permissions=True)

	@frappe.whitelist()
	def create_purchase_invoice(self):
		"""Create a Purchase Invoice draft from this OCR Import record."""
		if self.purchase_invoice:
			frappe.throw(_("Purchase Invoice {0} already created for this import.").format(self.purchase_invoice))

		if not self.supplier:
			frappe.throw(_("Please select a Supplier before creating a Purchase Invoice."))

		settings = frappe.get_cached_doc("OCR Settings")

		pi_items = []
		for item in self.items:
			pi_item = {
				"qty": item.qty or 1,
				"rate": item.rate or 0,
				"description": item.description_ocr or item.item_name or "OCR Imported Item",
			}

			if item.item_code:
				pi_item["item_code"] = item.item_code
			else:
				# No matched item — use description + expense account
				pi_item["item_name"] = item.item_name or item.description_ocr or "OCR Imported Item"
				if settings.default_expense_account:
					pi_item["expense_account"] = settings.default_expense_account

			if settings.default_warehouse:
				pi_item["warehouse"] = settings.default_warehouse
			if settings.default_cost_center:
				pi_item["cost_center"] = settings.default_cost_center

			pi_items.append(pi_item)

		if not pi_items:
			frappe.throw(_("No line items to create Purchase Invoice."))

		pi_dict = {
			"doctype": "Purchase Invoice",
			"supplier": self.supplier,
			"company": settings.default_company,
			"posting_date": self.invoice_date or frappe.utils.today(),
			"bill_no": self.invoice_number,
			"bill_date": self.invoice_date,
			"items": pi_items,
		}

		# Only set due_date if it's on or after the posting_date
		posting_date = pi_dict["posting_date"]
		if self.due_date and str(self.due_date) >= str(posting_date):
			pi_dict["due_date"] = self.due_date

		pi = frappe.get_doc(pi_dict)
		pi.flags.ignore_mandatory = True
		pi.insert(ignore_permissions=True)

		# Link PI back to this import
		self.purchase_invoice = pi.name
		self.status = "Completed"
		self.save()

		frappe.msgprint(
			_("Purchase Invoice {0} created as draft.").format(
				frappe.utils.get_link_to_form("Purchase Invoice", pi.name)
			),
			indicator="green",
		)

		return pi.name
