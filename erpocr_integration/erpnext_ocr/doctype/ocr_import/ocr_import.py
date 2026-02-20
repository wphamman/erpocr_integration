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

		# If PI or PR already created, mark completed
		if self.purchase_invoice or self.purchase_receipt:
			self.status = "Completed"
			return

		# Check supplier match
		supplier_matched = self.supplier and self.supplier_match_status in ("Auto Matched", "Confirmed")

		# Check item matches
		all_items_matched = True
		all_items_ready = True  # Ready includes having expense_account for service items

		for item in self.items:
			# Check if item is matched
			if item.match_status == "Unmatched" and not item.item_code:
				all_items_matched = False
				all_items_ready = False
				break

			# For matched items, check if service items have expense_account
			# (Items without expense_account are assumed to be stock items that get GL from item master)
			if item.item_code and not item.expense_account:
				# Check if this is a non-stock item that requires expense_account
				is_stock = frappe.db.get_value("Item", item.item_code, "is_stock_item")
				if not is_stock:
					# Non-stock item without expense_account → needs review
					all_items_ready = False

		if supplier_matched and all_items_matched and all_items_ready and self.items:
			self.status = "Matched"
		elif self.supplier_name_ocr or self.items:
			# Data was extracted but not fully matched/ready — needs user review
			self.status = "Needs Review"

	def on_update(self):
		"""Save aliases only when user explicitly confirms matches (status = Confirmed)."""
		if self.has_value_changed("supplier") and self.supplier and self.supplier_name_ocr:
			if self.supplier_match_status == "Confirmed":
				self._save_supplier_alias()

		for item in self.items:
			if item.item_code and item.description_ocr and item.match_status == "Confirmed":
				self._save_item_alias(item)

				# Only save service mapping alongside explicit item confirmation
				if item.expense_account:
					self._save_service_mapping(item)

	def _save_supplier_alias(self):
		"""Save supplier alias for future auto-matching."""
		ocr_text = self.supplier_name_ocr.strip()
		if not ocr_text:
			return

		if not frappe.db.exists("OCR Supplier Alias", ocr_text):
			frappe.get_doc(
				{
					"doctype": "OCR Supplier Alias",
					"ocr_text": ocr_text,
					"supplier": self.supplier,
					"source": "Auto",
				}
			).insert(ignore_permissions=True)

	def _save_item_alias(self, item):
		"""Save item alias for future auto-matching."""
		ocr_text = item.description_ocr.strip()
		if not ocr_text:
			return

		if not frappe.db.exists("OCR Item Alias", ocr_text):
			frappe.get_doc(
				{
					"doctype": "OCR Item Alias",
					"ocr_text": ocr_text,
					"item_code": item.item_code,
					"source": "Auto",
				}
			).insert(ignore_permissions=True)

	def _save_service_mapping(self, item):
		"""
		Save service mapping for future auto-matching.

		When user manually selects:
		- Item code (e.g., ITEM001)
		- Expense account (e.g., 5200 - Subscription Expenses)
		- Cost center (optional)
		- Supplier (optional, for supplier-specific mappings)

		Create a mapping so future invoices with similar descriptions auto-fill these fields.
		"""
		description = item.description_ocr.strip()
		if not description or not item.item_code or not item.expense_account:
			return

		# Extract a pattern from the description (first word or first few words)
		# Convert to lowercase for case-insensitive matching
		pattern = description.lower()

		company = self.get("company") or frappe.defaults.get_user_default("Company")
		supplier = self.supplier  # Link to supplier for supplier-specific mappings

		# Check if a mapping already exists for this pattern + company + supplier
		existing = frappe.db.get_value(
			"OCR Service Mapping",
			{
				"description_pattern": pattern,
				"company": company,
				"supplier": supplier or "",  # Empty string for NULL check
			},
			"name",
		)

		if existing:
			# Update existing mapping
			doc = frappe.get_doc("OCR Service Mapping", existing)
			doc.item_code = item.item_code
			doc.item_name = item.item_name
			doc.expense_account = item.expense_account
			doc.cost_center = item.cost_center
			doc.supplier = supplier
			doc.source = "Auto"
			doc.save(ignore_permissions=True)
		else:
			# Create new mapping
			frappe.get_doc(
				{
					"doctype": "OCR Service Mapping",
					"description_pattern": pattern,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"expense_account": item.expense_account,
					"cost_center": item.cost_center,
					"company": company,
					"supplier": supplier,
					"source": "Auto",
				}
			).insert(ignore_permissions=True)

	@frappe.whitelist()
	def create_purchase_invoice(self):
		"""Create a Purchase Invoice draft from this OCR Import record."""
		# Verify the calling user has PI create permission (background jobs use Administrator)
		if not frappe.has_permission("Purchase Invoice", "create"):
			frappe.throw(_("You don't have permission to create Purchase Invoices."))

		# Row-lock to prevent duplicate creation from concurrent calls (manual + auto)
		current = frappe.db.get_value(
			"OCR Import", self.name, ["purchase_invoice", "purchase_receipt"],
			as_dict=True, for_update=True,
		)
		if current.purchase_invoice:
			frappe.throw(
				_("Purchase Invoice {0} already created for this import.").format(current.purchase_invoice)
			)
		if current.purchase_receipt:
			frappe.throw(
				_("Purchase Receipt {0} already exists for this import. Cannot also create a Purchase Invoice.").format(
					current.purchase_receipt
				)
			)

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
			elif settings.default_item:
				# Use configured default item, keep OCR description
				pi_item["item_code"] = settings.default_item
			else:
				# No matched item and no default — use description only
				pi_item["item_name"] = item.item_name or item.description_ocr or "OCR Imported Item"

			# Row-level accounting fields (from service mapping) take precedence over defaults
			if item.expense_account:
				pi_item["expense_account"] = item.expense_account
			elif settings.default_expense_account and not item.item_code:
				# Only use default expense account if no item_code (items have their own defaults)
				pi_item["expense_account"] = settings.default_expense_account

			if item.cost_center:
				pi_item["cost_center"] = item.cost_center
			elif settings.default_cost_center:
				pi_item["cost_center"] = settings.default_cost_center

			if settings.default_warehouse:
				pi_item["warehouse"] = settings.default_warehouse

			pi_items.append(pi_item)

		if not pi_items:
			frappe.throw(_("No line items to create Purchase Invoice."))

		pi_dict = {
			"doctype": "Purchase Invoice",
			"supplier": self.supplier,
			"company": self.company,
			"currency": self.currency or frappe.get_cached_value("Company", self.company, "default_currency"),
			"posting_date": self.invoice_date or frappe.utils.today(),
			"bill_no": self.invoice_number,
			"bill_date": self.invoice_date,
			"items": pi_items,
		}

		# Only set due_date if it's on or after the posting_date
		posting_date = pi_dict["posting_date"]
		if self.due_date and str(self.due_date) >= str(posting_date):
			pi_dict["due_date"] = self.due_date

		# Apply tax template from OCR Import (user-editable, auto-set during extraction)
		if self.tax_template:
			template = frappe.get_cached_doc("Purchase Taxes and Charges Template", self.tax_template)
			# Validate template belongs to the same company
			if template.company and template.company != self.company:
				frappe.throw(
					_("Tax Template '{0}' belongs to company '{1}', not '{2}'").format(
						self.tax_template, template.company, self.company
					)
				)
			pi_dict["taxes_and_charges"] = self.tax_template
			pi_dict["taxes"] = []
			for tax_row in template.taxes:
				pi_dict["taxes"].append(
					{
						"category": tax_row.category,
						"add_deduct_tax": tax_row.add_deduct_tax,
						"charge_type": tax_row.charge_type,
						"row_id": tax_row.row_id,
						"account_head": tax_row.account_head,
						"description": tax_row.description,
						"rate": tax_row.rate,
						"cost_center": tax_row.cost_center,
						"account_currency": tax_row.account_currency,
						"included_in_print_rate": tax_row.included_in_print_rate,
						"included_in_paid_amount": tax_row.included_in_paid_amount,
					}
				)

		pi = frappe.get_doc(pi_dict)
		# ignore_mandatory needed because OCR data may be incomplete (creating a draft for review)
		pi.flags.ignore_mandatory = True
		pi.insert()

		# Add comment with original invoice link (if available from Drive)
		if self.drive_link and self.drive_link.startswith("https://"):
			from frappe.utils import escape_html

			safe_link = escape_html(self.drive_link)
			safe_path = escape_html(self.drive_folder_path or "N/A")
			pi.add_comment(
				"Comment",
				f"<b>Original Invoice PDF:</b> <a href='{safe_link}' target='_blank' rel='noopener noreferrer'>View in Google Drive</a><br>"
				f"<small>Archive path: {safe_path}</small>",
			)

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

	@frappe.whitelist()
	def create_purchase_receipt(self):
		"""Create a Purchase Receipt draft from this OCR Import record."""
		if not frappe.has_permission("Purchase Receipt", "create"):
			frappe.throw(_("You don't have permission to create Purchase Receipts."))

		# Row-lock to prevent duplicate creation from concurrent calls (manual + auto)
		current = frappe.db.get_value(
			"OCR Import", self.name, ["purchase_invoice", "purchase_receipt"],
			as_dict=True, for_update=True,
		)
		if current.purchase_receipt:
			frappe.throw(
				_("Purchase Receipt {0} already created for this import.").format(current.purchase_receipt)
			)
		if current.purchase_invoice:
			frappe.throw(
				_("Purchase Invoice {0} already exists for this import. Cannot also create a Purchase Receipt.").format(
					current.purchase_invoice
				)
			)

		if not self.supplier:
			frappe.throw(_("Please select a Supplier before creating a Purchase Receipt."))

		settings = frappe.get_cached_doc("OCR Settings")

		pr_items = []
		non_stock_warnings = []
		skipped_unmatched = 0
		for item in self.items:
			if not item.item_code:
				# Skip unmatched rows — PRs require actual items
				skipped_unmatched += 1
				continue

			pr_item = {
				"item_code": item.item_code,
				"qty": item.qty or 1,
				"rate": item.rate or 0,
				"description": item.description_ocr or item.item_name or "OCR Imported Item",
			}

			# Warn if non-stock item is on a PR
			is_stock = frappe.db.get_value("Item", item.item_code, "is_stock_item")
			if not is_stock:
				non_stock_warnings.append(item.item_code)

			if item.cost_center:
				pr_item["cost_center"] = item.cost_center
			elif settings.default_cost_center:
				pr_item["cost_center"] = settings.default_cost_center

			if settings.default_warehouse:
				pr_item["warehouse"] = settings.default_warehouse

			pr_items.append(pr_item)

		if not pr_items:
			frappe.throw(
				_("No matched items to create Purchase Receipt. "
				  "Match items first, or change Document Type to Purchase Invoice.")
			)

		pr_dict = {
			"doctype": "Purchase Receipt",
			"supplier": self.supplier,
			"company": self.company,
			"currency": self.currency or frappe.get_cached_value("Company", self.company, "default_currency"),
			"posting_date": self.invoice_date or frappe.utils.today(),
			"items": pr_items,
		}

		# Apply tax template from OCR Import
		if self.tax_template:
			template = frappe.get_cached_doc("Purchase Taxes and Charges Template", self.tax_template)
			if template.company and template.company != self.company:
				frappe.throw(
					_("Tax Template '{0}' belongs to company '{1}', not '{2}'").format(
						self.tax_template, template.company, self.company
					)
				)
			pr_dict["taxes_and_charges"] = self.tax_template
			pr_dict["taxes"] = []
			for tax_row in template.taxes:
				pr_dict["taxes"].append(
					{
						"category": tax_row.category,
						"add_deduct_tax": tax_row.add_deduct_tax,
						"charge_type": tax_row.charge_type,
						"row_id": tax_row.row_id,
						"account_head": tax_row.account_head,
						"description": tax_row.description,
						"rate": tax_row.rate,
						"cost_center": tax_row.cost_center,
						"account_currency": tax_row.account_currency,
						"included_in_print_rate": tax_row.included_in_print_rate,
						"included_in_paid_amount": tax_row.included_in_paid_amount,
					}
				)

		pr = frappe.get_doc(pr_dict)
		pr.flags.ignore_mandatory = True
		pr.insert()

		# Add comment with original invoice link (if available from Drive)
		if self.drive_link and self.drive_link.startswith("https://"):
			from frappe.utils import escape_html

			safe_link = escape_html(self.drive_link)
			safe_path = escape_html(self.drive_folder_path or "N/A")
			pr.add_comment(
				"Comment",
				f"<b>Original Invoice PDF:</b> <a href='{safe_link}' target='_blank' rel='noopener noreferrer'>View in Google Drive</a><br>"
				f"<small>Archive path: {safe_path}</small>",
			)

		# Link PR back to this import
		self.purchase_receipt = pr.name
		self.status = "Completed"
		self.save()

		msg = _("Purchase Receipt {0} created as draft.").format(
			frappe.utils.get_link_to_form("Purchase Receipt", pr.name)
		)
		warnings = []
		if skipped_unmatched:
			warnings.append(_("{0} unmatched row(s) skipped").format(skipped_unmatched))
		if non_stock_warnings:
			warnings.append(_("Non-stock items included: {0}").format(", ".join(non_stock_warnings)))
		if warnings:
			msg += "<br><br>" + _("Warning: {0}. Review the draft carefully.").format("; ".join(warnings))
		frappe.msgprint(msg, indicator="green" if not warnings else "orange")

		return pr.name
