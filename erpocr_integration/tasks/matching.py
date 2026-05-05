# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import re
from difflib import SequenceMatcher

import frappe

# Punctuation that should be collapsed to a single space for matching.
# Keeps letters, digits, and whitespace; strips hyphens, slashes, parens, etc.
_MATCH_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_for_matching(text: str) -> str:
	"""Normalize text for substring matching.

	Lowercases, strips punctuation (hyphens, slashes, parens, etc.),
	and collapses whitespace so that 'Pro-Plan' and 'pro plan' both
	become 'pro plan'.
	"""
	text = text.lower().strip()
	text = _MATCH_PUNCT.sub(" ", text)
	return re.sub(r"\s+", " ", text).strip()


def match_supplier(ocr_text: str) -> tuple[str | None, str]:
	"""
	Attempt to match an OCR-extracted supplier name to an ERPNext Supplier.

	Matching priority:
	1. Exact match in OCR Supplier Alias table (learned from previous confirmations)
	2. Exact match against Supplier.supplier_name

	Returns:
		tuple: (supplier_name or None, match_status)
	"""
	if not ocr_text:
		return None, "Unmatched"

	ocr_text_stripped = ocr_text.strip()

	# 1. Check alias table (exact match)
	alias = frappe.db.get_value(
		"OCR Supplier Alias",
		{"ocr_text": ocr_text_stripped},
		"supplier",
	)
	if alias:
		return alias, "Auto Matched"

	# 2. Check Supplier master (exact name match)
	supplier = frappe.db.get_value(
		"Supplier",
		{"supplier_name": ocr_text_stripped},
		"name",
	)
	if supplier:
		return supplier, "Auto Matched"

	# 3. Also try matching against the Supplier document name directly
	if frappe.db.exists("Supplier", ocr_text_stripped):
		return ocr_text_stripped, "Auto Matched"

	return None, "Unmatched"


def match_item(ocr_text: str) -> tuple[str | None, str]:
	"""
	Attempt to match an OCR-extracted item description to an ERPNext Item.

	Matching priority:
	1. Exact match in OCR Item Alias table (learned from previous confirmations)
	2. Exact match against Item.item_name
	3. Exact match against Item.name (item_code)

	Returns:
		tuple: (item_code or None, match_status)
	"""
	if not ocr_text:
		return None, "Unmatched"

	ocr_text_stripped = ocr_text.strip()

	# 1. Check alias table (exact match)
	alias = frappe.db.get_value(
		"OCR Item Alias",
		{"ocr_text": ocr_text_stripped},
		"item_code",
	)
	if alias:
		return alias, "Auto Matched"

	# 2. Check Item master (exact item_name match)
	item = frappe.db.get_value(
		"Item",
		{"item_name": ocr_text_stripped},
		"name",
	)
	if item:
		return item, "Auto Matched"

	# 3. Also try matching against item_code directly
	if frappe.db.exists("Item", ocr_text_stripped):
		return ocr_text_stripped, "Auto Matched"

	return None, "Unmatched"


def match_item_by_supplier_part(supplier: str, product_code: str) -> tuple[str | None, str]:
	"""
	Match using ERPNext's standard `Item Supplier` child table:
	(supplier, supplier_part_no=product_code) → item_code.

	This is the highest-precision matching tier — supplier-scoped, deterministic,
	and uses the exact mapping ERPNext is designed to capture.

	Multi-hit policy: if the same (supplier, supplier_part_no) appears against
	more than one parent Item, the data is ambiguous (ERPNext does not enforce
	global uniqueness on this pair). We skip the match entirely and log so the
	site can clean up the duplicates — better than silently picking one and
	giving the UI a false sense of direction.

	Args:
		supplier: ERPNext Supplier name (the OCR Import's confirmed supplier)
		product_code: Supplier's own SKU as printed on the invoice

	Returns:
		tuple: (item_code, "Auto Matched") on a single unambiguous hit,
		       (None, "Unmatched") on zero hits or multi-hit ambiguity.
	"""
	if not supplier or not product_code:
		return None, "Unmatched"

	supplier = supplier.strip()
	product_code = product_code.strip()
	if not supplier or not product_code:
		return None, "Unmatched"

	# Item Supplier is a child table; query by parenttype + supplier + supplier_part_no
	rows = frappe.get_all(
		"Item Supplier",
		filters={
			"parenttype": "Item",
			"supplier": supplier,
			"supplier_part_no": product_code,
		},
		fields=["parent"],
		limit_page_length=2,  # we only need to know "exactly one" vs "more"
		ignore_permissions=True,
	)

	if not rows:
		return None, "Unmatched"

	if len(rows) > 1:
		# Ambiguous — log for site cleanup, fall through to description tiers
		frappe.log_error(
			title="OCR: ambiguous Item Supplier match",
			message=(
				f"Supplier '{supplier}' + product_code '{product_code}' "
				f"resolves to multiple Items: {[r.parent for r in rows]}. "
				"Falling through to description-based matching. "
				"Clean up the duplicate Item Supplier rows in ERPNext to enable auto-matching."
			),
		)
		return None, "Unmatched"

	return rows[0].parent, "Auto Matched"


def match_supplier_fuzzy(ocr_text: str, threshold: float = 80) -> tuple[str | None, str, float]:
	"""
	Fuzzy fallback for supplier matching using difflib.SequenceMatcher.

	Called only when exact matching (match_supplier) fails.
	Compares OCR text against all active suppliers and existing aliases.

	Args:
		ocr_text: OCR-extracted supplier name
		threshold: Minimum similarity score (0-100) to consider a match

	Returns:
		tuple: (supplier_name or None, "Suggested" or "Unmatched", confidence_score)
	"""
	if not ocr_text:
		return None, "Unmatched", 0

	ocr_lower = ocr_text.strip().lower()
	best_match = None
	best_score = 0

	# Build candidate pool: suppliers + aliases
	suppliers = frappe.get_all(
		"Supplier",
		filters={"disabled": 0},
		fields=["name", "supplier_name"],
		limit_page_length=0,
		ignore_permissions=True,
	)

	for s in suppliers:
		for candidate in (s.name, s.supplier_name):
			if not candidate:
				continue
			score = SequenceMatcher(None, ocr_lower, candidate.lower()).ratio() * 100
			if score > best_score:
				best_score = score
				best_match = s.name

	# Also check alias table (fuzzy against alias ocr_text → resolve to supplier)
	aliases = frappe.get_all(
		"OCR Supplier Alias",
		fields=["ocr_text", "supplier"],
		limit_page_length=0,
		ignore_permissions=True,
	)
	for a in aliases:
		if not a.ocr_text:
			continue
		score = SequenceMatcher(None, ocr_lower, a.ocr_text.lower()).ratio() * 100
		if score > best_score:
			best_score = score
			best_match = a.supplier

	if best_match and best_score >= threshold:
		return best_match, "Suggested", best_score

	return None, "Unmatched", 0


def match_item_fuzzy(ocr_text: str, threshold: float = 80) -> tuple[str | None, str, float]:
	"""
	Fuzzy fallback for item matching using difflib.SequenceMatcher.

	Called only when exact matching (match_item) fails.
	Compares OCR text against all active items and existing aliases.

	Args:
		ocr_text: OCR-extracted item description or product code
		threshold: Minimum similarity score (0-100) to consider a match

	Returns:
		tuple: (item_code or None, "Suggested" or "Unmatched", confidence_score)
	"""
	if not ocr_text:
		return None, "Unmatched", 0

	ocr_lower = ocr_text.strip().lower()
	best_match = None
	best_score = 0

	# Build candidate pool: items + aliases
	items = frappe.get_all(
		"Item",
		filters={"disabled": 0},
		fields=["name", "item_name"],
		limit_page_length=0,
		ignore_permissions=True,
	)

	for i in items:
		for candidate in (i.name, i.item_name):
			if not candidate:
				continue
			score = SequenceMatcher(None, ocr_lower, candidate.lower()).ratio() * 100
			if score > best_score:
				best_score = score
				best_match = i.name

	# Also check alias table
	aliases = frappe.get_all(
		"OCR Item Alias",
		fields=["ocr_text", "item_code"],
		limit_page_length=0,
		ignore_permissions=True,
	)
	for a in aliases:
		if not a.ocr_text:
			continue
		score = SequenceMatcher(None, ocr_lower, a.ocr_text.lower()).ratio() * 100
		if score > best_score:
			best_score = score
			best_match = a.item_code

	if best_match and best_score >= threshold:
		return best_match, "Suggested", best_score

	return None, "Unmatched", 0


def match_service_item(
	description_ocr: str, company: str | None = None, supplier: str | None = None
) -> dict | None:
	"""
	Attempt to match an OCR description to a service item mapping.

	Service mappings are learned patterns that include:
	- Item code (e.g., ITEM001, DELIVERY-PURCHASES)
	- Expense account (e.g., 5200 - Subscription Expenses)
	- Cost center (optional)
	- Supplier (optional) for supplier-specific mappings

	Matching logic:
	- Priority: supplier-specific mappings first, then generic mappings
	- Within each priority level: longest pattern match wins
	- Searches OCR Service Mapping for description patterns (case-insensitive, partial match)

	Args:
		description_ocr: OCR-extracted description text
		company: Company to filter mappings (optional, uses default if not provided)
		supplier: Supplier to filter mappings (optional, for supplier-specific patterns)

	Returns:
		dict with keys: item_code, item_name, expense_account, cost_center, match_status
		OR None if no match found
	"""
	if not description_ocr:
		return None

	description_norm = normalize_for_matching(description_ocr)

	if not company:
		company = frappe.defaults.get_user_default("Company")

	# Priority 1: Supplier-specific mappings (if supplier is provided)
	if supplier:
		supplier_mappings = frappe.get_all(
			"OCR Service Mapping",
			filters={"company": company, "supplier": supplier},
			fields=["description_pattern", "item_code", "item_name", "expense_account", "cost_center"],
			order_by="LENGTH(description_pattern) DESC",
			ignore_permissions=True,
		)

		for mapping in supplier_mappings:
			pattern_norm = normalize_for_matching(mapping.description_pattern)
			if pattern_norm in description_norm:
				return {
					"item_code": mapping.item_code,
					"item_name": mapping.item_name,
					"expense_account": mapping.expense_account,
					"cost_center": mapping.cost_center,
					"match_status": "Auto Matched",
				}

	# Priority 2: Generic mappings (supplier field is empty/null)
	generic_mappings = frappe.get_all(
		"OCR Service Mapping",
		filters={"company": company, "supplier": ["is", "not set"]},
		fields=["description_pattern", "item_code", "item_name", "expense_account", "cost_center"],
		order_by="LENGTH(description_pattern) DESC",
		ignore_permissions=True,
	)

	for mapping in generic_mappings:
		pattern_norm = normalize_for_matching(mapping.description_pattern)
		if pattern_norm in description_norm:
			return {
				"item_code": mapping.item_code,
				"item_name": mapping.item_name,
				"expense_account": mapping.expense_account,
				"cost_center": mapping.cost_center,
				"match_status": "Auto Matched",
			}

	return None
