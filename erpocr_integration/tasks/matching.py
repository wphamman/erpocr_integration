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


def match_item(ocr_text: str, supplier: str | None = None) -> tuple[str | None, str]:
	"""
	Attempt to match an OCR-extracted item description to an ERPNext Item.

	Matching priority (v1.8.0, Q7c — supplier-scoped aliases):
	1. Supplier-scoped OCR Item Alias (exact ocr_text + this supplier) — only
	   when the caller passes the confirmed supplier. Beats the global alias so
	   the same printed description can map to different items per supplier
	   (the cross-supplier collision case).
	2. Global OCR Item Alias (exact ocr_text, blank supplier) — every
	   pre-v1.8.0 alias row lands here unchanged; the fallback tier.
	3. Exact match against Item.item_name
	4. Exact match against Item.name (item_code)

	Returns:
		tuple: (item_code or None, match_status)
	"""
	if not ocr_text:
		return None, "Unmatched"

	ocr_text_stripped = ocr_text.strip()
	supplier = (supplier or "").strip()

	# 1. Supplier-scoped alias (exact match) — highest description-tier
	# precision. order_by matches the global tier and the correction path
	# (R8): duplicates are legal now, and reads must deterministically hit
	# the same most-recently-modified row corrections target.
	if supplier:
		alias = frappe.db.get_value(
			"OCR Item Alias",
			{"ocr_text": ocr_text_stripped, "supplier": supplier},
			"item_code",
			order_by="modified desc, name asc",
		)
		if alias:
			return alias, "Auto Matched"

	# 2. Global alias (exact match, blank supplier). The "is not set" filter
	# keeps a supplier-scoped row from shadowing other suppliers' lines —
	# same NULL-filter pattern as match_service_item's generic tier.
	# order_by is load-bearing (R8): duplicates are possible now that the
	# ocr_text unique index is gone — most-recently-curated row wins,
	# deterministically on v15 AND v16 (same order as _save_item_alias's
	# correction target, so reads and corrections hit the same row).
	rows = frappe.get_all(
		"OCR Item Alias",
		filters={"ocr_text": ocr_text_stripped, "supplier": ["is", "not set"]},
		fields=["item_code"],
		order_by="modified desc, name asc",
		limit_page_length=1,
		ignore_permissions=True,
	)
	if rows:
		return rows[0].item_code, "Auto Matched"

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


def match_item_fuzzy(
	ocr_text: str, threshold: float = 80, supplier: str | None = None
) -> tuple[str | None, str, float]:
	"""
	Fuzzy fallback for item matching using difflib.SequenceMatcher.

	Called only when exact matching (match_item) fails.
	Compares OCR text against all active items and existing aliases.

	The alias pool honours Q7c scoping (v1.8.0): global rows plus rows scoped
	to the passed supplier — supplier A's scoped alias must not become a
	fuzzy "Suggested" candidate on supplier B's lines (the cross-supplier
	collision would otherwise re-enter one tier below the exact match).

	Args:
		ocr_text: OCR-extracted item description or product code
		threshold: Minimum similarity score (0-100) to consider a match
		supplier: confirmed supplier — includes that supplier's scoped aliases

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

	# Also check alias table — global rows + this supplier's scoped rows only
	# (Python-side filter: one query, and NULL/"" both count as global).
	supplier = (supplier or "").strip()
	aliases = frappe.get_all(
		"OCR Item Alias",
		fields=["ocr_text", "item_code", "supplier"],
		limit_page_length=0,
		ignore_permissions=True,
	)
	for a in aliases:
		if not a.ocr_text:
			continue
		alias_supplier = getattr(a, "supplier", None)
		if alias_supplier and alias_supplier != supplier:
			continue  # another supplier's scoped alias — not a candidate here
		score = SequenceMatcher(None, ocr_lower, a.ocr_text.lower()).ratio() * 100
		if score > best_score:
			best_score = score
			best_match = a.item_code

	if best_match and best_score >= threshold:
		return best_match, "Suggested", best_score

	return None, "Unmatched", 0


# A service mapping whose description_pattern is this sentinel is a *supplier
# default*: it codes ANY line from that supplier. Used as the last resort (after
# specific + generic patterns) for suppliers whose line descriptions vary too much
# to learn per-pattern — e.g. a transport subcontractor where every line embeds the
# route/driver/vehicle. description_pattern is mandatory on the doctype, so the
# sentinel is a literal "*" rather than a blank.
SUPPLIER_DEFAULT_PATTERN = "*"


def _service_mapping_result(mapping) -> dict:
	return {
		"item_code": mapping.item_code,
		"item_name": mapping.item_name,
		"expense_account": mapping.expense_account,
		"cost_center": mapping.cost_center,
		"match_status": "Auto Matched",
	}


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

	Matching priority (highest first):
	  1. Supplier-specific pattern mappings (longest pattern wins)
	  2. Generic pattern mappings (no supplier; longest pattern wins)
	  3. Supplier default — a supplier-scoped mapping whose pattern is the literal
	     "*" sentinel (SUPPLIER_DEFAULT_PATTERN). Codes any remaining line for that
	     supplier. Last resort, so specific/generic patterns always win.

	Patterns are matched as normalized substrings (case-insensitive, punctuation
	stripped). A pattern that normalizes to empty is ignored (never a wildcard) —
	only the explicit "*" sentinel acts supplier-wide.

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

	# Priority 1: Supplier-specific pattern mappings (if supplier is provided)
	if supplier:
		supplier_mappings = frappe.get_all(
			"OCR Service Mapping",
			filters={"company": company, "supplier": supplier},
			fields=["description_pattern", "item_code", "item_name", "expense_account", "cost_center"],
			order_by="LENGTH(description_pattern) DESC",
			ignore_permissions=True,
		)

		for mapping in supplier_mappings:
			if (mapping.description_pattern or "").strip() == SUPPLIER_DEFAULT_PATTERN:
				continue  # the supplier default — handled at Priority 3, never as a substring
			pattern_norm = normalize_for_matching(mapping.description_pattern)
			if pattern_norm and pattern_norm in description_norm:
				return _service_mapping_result(mapping)

	# Priority 2: Generic mappings (supplier field is empty/null)
	generic_mappings = frappe.get_all(
		"OCR Service Mapping",
		filters={"company": company, "supplier": ["is", "not set"]},
		fields=["description_pattern", "item_code", "item_name", "expense_account", "cost_center"],
		order_by="LENGTH(description_pattern) DESC",
		ignore_permissions=True,
	)

	for mapping in generic_mappings:
		if (mapping.description_pattern or "").strip() == SUPPLIER_DEFAULT_PATTERN:
			continue
		pattern_norm = normalize_for_matching(mapping.description_pattern)
		if pattern_norm and pattern_norm in description_norm:
			return _service_mapping_result(mapping)

	# Priority 3: Supplier default — the "*" wildcard row for this supplier, if any.
	# Codes any line the specific/generic patterns didn't recognise.
	if supplier:
		default_rows = frappe.get_all(
			"OCR Service Mapping",
			filters={
				"company": company,
				"supplier": supplier,
				"description_pattern": SUPPLIER_DEFAULT_PATTERN,
			},
			fields=["description_pattern", "item_code", "item_name", "expense_account", "cost_center"],
			limit_page_length=1,
			ignore_permissions=True,
		)
		if default_rows:
			return _service_mapping_result(default_rows[0])

	return None
