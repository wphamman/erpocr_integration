# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

"""
Utility functions for OCR text processing and parsing.

Used by gemini_extract.py for transforming extracted data.
"""

import re


def _clean_ocr_text(value: str) -> str:
	"""Normalize OCR-extracted text by removing common artifacts."""
	if not value:
		return ""
	# Replace newlines/carriage returns with empty string
	value = value.replace("\n", "").replace("\r", "")
	# Collapse multiple spaces into one
	value = re.sub(r"\s+", " ", value)
	# Remove spaces after opening and before closing brackets/parens
	# e.g. "( Pty )" → "(Pty)", "Star Pops ( Pty ) Ltd" → "Star Pops (Pty) Ltd"
	value = re.sub(r"\(\s+", "(", value)
	value = re.sub(r"\s+\)", ")", value)
	value = re.sub(r"\[\s+", "[", value)
	value = re.sub(r"\s+\]", "]", value)
	return value.strip()


def _parse_date(value: str) -> str | None:
	"""Parse a date string into YYYY-MM-DD format, or return None."""
	if not value:
		return None

	from datetime import datetime

	# Normalize whitespace — OCR often adds extra spaces (e.g., "February 9 , 2026")
	value = re.sub(r"\s+", " ", value.strip())
	# Remove spaces before punctuation (e.g., "9 , 2026" → "9, 2026")
	value = re.sub(r"\s+,", ",", value)
	formats = [
		"%Y-%m-%d",  # 2024-01-15
		"%d/%m/%Y",  # 15/01/2024
		"%m/%d/%Y",  # 01/15/2024
		"%d-%m-%Y",  # 15-01-2024
		"%d %B %Y",  # 15 January 2024
		"%d %b %Y",  # 15 Jan 2024
		"%B %d, %Y",  # January 15, 2024
		"%b %d, %Y",  # Jan 15, 2024
	]

	for fmt in formats:
		try:
			dt = datetime.strptime(value, fmt)
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			continue

	# Try to extract a date-like pattern
	match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
	if match:
		return match.group(0)

	return None


def _parse_amount(value: str) -> float:
	"""Parse a currency amount string to float."""
	if not value:
		return 0.0

	# Remove currency symbols, spaces, and thousands separators
	cleaned = re.sub(r"[^\d.,\-]", "", str(value))

	if not cleaned:
		return 0.0

	# Handle comma as decimal separator (e.g., 1.234,56 → 1234.56)
	if "," in cleaned and "." in cleaned:
		if cleaned.rindex(",") > cleaned.rindex("."):
			# Comma is decimal separator (European format)
			cleaned = cleaned.replace(".", "").replace(",", ".")
		else:
			# Comma is thousands separator
			cleaned = cleaned.replace(",", "")
	elif "," in cleaned:
		# Could be either — if exactly 2 digits after comma, treat as decimal
		parts = cleaned.split(",")
		if len(parts) == 2 and len(parts[1]) == 2:
			cleaned = cleaned.replace(",", ".")
		else:
			cleaned = cleaned.replace(",", "")

	try:
		return float(cleaned)
	except ValueError:
		return 0.0


def _parse_float(value: str) -> float:
	"""Parse a numeric string to float. Returns 0.0 for empty or invalid input."""
	if not value:
		return 0.0

	cleaned = re.sub(r"[^\d.\-]", "", str(value))
	try:
		result = float(cleaned)
		return result if result > 0 else 0.0
	except ValueError:
		return 0.0
