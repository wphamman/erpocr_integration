# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


def _resolve_rate(item_code, purchase_order_item=None):
	"""Get rate for PR item: PO rate > last_purchase_rate > standard_rate > 0."""
	if purchase_order_item:
		po_rate = frappe.db.get_value("Purchase Order Item", purchase_order_item, "rate")
		if po_rate:
			return flt(po_rate)
	if item_code:
		item = frappe.db.get_value("Item", item_code, ["last_purchase_rate", "standard_rate"], as_dict=True)
		if item:
			return flt(item.last_purchase_rate) or flt(item.standard_rate)
	return 0


class OCRDeliveryNote(Document):
	def before_save(self):
		self._update_status()

	def _update_status(self):
		"""Auto-update status based on match states."""
		if self.status in ("Completed", "Draft Created", "No Action", "Error"):
			return

		# If PO or PR already created, mark as Draft Created
		if self.purchase_order_result or self.purchase_receipt:
			self.status = "Draft Created"
			return

		# Check supplier match
		supplier_matched = bool(self.supplier)

		# Check item matches
		all_items_matched = all(item.item_code or item.match_status != "Unmatched" for item in self.items)

		if supplier_matched and all_items_matched and self.items:
			self.status = "Matched"
		elif self.supplier_name_ocr or self.items:
			self.status = "Needs Review"

	def on_update(self):
		"""Save supplier alias when user explicitly confirms match."""
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

	# Stale field clearing: when supplier changes, clear PO and item-level refs
	# (handled client-side in ocr_delivery_note.js — same pattern as OCR Import)

	@frappe.whitelist()
	def create_purchase_order(self):
		"""Create a draft Purchase Order from this OCR Delivery Note."""
		if not frappe.has_permission("Purchase Order", "create"):
			frappe.throw(_("You don't have permission to create Purchase Orders."))

		if self.status not in ("Matched", "Needs Review"):
			frappe.throw(
				_("Cannot create Purchase Order from a record with status '{0}'.").format(self.status)
			)

		if self.document_type != "Purchase Order":
			frappe.throw(_("Document Type must be 'Purchase Order' to create a Purchase Order."))

		# Row-lock to prevent duplicate creation
		current = frappe.db.get_value(
			"OCR Delivery Note",
			self.name,
			["purchase_order_result", "purchase_receipt"],
			as_dict=True,
			for_update=True,
		)
		if current.purchase_order_result or current.purchase_receipt:
			frappe.throw(_("A document has already been created for this delivery note."))

		if not self.supplier:
			frappe.throw(_("Please select a Supplier before creating a Purchase Order."))

		settings = frappe.get_cached_doc("OCR Settings")

		po_items = []
		skipped_unmatched = 0
		for item in self.items:
			if not item.item_code:
				skipped_unmatched += 1
				continue

			po_item = {
				"item_code": item.item_code,
				"qty": item.qty or 1,
				"rate": 0,  # Accounts team fills in rates
				"description": item.description_ocr or item.item_name or "OCR Scanned Item",
			}

			warehouse = settings.get("dn_default_warehouse") or settings.get("default_warehouse")
			if warehouse:
				po_item["warehouse"] = warehouse

			po_items.append(po_item)

		if not po_items:
			frappe.throw(_("No matched items to create Purchase Order. Match items first."))

		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": self.supplier,
				"company": self.company,
				"set_posting_time": 1,
				"transaction_date": self.delivery_date or frappe.utils.today(),
				"items": po_items,
			}
		)
		po.flags.ignore_mandatory = True
		po.insert()

		# Restore OCR descriptions
		matched_items = [item for item in self.items if item.item_code]
		for po_item, ocr_item in zip(po.items, matched_items, strict=False):
			ocr_desc = ocr_item.description_ocr or ocr_item.item_name
			if ocr_desc and ocr_desc != po_item.item_name:
				po_item.db_set({"item_name": ocr_desc, "description": ocr_desc})

		# Copy scan attachment to PO
		self._copy_scan_to_document("Purchase Order", po.name)

		# Link PO back to this delivery note
		self.purchase_order_result = po.name
		self.status = "Draft Created"
		self.save()

		msg = _("Purchase Order {0} created as draft (rates need to be filled in).").format(
			frappe.utils.get_link_to_form("Purchase Order", po.name)
		)
		if skipped_unmatched:
			msg += "<br>" + _("{0} unmatched row(s) skipped.").format(skipped_unmatched)
		frappe.msgprint(msg, indicator="green" if not skipped_unmatched else "orange")

		return po.name

	@frappe.whitelist()
	def create_purchase_receipt(self):
		"""Create a Purchase Receipt draft from this OCR Delivery Note."""
		if not frappe.has_permission("Purchase Receipt", "create"):
			frappe.throw(_("You don't have permission to create Purchase Receipts."))

		if self.status not in ("Matched", "Needs Review"):
			frappe.throw(
				_("Cannot create Purchase Receipt from a record with status '{0}'.").format(self.status)
			)

		if self.document_type != "Purchase Receipt":
			frappe.throw(_("Document Type must be 'Purchase Receipt' to create a Purchase Receipt."))

		# Row-lock to prevent duplicate creation
		current = frappe.db.get_value(
			"OCR Delivery Note",
			self.name,
			["purchase_order_result", "purchase_receipt"],
			as_dict=True,
			for_update=True,
		)
		if current.purchase_order_result or current.purchase_receipt:
			frappe.throw(_("A document has already been created for this delivery note."))

		if not self.supplier:
			frappe.throw(_("Please select a Supplier before creating a Purchase Receipt."))

		settings = frappe.get_cached_doc("OCR Settings")

		pr_items = []
		skipped_unmatched = 0
		non_stock_warnings = []
		for item in self.items:
			if not item.item_code:
				skipped_unmatched += 1
				continue

			rate = _resolve_rate(item.item_code, item.purchase_order_item)

			pr_item = {
				"item_code": item.item_code,
				"qty": item.qty or 1,
				"rate": rate,
				"description": item.description_ocr or item.item_name or "OCR Scanned Item",
			}

			is_stock = frappe.db.get_value("Item", item.item_code, "is_stock_item")
			if not is_stock:
				non_stock_warnings.append(item.item_code)

			warehouse = settings.get("dn_default_warehouse") or settings.get("default_warehouse")
			if warehouse:
				pr_item["warehouse"] = warehouse

			# PO refs
			if self.purchase_order and item.purchase_order_item:
				pr_item["purchase_order"] = self.purchase_order
				pr_item["purchase_order_item"] = item.purchase_order_item

			pr_items.append(pr_item)

		if not pr_items:
			frappe.throw(_("No matched items to create Purchase Receipt. Match items first."))

		pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": self.supplier,
				"company": self.company,
				"set_posting_time": 1,
				"posting_date": self.delivery_date or frappe.utils.today(),
				"items": pr_items,
			}
		)
		pr.flags.ignore_mandatory = True
		pr.insert()

		# Restore OCR descriptions
		matched_items = [item for item in self.items if item.item_code]
		for pr_item, ocr_item in zip(pr.items, matched_items, strict=False):
			ocr_desc = ocr_item.description_ocr or ocr_item.item_name
			if ocr_desc and ocr_desc != pr_item.item_name:
				pr_item.db_set({"item_name": ocr_desc, "description": ocr_desc})

		# Copy scan attachment to PR
		self._copy_scan_to_document("Purchase Receipt", pr.name)

		# Link PR back to this delivery note
		self.purchase_receipt = pr.name
		self.status = "Draft Created"
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

	def _copy_scan_to_document(self, doctype, docname):
		"""Copy the original scan file attachment to the created document."""
		attachments = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "OCR Delivery Note",
				"attached_to_name": self.name,
				"is_private": 1,
			},
			fields=["name", "file_url", "file_name"],
			limit=1,
		)
		if attachments:
			att = attachments[0]
			try:
				frappe.get_doc(
					{
						"doctype": "File",
						"file_url": att.file_url,
						"file_name": att.file_name,
						"attached_to_doctype": doctype,
						"attached_to_name": docname,
						"is_private": 1,
					}
				).insert(ignore_permissions=True)
			except Exception:
				# Non-critical — don't fail document creation over attachment copy
				frappe.log_error(title=f"OCR DN: Failed to copy attachment to {doctype} {docname}")

		# Also add Drive link as comment if available
		if self.drive_link and self.drive_link.startswith("https://"):
			from frappe.utils import escape_html

			safe_link = escape_html(self.drive_link)
			safe_path = escape_html(self.drive_folder_path or "N/A")
			doc = frappe.get_doc(doctype, docname)
			doc.add_comment(
				"Comment",
				f"<b>Original Delivery Note Scan:</b> <a href='{safe_link}' target='_blank' "
				f"rel='noopener noreferrer'>View in Google Drive</a><br>"
				f"<small>Archive path: {safe_path}</small>",
			)

	@frappe.whitelist()
	def unlink_document(self):
		"""Unlink and delete the draft PO/PR, resetting this OCR DN for re-use."""
		if not frappe.has_permission("OCR Delivery Note", "write", self.name):
			frappe.throw(_("You don't have permission to modify this record."))

		if self.status != "Draft Created":
			frappe.throw(_("Can only unlink documents when status is 'Draft Created'."))

		linked_doctype = None
		linked_name = None
		link_field = None

		if self.purchase_order_result:
			linked_doctype = "Purchase Order"
			linked_name = self.purchase_order_result
			link_field = "purchase_order_result"
		elif self.purchase_receipt:
			linked_doctype = "Purchase Receipt"
			linked_name = self.purchase_receipt
			link_field = "purchase_receipt"

		if not linked_name:
			frappe.throw(_("No linked document found to unlink."))

		docstatus = frappe.db.get_value(linked_doctype, linked_name, "docstatus")
		if docstatus == 1:
			frappe.throw(
				_("{0} {1} is submitted. Amend or cancel it first.").format(linked_doctype, linked_name)
			)

		# Clear link FIRST via db_set, then delete
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

	@frappe.whitelist()
	def mark_no_action(self, reason):
		"""Mark this OCR Delivery Note as No Action Required with a reason."""
		if not frappe.has_permission("OCR Delivery Note", "write", self.name):
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
