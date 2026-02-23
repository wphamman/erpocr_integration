import frappe


def execute():
	"""Clear stale document_type on in-flight OCR Import records.

	In v0.3, document_type defaulted to "Purchase Invoice" and was auto-detected.
	In v0.4, document_type defaults to blank and the user must explicitly select.

	This patch clears document_type on records that:
	- Are still in progress (Pending, Needs Review, Matched)
	- Have NOT yet had a document created (no PI or PR)

	Records already "Completed" or "Error" keep their document_type for historical accuracy.

	NOTE: We only check purchase_invoice and purchase_receipt here because the
	journal_entry column is new in v0.4 and may not exist yet when this patch runs
	(Frappe runs patches before schema sync). Since journal_entry didn't exist in
	v0.3, no in-flight record can have one — the check is unnecessary.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabOCR Import`
		SET document_type = ''
		WHERE status IN ('Pending', 'Needs Review', 'Matched')
		  AND (purchase_invoice IS NULL OR purchase_invoice = '')
		  AND (purchase_receipt IS NULL OR purchase_receipt = '')
		"""
	)
