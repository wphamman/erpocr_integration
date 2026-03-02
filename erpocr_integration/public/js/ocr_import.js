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

		// Filter credit account by company and usable accounts only
		frm.set_query('credit_account', function() {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter purchase_order by supplier, company, open statuses
		frm.set_query('purchase_order', function() {
			return {
				filters: {
					supplier: frm.doc.supplier,
					company: frm.doc.company,
					docstatus: 1,
					status: ['in', ['To Receive and Bill', 'To Receive', 'To Bill']]
				}
			};
		});

		// Filter purchase_receipt_link via server-side query (PO link is on child rows)
		frm.set_query('purchase_receipt_link', function() {
			return {
				query: 'erpocr_integration.api.purchase_receipt_link_query',
				filters: { purchase_order: frm.doc.purchase_order }
			};
		});
	},

	refresh: function(frm) {
		// Contextual intro message based on status (standard ERPNext pattern)
		set_status_intro(frm);

		// Check for potential duplicates (skip for new, Pending, Error records)
		check_and_show_duplicates(frm);

		// Add "Upload PDF" button for new records
		if (frm.is_new()) {
			frm.add_custom_button(__('Upload File'), function() {
				// Create file input element
				let input = document.createElement('input');
				input.type = 'file';
				input.accept = 'application/pdf, image/jpeg, image/png';

				input.onchange = function(e) {
					let file = e.target.files[0];
					if (!file) return;

					// Validate file size (10MB max)
					if (file.size > 10 * 1024 * 1024) {
						frappe.msgprint(__('File too large. Maximum size is 10MB.'));
						return;
					}

					// Validate file type
					var allowed_exts = ['.pdf', '.jpg', '.jpeg', '.png'];
					var file_ext = file.name.toLowerCase().substring(file.name.lastIndexOf('.'));
					if (!allowed_exts.includes(file_ext)) {
						frappe.msgprint(__('Unsupported file type. Accepted formats: PDF, JPEG, PNG.'));
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

									// Poll for status updates (frm not passed — realtime handler covers reload after navigate)
									poll_extraction_status(null, ocr_import_name);
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

		// Create dropdown — one click sets document_type, saves, and creates the document
		// No need for the user to find/set the Document Type field first
		if (!frm.is_new() && ['Matched', 'Needs Review'].includes(frm.doc.status)) {
			if (!frm.doc.purchase_invoice) {
				frm.add_custom_button(__('Purchase Invoice'), function() {
					create_document(frm, 'Purchase Invoice', 'create_purchase_invoice');
				}, __('Create'));
			}
			if (!frm.doc.purchase_receipt && frm.doc.status === 'Matched') {
				frm.add_custom_button(__('Purchase Receipt'), function() {
					create_document(frm, 'Purchase Receipt', 'create_purchase_receipt');
				}, __('Create'));
			}
			if (!frm.doc.journal_entry) {
				frm.add_custom_button(__('Journal Entry'), function() {
					create_document(frm, 'Journal Entry', 'create_journal_entry');
				}, __('Create'));
			}
		}

		// Unlink & Reset button — allows user to delete the draft and try a different document type
		if (!frm.is_new() && frm.doc.status === 'Draft Created') {
			frm.add_custom_button(__('Unlink & Reset'), function() {
				let linked = frm.doc.purchase_invoice || frm.doc.purchase_receipt || frm.doc.journal_entry;
				frappe.confirm(
					__('This will delete the draft document ({0}) and reset this record for re-use. Continue?', [linked]),
					function() {
						frappe.call({
							method: 'unlink_document',
							doc: frm.doc,
							callback: function(r) {
								if (!r.exc) {
									frm.reload_doc();
								}
							}
						});
					}
				);
			}, __('Actions'));
		}

		// PO linking buttons (only when supplier is set and in a reviewable state)
		if (!frm.is_new() && frm.doc.supplier && !['Completed', 'Draft Created', 'Error', 'Pending'].includes(frm.doc.status)) {
			// "Find Open POs" button
			if (!frm.doc.purchase_order) {
				frm.add_custom_button(__('Find Open POs'), function() {
					frappe.call({
						method: 'erpocr_integration.api.get_open_purchase_orders',
						args: {
							supplier: frm.doc.supplier,
							company: frm.doc.company
						},
						callback: function(r) {
							if (r.message && r.message.length) {
								show_po_selection_dialog(frm, r.message);
							} else {
								frappe.msgprint(__('No open Purchase Orders found for this supplier.'));
							}
						}
					});
				}, __('Purchase Order'));
			}

			// "Match PO Items" button (when PO is selected)
			if (frm.doc.purchase_order) {
				frm.add_custom_button(__('Match PO Items'), function() {
					frappe.call({
						method: 'erpocr_integration.api.match_po_items',
						args: {
							ocr_import: frm.doc.name,
							purchase_order: frm.doc.purchase_order
						},
						callback: function(r) {
							if (r.message) {
								show_po_match_dialog(frm, r.message);
							}
						}
					});
				}, __('Purchase Order'));
			}
		}

		// "No Action Required" button — for receipts, delivery notes, etc. that don't need PI/PR/JE
		if (!frm.is_new() && !['Completed', 'Draft Created', 'No Action', 'Pending'].includes(frm.doc.status)) {
			frm.add_custom_button(__('No Action Required'), function() {
				frappe.prompt(
					{
						fieldname: 'reason',
						fieldtype: 'Small Text',
						label: __('Reason'),
						reqd: 1,
						description: __('e.g., "Receipt for OCR-IMP-00025", "Delivery note — not an invoice", "Duplicate"')
					},
					function(values) {
						frappe.call({
							method: 'mark_no_action',
							doc: frm.doc,
							args: { reason: values.reason },
							callback: function(r) {
								if (!r.exc) {
									frm.reload_doc();
								}
							}
						});
					},
					__('Mark as No Action'),
					__('Confirm')
				);
			}, __('Actions'));
		}

		// Add retry button for failed extractions
		if (frm.doc.status === 'Error' && ['Gemini Manual Upload', 'Gemini Email', 'Gemini Drive Scan'].includes(frm.doc.source_type)) {
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
							poll_extraction_status(frm, frm.doc.name);
						}
					}
				});
			}, __('Actions'));
		}

		// Add "View Original Invoice" button and make Drive link clickable
		if (!frm.is_new() && frm.doc.drive_link && frm.doc.drive_link.startsWith('https://')) {
			frm.add_custom_button(__('View Original Invoice'), function() {
				window.open(frm.doc.drive_link, '_blank');
			}, __('Actions'));

			// Render drive_link as clickable HTML (sanitize to prevent XSS)
			let escaped_link = frappe.utils.escape_html(frm.doc.drive_link);
			let link_html = `<a href="${escaped_link}" target="_blank" rel="noopener noreferrer" style="word-break: break-all;">View in Google Drive</a>`;
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
	},

	document_type: function(frm) {
		// When switching to Journal Entry, auto-populate credit_account from OCR Settings
		if (frm.doc.document_type === 'Journal Entry' && !frm.doc.credit_account) {
			frappe.db.get_single_value('OCR Settings', 'default_credit_account')
				.then(value => {
					if (value) {
						frm.set_value('credit_account', value);
					}
				});
		}
	},

	// Stale field clearing: when supplier changes, always clear PO/PR and item-level refs
	supplier: function(frm) {
		frm.set_value('purchase_order', '');
		frm.set_value('purchase_receipt_link', '');
		clear_item_po_pr_fields(frm);
	},

	// When PO changes, clear PR link and item-level refs
	purchase_order: function(frm) {
		if (frm.doc.purchase_receipt_link) {
			frm.set_value('purchase_receipt_link', '');
		}
		clear_item_po_pr_fields(frm);
	},

	// When PR link changes, clear stale refs then auto-run PR matching
	purchase_receipt_link: function(frm) {
		clear_item_pr_fields(frm);

		// Skip auto-matching if the value was set from the PO dialog
		// (the dialog handles matching + save itself)
		if (frm._skip_pr_auto_match) {
			frm._skip_pr_auto_match = false;
			return;
		}

		if (frm.doc.purchase_receipt_link && frm.doc.purchase_order) {
			frappe.call({
				method: 'erpocr_integration.api.match_pr_items',
				args: {
					ocr_import: frm.doc.name,
					purchase_receipt: frm.doc.purchase_receipt_link
				},
				callback: function(r) {
					if (r.message && r.message.matches) {
						apply_pr_matches(frm, r.message.matches);
						frm.dirty();
						frm.save();
						frappe.show_alert({
							message: __('PR items matched.'),
							indicator: 'green'
						}, 3);
					}
				}
			});
		}
	}
});

function clear_item_po_pr_fields(frm) {
	let changed = false;
	(frm.doc.items || []).forEach(function(item) {
		if (item.purchase_order_item || item.po_qty || item.po_rate || item.pr_detail) {
			frappe.model.set_value(item.doctype, item.name, 'purchase_order_item', '');
			frappe.model.set_value(item.doctype, item.name, 'po_qty', 0);
			frappe.model.set_value(item.doctype, item.name, 'po_rate', 0);
			frappe.model.set_value(item.doctype, item.name, 'pr_detail', '');
			changed = true;
		}
	});
	if (changed) {
		frm.refresh_fields();
	}
}

function clear_item_pr_fields(frm) {
	let changed = false;
	(frm.doc.items || []).forEach(function(item) {
		if (item.pr_detail) {
			frappe.model.set_value(item.doctype, item.name, 'pr_detail', '');
			changed = true;
		}
	});
	if (changed) {
		frm.refresh_fields();
	}
}

function show_po_selection_dialog(frm, purchase_orders) {
	let fields = [
		{
			fieldtype: 'HTML',
			fieldname: 'po_list',
			options: build_po_list_html(purchase_orders)
		}
	];

	let d = new frappe.ui.Dialog({
		title: __('Open Purchase Orders'),
		fields: fields,
		size: 'large'
	});

	d.show();

	// Bind click handlers on PO rows
	d.$wrapper.find('.select-po-btn').on('click', function() {
		let po_name = $(this).data('po');
		frm.set_value('purchase_order', po_name);
		frm.dirty();
		d.hide();
		frappe.show_alert({
			message: __('Purchase Order {0} selected. Click "Match PO Items" to match.', [po_name]),
			indicator: 'blue'
		}, 5);
	});
}

function build_po_list_html(purchase_orders) {
	let esc = frappe.utils.escape_html;
	let rows = purchase_orders.map(function(po) {
		return `<tr>
			<td><a href="/app/purchase-order/${encodeURIComponent(po.name)}" target="_blank">${esc(po.name)}</a></td>
			<td>${esc(po.transaction_date)}</td>
			<td>${format_currency(po.grand_total)}</td>
			<td>${esc(po.status)}</td>
			<td><button class="btn btn-xs btn-primary select-po-btn" data-po="${esc(po.name)}">${__('Select')}</button></td>
		</tr>`;
	}).join('');

	return `<table class="table table-bordered table-hover">
		<thead><tr>
			<th>${__('PO #')}</th>
			<th>${__('Date')}</th>
			<th>${__('Total')}</th>
			<th>${__('Status')}</th>
			<th></th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>`;
}

function show_po_match_dialog(frm, data) {
	let match_html = build_match_results_html(data.matches, data.unmatched_po);
	let pr_html = '';

	if (data.purchase_receipts && data.purchase_receipts.length) {
		pr_html = '<hr><h5>' + __('Purchase Receipts against this PO') + '</h5>' +
			build_pr_list_html(data.purchase_receipts);
	}

	let d = new frappe.ui.Dialog({
		title: __('Match PO Items'),
		fields: [
			{
				fieldtype: 'HTML',
				fieldname: 'match_results',
				options: match_html + pr_html
			}
		],
		size: 'extra-large',
		primary_action_label: __('Apply Matches'),
		primary_action: function() {
			apply_po_matches(frm, data.matches);

			// If a PR was selected in the dialog, set it
			let selected_pr = d.$wrapper.find('.select-pr-btn.btn-success').data('pr');
			if (selected_pr) {
				// Set flag to prevent the field handler from also running match_pr_items
				frm._skip_pr_auto_match = true;
				frm.set_value('purchase_receipt_link', selected_pr);
				// Match PR items too
				frappe.call({
					method: 'erpocr_integration.api.match_pr_items',
					args: {
						ocr_import: frm.doc.name,
						purchase_receipt: selected_pr
					},
					callback: function(r) {
						if (r.message) {
							apply_pr_matches(frm, r.message.matches);
						}
						frm.dirty();
						frm.save();
					}
				});
			} else {
				frm.dirty();
				frm.save();
			}

			d.hide();
			frappe.show_alert({
				message: __('PO item matches applied.'),
				indicator: 'green'
			}, 5);
		}
	});

	d.show();

	// PR selection toggle
	d.$wrapper.find('.select-pr-btn').on('click', function() {
		d.$wrapper.find('.select-pr-btn').removeClass('btn-success').addClass('btn-default');
		$(this).removeClass('btn-default').addClass('btn-success');
	});
}

function build_match_results_html(matches, unmatched_po) {
	let esc = frappe.utils.escape_html;
	let rows = matches.map(function(m) {
		let badge, po_info;
		if (m.match) {
			badge = '<span class="indicator-pill green">Matched</span>';
			po_info = `${esc(m.match.po_item_code)} — Qty: ${m.match.po_qty}, Rate: ${format_currency(m.match.po_rate)}`;
		} else {
			badge = '<span class="indicator-pill orange">Unmatched</span>';
			po_info = '—';
		}
		return `<tr>
			<td>${m.idx}</td>
			<td>${esc(m.description_ocr || '')}</td>
			<td>${esc(m.item_code || '—')}</td>
			<td>Qty: ${m.qty || 0}, Rate: ${format_currency(m.rate || 0)}</td>
			<td>${po_info}</td>
			<td>${badge}</td>
		</tr>`;
	}).join('');

	let unmatched_rows = '';
	if (unmatched_po && unmatched_po.length) {
		unmatched_rows = '<h5 class="mt-3">' + __('Unmatched PO Items') + '</h5>' +
			'<table class="table table-bordered"><thead><tr>' +
			'<th>' + __('Item Code') + '</th><th>' + __('Item Name') + '</th>' +
			'<th>' + __('Qty') + '</th><th>' + __('Rate') + '</th></tr></thead><tbody>' +
			unmatched_po.map(function(p) {
				return `<tr><td>${frappe.utils.escape_html(p.item_code)}</td><td>${frappe.utils.escape_html(p.item_name)}</td><td>${p.qty}</td><td>${format_currency(p.rate)}</td></tr>`;
			}).join('') + '</tbody></table>';
	}

	return `<table class="table table-bordered">
		<thead><tr>
			<th>#</th>
			<th>${__('OCR Description')}</th>
			<th>${__('Matched Item')}</th>
			<th>${__('OCR Qty/Rate')}</th>
			<th>${__('PO Item')}</th>
			<th>${__('Status')}</th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>` + unmatched_rows;
}

function build_pr_list_html(purchase_receipts) {
	let esc = frappe.utils.escape_html;
	let rows = purchase_receipts.map(function(pr) {
		return `<tr>
			<td><a href="/app/purchase-receipt/${encodeURIComponent(pr.name)}" target="_blank">${esc(pr.name)}</a></td>
			<td>${esc(pr.posting_date)}</td>
			<td>${esc(pr.status)}</td>
			<td><button class="btn btn-xs btn-default select-pr-btn" data-pr="${esc(pr.name)}">${__('Select')}</button></td>
		</tr>`;
	}).join('');

	return `<table class="table table-bordered table-hover">
		<thead><tr>
			<th>${__('PR #')}</th>
			<th>${__('Date')}</th>
			<th>${__('Status')}</th>
			<th></th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>`;
}

function apply_po_matches(frm, matches) {
	matches.forEach(function(m) {
		if (m.match) {
			let item = frm.doc.items[m.idx - 1];
			if (item) {
				frappe.model.set_value(item.doctype, item.name, 'purchase_order_item', m.match.purchase_order_item);
				frappe.model.set_value(item.doctype, item.name, 'po_qty', m.match.po_qty);
				frappe.model.set_value(item.doctype, item.name, 'po_rate', m.match.po_rate);
			}
		}
	});
	frm.refresh_fields();
}

function apply_pr_matches(frm, matches) {
	matches.forEach(function(m) {
		if (m.match) {
			let item = frm.doc.items[m.idx - 1];
			if (item) {
				frappe.model.set_value(item.doctype, item.name, 'pr_detail', m.match.pr_detail);
			}
		}
	});
	frm.refresh_fields();
}

function poll_extraction_status(frm, ocr_import_name) {
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

						// Reload form (fall back to open form when frm was not passed)
						let active_frm = frm || frappe.ui.form.get_open_form();
						if (active_frm && active_frm.doc && active_frm.doc.name === ocr_import_name) {
							active_frm.reload_doc();
						}

						// Show final status message
						if (status === 'Error') {
							frappe.msgprint({
								title: __('Extraction Failed'),
								message: __('Please check the error log or retry the extraction.'),
								indicator: 'red'
							});
						} else if (status === 'Matched') {
							frappe.show_alert({
								message: __('Extraction complete! All items matched. Use the Create menu to create a document.'),
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

function set_status_intro(frm) {
	frm.set_intro('');
	if (frm.is_new()) return;

	const doc = frm.doc;

	if (['Pending', 'Extracting', 'Processing'].includes(doc.status)) {
		frm.set_intro(__('Extracting data from invoice... Please wait.'), 'blue');
	} else if (doc.status === 'Error') {
		frm.set_intro(__('Extraction failed. Check the Error Log or click Retry Extraction.'), 'red');
	} else if (doc.status === 'Needs Review') {
		frm.set_intro(__('Review the extracted data below. Confirm or correct supplier and item matches, then use the Create menu.'), 'orange');
	} else if (doc.status === 'Matched') {
		frm.set_intro(__('All items matched. Use the <b>Create</b> dropdown to create a Purchase Invoice, Purchase Receipt, or Journal Entry.'), 'blue');
	} else if (doc.status === 'Draft Created') {
		// Show link to created draft + hint about Unlink
		let link = '';
		if (doc.purchase_invoice) {
			link = `<a href="/app/purchase-invoice/${encodeURIComponent(doc.purchase_invoice)}">${frappe.utils.escape_html(doc.purchase_invoice)}</a>`;
			frm.set_intro(__('Draft Purchase Invoice {0} created. Submit it to complete, or use Actions > Unlink & Reset to start over.', [link]), 'blue');
		} else if (doc.purchase_receipt) {
			link = `<a href="/app/purchase-receipt/${encodeURIComponent(doc.purchase_receipt)}">${frappe.utils.escape_html(doc.purchase_receipt)}</a>`;
			frm.set_intro(__('Draft Purchase Receipt {0} created. Submit it to complete, or use Actions > Unlink & Reset to start over.', [link]), 'blue');
		} else if (doc.journal_entry) {
			link = `<a href="/app/journal-entry/${encodeURIComponent(doc.journal_entry)}">${frappe.utils.escape_html(doc.journal_entry)}</a>`;
			frm.set_intro(__('Draft Journal Entry {0} created. Submit it to complete, or use Actions > Unlink & Reset to start over.', [link]), 'blue');
		} else {
			frm.set_intro(__('Draft created.'), 'blue');
		}
	} else if (doc.status === 'No Action') {
		frm.set_intro(__('No Action Required: {0}', [frappe.utils.escape_html(doc.no_action_reason || '')]), 'grey');
	} else if (doc.status === 'Completed') {
		// Show link to submitted document
		let link = '';
		if (doc.purchase_invoice) {
			link = `<a href="/app/purchase-invoice/${encodeURIComponent(doc.purchase_invoice)}">${frappe.utils.escape_html(doc.purchase_invoice)}</a>`;
			frm.set_intro(__('Purchase Invoice {0} submitted.', [link]), 'green');
		} else if (doc.purchase_receipt) {
			link = `<a href="/app/purchase-receipt/${encodeURIComponent(doc.purchase_receipt)}">${frappe.utils.escape_html(doc.purchase_receipt)}</a>`;
			frm.set_intro(__('Purchase Receipt {0} submitted.', [link]), 'green');
		} else if (doc.journal_entry) {
			link = `<a href="/app/journal-entry/${encodeURIComponent(doc.journal_entry)}">${frappe.utils.escape_html(doc.journal_entry)}</a>`;
			frm.set_intro(__('Journal Entry {0} submitted.', [link]), 'green');
		} else {
			frm.set_intro(__('Completed.'), 'green');
		}
	}
}

function create_document(frm, doc_type, method_name) {
	// Set document_type, save, then call the create method — all in one click.
	// If document_type is already correct and form is clean, skip save to avoid
	// "No changes in document" blocking the create call.
	function call_create() {
		frappe.call({
			method: method_name,
			doc: frm.doc,
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
					frappe.show_alert({
						message: __('{0} draft created.', [doc_type]),
						indicator: 'green'
					}, 5);
				}
			}
		});
	}

	function do_create() {
		if (frm.doc.document_type === doc_type && !frm.is_dirty()) {
			call_create();
		} else {
			frm.set_value('document_type', doc_type);
			frm.save().then(() => {
				call_create();
			});
		}
	}

	// Check for duplicates before creating — warn user if potential dupes exist
	frappe.call({
		method: 'erpocr_integration.api.check_duplicates',
		args: { ocr_import: frm.doc.name },
		callback: function(r) {
			if (r.message && r.message.length) {
				let esc = frappe.utils.escape_html;
				let lines = r.message.map(function(d) {
					let link = `/app/ocr-import/${encodeURIComponent(d.name)}`;
					return `<li><a href="${link}" target="_blank">${esc(d.name)}</a> — ${esc(d.status)} (${esc(d.match_reason)})</li>`;
				}).join('');
				frappe.confirm(
					__('Potential duplicates found:') + `<ul>${lines}</ul>` +
					__('Do you still want to create a {0}?', [doc_type]),
					function() { do_create(); }
				);
			} else {
				do_create();
			}
		}
	});
}

function check_and_show_duplicates(frm) {
	if (frm.is_new()) return;
	if (['Pending', 'Extracting', 'Processing', 'Error', 'No Action'].includes(frm.doc.status)) return;

	frappe.call({
		method: 'erpocr_integration.api.check_duplicates',
		args: { ocr_import: frm.doc.name },
		async: true,
		callback: function(r) {
			// Remove any previous duplicate banner
			frm.$wrapper.find('.ocr-duplicate-warning').remove();

			if (r.message && r.message.length) {
				let esc = frappe.utils.escape_html;
				let lines = r.message.map(function(d) {
					let link = `/app/ocr-import/${encodeURIComponent(d.name)}`;
					return `<a href="${link}">${esc(d.name)}</a> (${esc(d.status)} — ${esc(d.match_reason)})`;
				}).join(', ');
				let html = `<div class="ocr-duplicate-warning form-message orange">
					<div>${__('Possible duplicate')}:  ${lines}</div>
				</div>`;
				frm.$wrapper.find('.form-message').first().after(html);
			}
		}
	});
}
