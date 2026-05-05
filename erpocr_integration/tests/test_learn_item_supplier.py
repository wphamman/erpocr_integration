"""Tests for tasks/learn_item_supplier.py — background job that writes
(supplier, product_code) → item_code mappings into ERPNext's standard
Item Supplier child table.

Covers:
  - permission posture (originating user, not Administrator)
  - skip + log when user lacks Item write
  - DB existence re-check (idempotency under concurrent confirms)
  - skip when any input is missing or stale
  - successful append + save path
  - graceful failure when Item.save throws
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from erpocr_integration.tasks.learn_item_supplier import learn_item_supplier


def _setup_happy_path(mock_frappe, item_supplier_exists=False):
	"""Configure mock_frappe so the job can run end-to-end."""
	mock_frappe.db.exists = MagicMock(
		side_effect=lambda doctype, *args, **kwargs: {
			"Item": True,
			"Supplier": True,
			"Item Supplier": item_supplier_exists,
		}.get(doctype, False)
	)
	mock_frappe.has_permission = MagicMock(return_value=True)
	item_doc = MagicMock()
	item_doc.append = MagicMock()
	item_doc.save = MagicMock()
	mock_frappe.get_doc = MagicMock(return_value=item_doc)
	return item_doc


class TestLearnItemSupplier:
	def test_skips_when_item_code_missing(self, mock_frappe):
		learn_item_supplier(
			item_code="", supplier="Acme", product_code="P-001", originating_user="user@example.com"
		)
		mock_frappe.set_user.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

	def test_skips_when_supplier_missing(self, mock_frappe):
		learn_item_supplier(
			item_code="ITEM-001", supplier="", product_code="P-001", originating_user="user@example.com"
		)
		mock_frappe.set_user.assert_not_called()

	def test_skips_when_product_code_missing(self, mock_frappe):
		learn_item_supplier(
			item_code="ITEM-001", supplier="Acme", product_code="", originating_user="user@example.com"
		)
		mock_frappe.set_user.assert_not_called()

	def test_skips_when_originating_user_missing(self, mock_frappe):
		learn_item_supplier(item_code="ITEM-001", supplier="Acme", product_code="P-001", originating_user="")
		mock_frappe.set_user.assert_not_called()

	def test_skips_when_item_no_longer_exists(self, mock_frappe):
		"""Item could be deleted between enqueue and execution — exit cleanly."""
		mock_frappe.db.exists = MagicMock(side_effect=lambda doctype, *args, **kwargs: doctype == "Supplier")
		learn_item_supplier(
			item_code="ITEM-DELETED",
			supplier="Acme",
			product_code="P-001",
			originating_user="user@example.com",
		)
		mock_frappe.set_user.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

	def test_skips_when_supplier_no_longer_exists(self, mock_frappe):
		mock_frappe.db.exists = MagicMock(side_effect=lambda doctype, *args, **kwargs: doctype == "Item")
		learn_item_supplier(
			item_code="ITEM-001",
			supplier="DELETED",
			product_code="P-001",
			originating_user="user@example.com",
		)
		mock_frappe.set_user.assert_not_called()
		mock_frappe.get_doc.assert_not_called()

	def test_uses_originating_user_not_administrator(self, mock_frappe):
		"""Worker must set_user to the originating user, not stay as Administrator."""
		_setup_happy_path(mock_frappe)
		learn_item_supplier(
			item_code="ITEM-001",
			supplier="Acme",
			product_code="P-001",
			originating_user="danell@starpops.co.za",
		)
		mock_frappe.set_user.assert_called_once_with("danell@starpops.co.za")

	def test_skips_and_logs_when_no_item_write_permission(self, mock_frappe):
		"""User without Item write → log, skip Item.save, don't crash."""
		_setup_happy_path(mock_frappe)
		mock_frappe.has_permission = MagicMock(return_value=False)

		learn_item_supplier(
			item_code="ITEM-001",
			supplier="Acme",
			product_code="P-001",
			originating_user="readonly@example.com",
		)

		mock_frappe.has_permission.assert_called_once_with("Item", "write", doc="ITEM-001")
		mock_frappe.get_doc.assert_not_called()
		mock_frappe.log_error.assert_called_once()
		log_message = mock_frappe.log_error.call_args.kwargs["message"]
		assert "readonly@example.com" in log_message
		assert "no permission" in mock_frappe.log_error.call_args.kwargs["title"].lower()

	def test_skips_when_item_supplier_row_already_exists(self, mock_frappe):
		"""DB existence re-check prevents duplicate writes — idempotent."""
		item_doc = _setup_happy_path(mock_frappe, item_supplier_exists=True)
		learn_item_supplier(
			item_code="ITEM-001",
			supplier="Acme",
			product_code="P-001",
			originating_user="user@example.com",
		)
		# Existence check ran for Item Supplier
		exists_calls = [c.args[0] for c in mock_frappe.db.exists.call_args_list]
		assert "Item Supplier" in exists_calls
		# But we never appended/saved
		item_doc.append.assert_not_called()
		item_doc.save.assert_not_called()

	def test_happy_path_appends_and_saves(self, mock_frappe):
		"""When all checks pass, append the supplier row and save the Item."""
		item_doc = _setup_happy_path(mock_frappe, item_supplier_exists=False)

		learn_item_supplier(
			item_code="ITEM-001",
			supplier="Acme",
			product_code="P-001",
			originating_user="user@example.com",
		)

		item_doc.append.assert_called_once_with(
			"supplier_items",
			{"supplier": "Acme", "supplier_part_no": "P-001"},
		)
		item_doc.save.assert_called_once()
		# Save uses the user's permissions (no ignore_permissions=True)
		assert "ignore_permissions" not in item_doc.save.call_args.kwargs
		mock_frappe.db.commit.assert_called_once()

	def test_logs_and_continues_when_save_throws(self, mock_frappe):
		"""Item.save() failures (e.g. custom validate hook) must not crash worker."""
		item_doc = _setup_happy_path(mock_frappe, item_supplier_exists=False)
		item_doc.save.side_effect = Exception("validate hook rejected the row")

		# Should NOT raise — graceful logging
		learn_item_supplier(
			item_code="ITEM-001",
			supplier="Acme",
			product_code="P-001",
			originating_user="user@example.com",
		)

		mock_frappe.log_error.assert_called()
		assert "learning failed" in mock_frappe.log_error.call_args.kwargs["title"].lower()

	def test_strips_whitespace_from_inputs(self, mock_frappe):
		"""Inputs are normalized (.strip()) before use."""
		item_doc = _setup_happy_path(mock_frappe, item_supplier_exists=False)
		learn_item_supplier(
			item_code="  ITEM-001  ",
			supplier="  Acme  ",
			product_code="  P-001  ",
			originating_user="  user@example.com  ",
		)
		item_doc.append.assert_called_once_with(
			"supplier_items",
			{"supplier": "Acme", "supplier_part_no": "P-001"},
		)
		mock_frappe.set_user.assert_called_once_with("user@example.com")
