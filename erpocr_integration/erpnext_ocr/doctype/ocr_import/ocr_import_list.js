// Copyright (c) 2025, ERPNext OCR Integration Contributors
// For license information, please see license.txt

frappe.listview_settings['OCR Import'] = {
	add_fields: ['status', 'supplier', 'total_amount', 'currency', 'document_type',
		'purchase_invoice', 'purchase_receipt', 'journal_entry'],

	get_indicator: function(doc) {
		// Return [label, color, field] — standard ERPNext status indicator pattern
		const status_map = {
			'Pending':       [__('Pending'), 'orange', 'status'],
			'Needs Review':  [__('Needs Review'), 'orange', 'status'],
			'Matched':       [__('Matched'), 'blue', 'status'],
			'Draft Created': [__('Draft Created'), 'purple', 'status'],
			'Completed':     [__('Completed'), 'green', 'status'],
			'Error':         [__('Error'), 'red', 'status']
		};
		return status_map[doc.status] || [__(doc.status), 'grey', 'status'];
	},

	formatters: {
		total_amount: function(value, df, doc) {
			if (value) {
				let formatted = format_currency(value, doc.currency);
				return formatted;
			}
			return '';
		}
	}
};
