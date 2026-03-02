// Copyright (c) 2025, ERPNext OCR Integration Contributors
// For license information, please see license.txt

frappe.listview_settings['OCR Delivery Note'] = {
	add_fields: ['status', 'supplier', 'document_type',
		'purchase_order_result', 'purchase_receipt'],

	get_indicator: function(doc) {
		const status_map = {
			'Pending':       [__('Pending'), 'orange', 'status'],
			'Needs Review':  [__('Needs Review'), 'orange', 'status'],
			'Matched':       [__('Matched'), 'blue', 'status'],
			'Draft Created': [__('Draft Created'), 'purple', 'status'],
			'Completed':     [__('Completed'), 'green', 'status'],
			'No Action':     [__('No Action'), 'grey', 'status'],
			'Error':         [__('Error'), 'red', 'status']
		};
		return status_map[doc.status] || [__(doc.status), 'grey', 'status'];
	}
};
