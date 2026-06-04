frappe.ui.form.on('OCR Fleet Slip', {
	setup: function(frm) {
		frm.set_query('fleet_vehicle', function() {
			return { filters: { is_active: 1 } };
		});
		frm.set_query('expense_account', function() {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					disabled: 0
				}
			};
		});
		frm.set_query('cost_center', function() {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					disabled: 0
				}
			};
		});
		frm.set_query('tax_template', function() {
			return {
				filters: { company: frm.doc.company }
			};
		});
	},

	refresh: function(frm) {
		fleet_set_status_intro(frm);

		// Posting Mode branches the disposition:
		// - Direct Expense → create a Purchase Invoice (the slip is the AP source doc)
		// - Fleet Card    → close as a control record via "Mark Recorded" (no PI;
		//                   cost is booked from the provider's monthly invoice in fleet_management)
		var reviewable = !frm.is_new() && ['Matched', 'Needs Review'].includes(frm.doc.status);

		if (reviewable && frm.doc.posting_mode === 'Direct Expense') {
			frm.add_custom_button(__('Purchase Invoice'), function() {
				fleet_create_document(frm, 'Purchase Invoice', 'create_purchase_invoice');
			}, __('Create'));
		}

		if (reviewable && frm.doc.posting_mode === 'Fleet Card') {
			frm.add_custom_button(__('Mark Recorded'), function() {
				frappe.confirm(
					__('Close this slip as a control record? No Purchase Invoice will be created — the cost is booked from the fleet card provider\'s monthly invoice.'),
					function() {
						frappe.call({
							method: 'mark_recorded',
							doc: frm.doc,
							callback: function(r) {
								if (!r.exc) { frm.reload_doc(); }
							}
						});
					}
				);
			}).addClass('btn-primary');
		}

		// Unlink & Reset
		if (!frm.is_new() && frm.doc.status === 'Draft Created') {
			frm.add_custom_button(__('Unlink & Reset'), function() {
				frappe.confirm(
					__('This will delete the draft document and reset this fleet slip. Continue?'),
					function() {
						frappe.call({
							method: 'unlink_document',
							doc: frm.doc,
							callback: function(r) {
								if (!r.exc) { frm.reload_doc(); }
							}
						});
					}
				);
			}, __('Actions'));
		}

		// No Action Required
		if (!frm.is_new() && !['Completed', 'Draft Created', 'No Action'].includes(frm.doc.status)) {
			frm.add_custom_button(__('No Action Required'), function() {
				frappe.prompt(
					{ fieldtype: 'Small Text', label: __('Reason'), reqd: 1, fieldname: 'reason' },
					function(values) {
						frappe.call({
							method: 'mark_no_action',
							doc: frm.doc,
							args: { reason: values.reason },
							callback: function(r) {
								if (!r.exc) { frm.reload_doc(); }
							}
						});
					},
					__('No Action Required'),
					__('Confirm')
				);
			}, __('Actions'));
		}

		// Retry Extraction
		if (!frm.is_new() && frm.doc.status === 'Error') {
			frm.add_custom_button(__('Retry Extraction'), function() {
				frappe.call({
					method: 'erpocr_integration.fleet_api.retry_fleet_extraction',
					args: { ocr_fleet_name: frm.doc.name },
					callback: function(r) {
						if (!r.exc) { frm.reload_doc(); }
					}
				});
			});
		}

		// Move to Invoice Pipeline — for slips that aren't fleet card transactions
		// (e.g. fuel paid with a personal card, dropped in the wrong Drive folder)
		if (!frm.is_new() && !['Completed', 'Draft Created', 'No Action'].includes(frm.doc.status)) {
			frm.add_custom_button(__('Move to Invoice Pipeline'), function() {
				frappe.confirm(
					__('This slip will be re-processed as a regular invoice (not a fleet card transaction). The fleet slip will be marked as No Action and a new OCR Import will be created. Continue?'),
					function() {
						frappe.call({
							method: 'erpocr_integration.fleet_api.route_to_invoice_pipeline',
							args: { ocr_fleet_name: frm.doc.name },
							callback: function(r) {
								if (!r.exc && r.message) {
									frappe.show_alert({
										message: __('Created OCR Import {0}. Redirecting...', [r.message]),
										indicator: 'green'
									});
									setTimeout(function() {
										frappe.set_route('Form', 'OCR Import', r.message);
									}, 1500);
								}
							}
						});
					}
				);
			}, __('Actions'));
		}

		// View Original Scan
		if (!frm.is_new() && frm.doc.drive_link) {
			frm.add_custom_button(__('View Original Scan'), function() {
				window.open(frm.doc.drive_link, '_blank');
			});
		}

		// Unauthorized warning
		if (frm.doc.unauthorized_flag) {
			frm.dashboard.set_headline(
				__('This slip was classified as <b>Other</b> — not fuel or toll. Review and mark No Action if unauthorized.'),
				'orange'
			);
		}
	},

	fleet_vehicle: function(frm) {
		if (frm.doc.fleet_vehicle && frm.doc.vehicle_match_status !== 'Confirmed') {
			frm.set_value('vehicle_match_status', 'Confirmed');
		}
		// Clear posting config when vehicle changes — will be re-applied on save
		if (!frm.doc.fleet_vehicle) {
			frm.set_value('posting_mode', '');
			frm.set_value('fleet_card_supplier', '');
			frm.set_value('expense_account', '');
			frm.set_value('cost_center', '');
		}
	}
});


function fleet_set_status_intro(frm) {
	frm.dashboard.clear_headline();

	if (frm.doc.unauthorized_flag) {
		// Handled in refresh above
		return;
	}

	var status = frm.doc.status;

	if (status === 'Pending') {
		frm.set_intro(__('Extracting data from fleet slip scan...'), 'blue');
	} else if (status === 'Error') {
		var error_msg = frm.doc.error_log ? ': ' + frappe.utils.escape_html(frm.doc.error_log.substring(0, 200)) : '';
		frm.set_intro(__('Extraction failed') + error_msg, 'red');
	} else if (status === 'Needs Review') {
		if (frm.doc.posting_mode === 'Fleet Card') {
			frm.set_intro(__('Review extracted data and verify the vehicle match. This is a fleet card slip — once verified, click Mark Recorded (no Purchase Invoice; the cost is booked from the fleet card provider\'s monthly invoice).'), 'orange');
		} else if (frm.doc.posting_mode === 'Direct Expense') {
			frm.set_intro(__('Review extracted data and verify vehicle match, then click Create > Purchase Invoice.'), 'orange');
		} else {
			frm.set_intro(__('Review extracted data and match a vehicle.'), 'orange');
		}
	} else if (status === 'Matched') {
		if (frm.doc.posting_mode === 'Fleet Card') {
			frm.set_intro(__('Ready to record. This is a fleet card slip — the cost is booked from the provider\'s monthly invoice; this slip is the control record. Click Mark Recorded to close it.'), 'blue');
		} else if (frm.doc.posting_mode === 'Direct Expense') {
			frm.set_intro(__('Ready to create Purchase Invoice. Use the Create button above.'), 'blue');
		} else {
			frm.set_intro(__('Match a vehicle to continue.'), 'blue');
		}
	} else if (status === 'Draft Created') {
		var link = '';
		if (frm.doc.purchase_invoice) {
			link = '<a href="/app/purchase-invoice/' + encodeURIComponent(frm.doc.purchase_invoice) + '">' +
				frappe.utils.escape_html(frm.doc.purchase_invoice) + '</a>';
		}
		frm.set_intro(__('Draft created: {0}. Submit it to complete, or Unlink & Reset to start over.', [link]), 'blue');
	} else if (status === 'No Action') {
		var reason = frm.doc.no_action_reason ? ': ' + frappe.utils.escape_html(frm.doc.no_action_reason) : '';
		frm.set_intro(__('No action required') + reason, 'grey');
	} else if (status === 'Completed') {
		if (frm.doc.purchase_invoice) {
			var completed_link = '<a href="/app/purchase-invoice/' + encodeURIComponent(frm.doc.purchase_invoice) + '">' +
				frappe.utils.escape_html(frm.doc.purchase_invoice) + '</a>';
			frm.set_intro(__('Completed: {0}', [completed_link]), 'green');
		} else {
			// Fleet Card path: no PI, recorded as a control entry only.
			frm.set_intro(__('Recorded as a control record. The cost is booked from the fleet card provider\'s monthly invoice.'), 'green');
		}
	}
}


function fleet_create_document(frm, doc_type, method_name) {
	function call_create() {
		frappe.call({
			method: method_name,
			doc: frm.doc,
			freeze: true,
			freeze_message: __('Creating {0}...', [doc_type]),
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			}
		});
	}

	if (frm.doc.document_type === doc_type && !frm.is_dirty()) {
		call_create();
	} else {
		frm.set_value('document_type', doc_type);
		frm.save().then(function() {
			call_create();
		});
	}
}
