// Copyright (c) 2025, ERPNext OCR Integration Contributors
// For license information, please see license.txt

frappe.ui.form.on('OCR Import', {
	setup: function(frm) {
		// Filter tax template by company
		frm.set_query('tax_template', function() {
			return {
				filters: {
					company: frm.doc.company
				}
			};
		});
	},

	refresh: function(frm) {
		// Add "Upload PDF" button for new records
		if (frm.is_new()) {
			frm.add_custom_button(__('Upload PDF'), function() {
				// Create file input element
				let input = document.createElement('input');
				input.type = 'file';
				input.accept = 'application/pdf';

				input.onchange = function(e) {
					let file = e.target.files[0];
					if (!file) return;

					// Validate file size (10MB max)
					if (file.size > 10 * 1024 * 1024) {
						frappe.msgprint(__('File too large. Maximum size is 10MB.'));
						return;
					}

					// Validate file type
					if (!file.name.toLowerCase().endsWith('.pdf')) {
						frappe.msgprint(__('Only PDF files are supported.'));
						return;
					}

					// Upload via FormData
					let formData = new FormData();
					formData.append('file', file);

					frappe.show_alert({
						message: __('Uploading {0}...', [file.name]),
						indicator: 'blue'
					});

					// Use XMLHttpRequest for file upload (frappe.call doesn't support FormData well)
					let xhr = new XMLHttpRequest();
					xhr.open('POST', '/api/method/erpocr_integration.api.upload_pdf');
					xhr.setRequestHeader('X-Frappe-CSRF-Token', frappe.csrf_token);

					xhr.onload = function() {
						if (xhr.status === 200) {
							try {
								let response = JSON.parse(xhr.responseText);
								if (response.message) {
									let ocr_import_name = response.message.ocr_import;

									// Navigate to created OCR Import
									frappe.set_route('Form', 'OCR Import', ocr_import_name);

									// Poll for status updates
									poll_extraction_status(ocr_import_name);
								}
							} catch (e) {
								frappe.msgprint(__('Error parsing response'));
							}
						} else {
							try {
								let error = JSON.parse(xhr.responseText);
								frappe.msgprint(__('Upload failed: {0}', [error._server_messages || error.message || 'Unknown error']));
							} catch (e) {
								frappe.msgprint(__('Upload failed'));
							}
						}
					};

					xhr.onerror = function() {
						frappe.msgprint(__('Upload failed'));
					};

					xhr.send(formData);
				};

				input.click();
			}, __('Actions'));
		}

		// Add retry/re-upload buttons for failed extractions
		if (frm.doc.status === 'Error' && (frm.doc.source_type === 'Gemini Manual Upload' || frm.doc.source_type === 'Gemini Email')) {
			if (frm.doc.drive_file_id) {
				// PDF is in Drive — offer retry
				frm.add_custom_button(__('Retry Extraction'), function() {
					frappe.call({
						method: 'erpocr_integration.api.retry_gemini_extraction',
						args: {ocr_import: frm.doc.name},
						callback: function(r) {
							if (!r.exc) {
								frm.reload_doc();
								frappe.show_alert({
									message: __('Retrying extraction...'),
									indicator: 'blue'
								});
								poll_extraction_status(frm.doc.name);
							}
						}
					});
				}, __('Actions'));
			} else {
				// No PDF available — offer re-upload
				frm.add_custom_button(__('Re-upload PDF'), function() {
					frappe.set_route('Form', 'OCR Import', 'new');
				}, __('Actions'));
			}
		}

		// Add "View Original Invoice" button and make Drive link clickable
		if (!frm.is_new() && frm.doc.drive_link) {
			frm.add_custom_button(__('View Original Invoice'), function() {
				window.open(frm.doc.drive_link, '_blank');
			}, __('Actions'));

			// Render drive_link as clickable HTML
			let link_html = `<a href="${frm.doc.drive_link}" target="_blank" style="word-break: break-all;">View in Google Drive</a>`;
			frm.fields_dict.drive_link.$wrapper.find('.like-disabled-input, .control-value').html(link_html);
		}

		// Color-code confidence indicator
		if (!frm.is_new() && frm.doc.confidence != null) {
			let conf = frm.doc.confidence;
			let color, label;
			if (conf >= 80) {
				color = 'green';
				label = 'High';
			} else if (conf >= 50) {
				color = 'orange';
				label = 'Medium';
			} else {
				color = 'red';
				label = 'Low';
			}
			let badge = `<span class="indicator-pill whitespace-nowrap ${color}">${Math.round(conf)}% — ${label}</span>`;
			frm.fields_dict.confidence.$wrapper.find('.like-disabled-input, .control-value').html(badge);
		}

		// Subscribe to realtime updates for this document
		if (!frm.is_new() && ['Pending', 'Extracting', 'Processing'].includes(frm.doc.status)) {
			// Unbind existing handler to prevent duplicates
			frappe.realtime.off('ocr_extraction_progress');

			// Bind new handler
			frappe.realtime.on('ocr_extraction_progress', function(data) {
				if (data.ocr_import === frm.doc.name) {
					// Show alert
					frappe.show_alert({
						message: __(data.message || data.status),
						indicator: data.status === 'Error' ? 'red' : 'blue'
					});

					// Reload form when status changes
					if (!['Extracting', 'Processing'].includes(data.status)) {
						setTimeout(function() {
							frm.reload_doc();
						}, 1000);
					}
				}
			});
		}
	}
});

function poll_extraction_status(ocr_import_name) {
	let poll_count = 0;
	let max_polls = 60;  // 60 * 2s = 2 minutes max

	let interval = setInterval(function() {
		poll_count++;

		frappe.call({
			method: 'frappe.client.get_value',
			args: {
				doctype: 'OCR Import',
				filters: {name: ocr_import_name},
				fieldname: ['status']
			},
			callback: function(r) {
				if (r.message) {
					let status = r.message.status;

					// Stop polling if status is final or max polls reached
					if (!['Pending', 'Extracting', 'Processing'].includes(status) || poll_count >= max_polls) {
						clearInterval(interval);

						// Reload form
						if (cur_frm && cur_frm.doc.name === ocr_import_name) {
							cur_frm.reload_doc();
						}

						// Show final status message
						if (status === 'Error') {
							frappe.msgprint({
								title: __('Extraction Failed'),
								message: __('Please check the error log or retry the extraction.'),
								indicator: 'red'
							});
						} else if (status === 'Completed') {
							frappe.show_alert({
								message: __('Extraction complete! Purchase Invoice created.'),
								indicator: 'green'
							}, 5);
						} else if (status === 'Matched') {
							frappe.show_alert({
								message: __('Extraction complete! All items matched.'),
								indicator: 'green'
							}, 5);
						} else {
							frappe.show_alert({
								message: __('Extraction complete! Please review and confirm matches.'),
								indicator: 'orange'
							}, 5);
						}
					}
				}
			},
			error: function() {
				// Stop polling on error
				clearInterval(interval);
			}
		});
	}, 2000);  // Poll every 2 seconds
}
