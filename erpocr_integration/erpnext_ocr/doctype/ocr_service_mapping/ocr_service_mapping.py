# Copyright (c) 2026, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OCRServiceMapping(Document):
	"""
	Stores learned mappings from OCR descriptions to service items with GL accounts.

	When a user manually selects:
	- Service item (e.g., ITEM001)
	- Expense account (e.g., 5200 - Subscription Expenses)
	- Cost center (e.g., Main - CC)

	For a description like "Subscription (Feb 26)...", the system creates this mapping
	so future invoices with "subscription" in the description auto-fill these fields.
	"""

	def validate(self):
		"""Validate the service mapping."""
		# Convert description pattern to lowercase for consistent matching
		if self.description_pattern:
			self.description_pattern = self.description_pattern.lower().strip()

		# Set default company if not set
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company")

		# Validate expense account belongs to company
		if self.expense_account:
			account_company = frappe.db.get_value("Account", self.expense_account, "company")
			if account_company != self.company:
				frappe.throw(f"Expense Account {self.expense_account} does not belong to company {self.company}")
