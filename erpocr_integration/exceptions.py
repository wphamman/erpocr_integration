# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt


class OCRImportError(Exception):
	"""Raised when OCR import processing fails."""

	pass


class WebhookValidationError(Exception):
	"""Raised when webhook request validation fails."""

	pass
