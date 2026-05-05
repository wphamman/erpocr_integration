# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""Background job: write (supplier, product_code) → item_code mapping into
ERPNext's standard `Item Supplier` child table on the Item master.

Triggered from OCRImport.on_update when a user confirms a match that has both
a resolved item_code and a Gemini-extracted product_code.

Why a background job (not sync in on_update):
  - Item.save() is heavy (full validate + on_update chain on Item)
  - For an N-line invoice we'd otherwise pay N x Item.save inside the user's save
  - Avoids potential re-entry into OCRImport.on_update via Item hooks

Why we use the originating user's permissions (not Administrator):
  This is master-data learning. Whether OCR is allowed to mutate Item master
  is a site-level policy decision the operator makes by granting Item write
  to the OCR Manager role (or not). When granted, learning happens; when not,
  the job logs and exits cleanly — matching still works without learning.
"""

import frappe


def learn_item_supplier(item_code: str, supplier: str, product_code: str, originating_user: str):
	"""Append `Item Supplier` row mapping (supplier, supplier_part_no=product_code)
	to the Item, idempotently and under the originating user's permissions.

	Args:
		item_code: ERPNext Item.name to add the mapping to
		supplier: ERPNext Supplier.name
		product_code: Supplier's own SKU as printed on the invoice
		originating_user: user (email) who confirmed the OCR row — drives the
			permission check so site policy is honoured
	"""
	# Normalize and short-circuit on missing inputs
	item_code = (item_code or "").strip()
	supplier = (supplier or "").strip()
	product_code = (product_code or "").strip()
	originating_user = (originating_user or "").strip()
	if not (item_code and supplier and product_code and originating_user):
		return

	# Verify each link target still exists (background job runs after commit;
	# upstream doc could have been deleted between enqueue and execution).
	if not frappe.db.exists("Item", item_code):
		return
	if not frappe.db.exists("Supplier", supplier):
		return

	# Run as the user who confirmed the match — their permissions decide whether
	# OCR is allowed to mutate Item master on this site.
	frappe.set_user(originating_user)

	if not frappe.has_permission("Item", "write", doc=item_code):
		frappe.log_error(
			title="OCR: Item Supplier learning skipped (no permission)",
			message=(
				f"User '{originating_user}' confirmed an OCR match resolving "
				f"supplier '{supplier}' + product_code '{product_code}' to Item "
				f"'{item_code}', but lacks write permission on Item. Learning "
				"skipped. Grant Item write to the user's role to enable "
				"automatic supplier-product mappings."
			),
		)
		return

	# DB existence re-check (belt-and-braces: enqueue dedup serializes most
	# concurrent confirms but doesn't fully replace this check — another app
	# or manual edit could have populated the row between dedup and execution).
	if frappe.db.exists(
		"Item Supplier",
		{
			"parenttype": "Item",
			"parent": item_code,
			"supplier": supplier,
			"supplier_part_no": product_code,
		},
	):
		return

	# Append + save under the originating user's permissions
	try:
		item = frappe.get_doc("Item", item_code)
		item.append(
			"supplier_items",
			{"supplier": supplier, "supplier_part_no": product_code},
		)
		item.save()
		frappe.db.commit()  # nosemgrep
	except Exception:
		# Don't crash the worker on Item validate quirks; log and move on.
		# Matching for THIS invoice already used the user's confirmed item_code,
		# so the only loss is the future-invoice optimisation for this supplier.
		frappe.log_error(title="OCR: Item Supplier learning failed")
