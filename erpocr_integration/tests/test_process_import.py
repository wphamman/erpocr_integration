"""Tests for erpocr_integration.tasks.process_import — pure utility functions."""

import pytest

from erpocr_integration.tasks.process_import import (
	_clean_ocr_text,
	_parse_amount,
	_parse_date,
	_parse_float,
)


# ---------------------------------------------------------------------------
# _clean_ocr_text
# ---------------------------------------------------------------------------

class TestCleanOcrText:
	def test_none_returns_empty(self):
		assert _clean_ocr_text(None) == ""

	def test_empty_returns_empty(self):
		assert _clean_ocr_text("") == ""

	def test_strips_whitespace(self):
		assert _clean_ocr_text("  hello  ") == "hello"

	def test_removes_newlines(self):
		assert _clean_ocr_text("line1\nline2\rline3") == "line1line2line3"

	def test_collapses_multiple_spaces(self):
		assert _clean_ocr_text("too   many    spaces") == "too many spaces"

	def test_bracket_spacing_parens(self):
		assert _clean_ocr_text("Star Pops ( Pty ) Ltd") == "Star Pops (Pty) Ltd"

	def test_bracket_spacing_square(self):
		assert _clean_ocr_text("[ item ]") == "[item]"

	def test_mixed_artifacts(self):
		assert _clean_ocr_text("  Star\nPops  ( Pty )  Ltd  ") == "StarPops (Pty) Ltd"

	def test_product_code_with_newline(self):
		assert _clean_ocr_text("POP-\n050") == "POP-050"

	def test_already_clean(self):
		assert _clean_ocr_text("Clean Text") == "Clean Text"


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
	def test_none_returns_none(self):
		assert _parse_date(None) is None

	def test_empty_returns_none(self):
		assert _parse_date("") is None

	def test_iso_format(self):
		assert _parse_date("2024-01-15") == "2024-01-15"

	def test_dd_mm_yyyy_slash(self):
		assert _parse_date("15/01/2024") == "2024-01-15"

	def test_mm_dd_yyyy_slash(self):
		assert _parse_date("01/15/2024") == "2024-01-15"

	def test_dd_mm_yyyy_dash(self):
		assert _parse_date("15-01-2024") == "2024-01-15"

	def test_dd_month_yyyy(self):
		assert _parse_date("15 January 2024") == "2024-01-15"

	def test_dd_mon_yyyy(self):
		assert _parse_date("15 Jan 2024") == "2024-01-15"

	def test_month_dd_yyyy(self):
		assert _parse_date("January 15, 2024") == "2024-01-15"

	def test_mon_dd_yyyy(self):
		assert _parse_date("Jan 15, 2024") == "2024-01-15"

	def test_ocr_extra_spaces(self):
		"""OCR often adds extra spaces around punctuation."""
		assert _parse_date("February 9 , 2026") == "2026-02-09"

	def test_embedded_iso_date(self):
		"""Falls back to regex extraction of YYYY-MM-DD."""
		assert _parse_date("Date: 2024-06-15 (final)") == "2024-06-15"

	def test_garbage_returns_none(self):
		assert _parse_date("not a date at all") is None

	def test_leading_trailing_whitespace(self):
		assert _parse_date("  2024-01-15  ") == "2024-01-15"


# ---------------------------------------------------------------------------
# _parse_amount
# ---------------------------------------------------------------------------

class TestParseAmount:
	def test_none_returns_zero(self):
		assert _parse_amount(None) == 0.0

	def test_empty_returns_zero(self):
		assert _parse_amount("") == 0.0

	def test_plain_number(self):
		assert _parse_amount("1234.56") == 1234.56

	def test_currency_symbol_rand(self):
		assert _parse_amount("R1,234.56") == 1234.56

	def test_currency_symbol_dollar(self):
		assert _parse_amount("$100.00") == 100.00

	def test_european_format(self):
		"""Comma as decimal, period as thousands."""
		assert _parse_amount("1.234,56") == 1234.56

	def test_comma_as_decimal(self):
		"""Comma with exactly 2 digits after = decimal."""
		assert _parse_amount("1234,56") == 1234.56

	def test_comma_as_thousands(self):
		"""Comma with != 2 digits after = thousands."""
		assert _parse_amount("1,234") == 1234.0

	def test_negative_amount(self):
		assert _parse_amount("-500.00") == -500.0

	def test_spaces_in_amount(self):
		assert _parse_amount("R 1 234.56") == 1234.56

	def test_garbage_returns_zero(self):
		assert _parse_amount("N/A") == 0.0

	def test_integer_string(self):
		assert _parse_amount("500") == 500.0


# ---------------------------------------------------------------------------
# _parse_float
# ---------------------------------------------------------------------------

class TestParseFloat:
	def test_none_returns_zero(self):
		assert _parse_float(None) == 0.0

	def test_empty_returns_zero(self):
		assert _parse_float("") == 0.0

	def test_valid_float(self):
		assert _parse_float("123.45") == 123.45

	def test_non_numeric_returns_zero(self):
		assert _parse_float("abc") == 0.0

	def test_negative_returns_zero(self):
		"""_parse_float clamps to >= 0."""
		assert _parse_float("-5.0") == 0.0

	def test_integer_string(self):
		assert _parse_float("42") == 42.0
