// Copyright (c) 2025, ERPNext OCR Integration Contributors
// For license information, please see license.txt

frappe.ui.form.on('OCR Delivery Note', {
	setup: function(frm) {
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
	},

	refresh: function(frm) {
		dn_set_status_intro(frm);

		// Create dropdown — sets document_type, saves, and creates document
		if (!frm.is_new() && ['Matched', 'Needs Review'].includes(frm.doc.status)) {
			if (!frm.doc.purchase_order_result) {
				frm.add_custom_button(__('Purchase Order'), function() {
					dn_create_document(frm, 'Purchase Order', 'create_purchase_order');
				}, __('Create'));
			}
			if (!frm.doc.purchase_receipt) {
				frm.add_custom_button(__('Purchase Receipt'), function() {
					dn_create_document(frm, 'Purchase Receipt', 'create_purchase_receipt');
				}, __('Create'));
			}
		}

		// Unlink & Reset button
		if (!frm.is_new() && frm.doc.status === 'Draft Created') {
			frm.add_custom_button(__('Unlink & Reset'), function() {
				let linked = frm.doc.purchase_order_result || frm.doc.purchase_receipt;
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
		if (!frm.is_new() && frm.doc.supplier && !['Completed', 'Draft Created', 'Error', 'Pending', 'No Action'].includes(frm.doc.status)) {
			// "Find Open POs" button
			if (!frm.doc.purchase_order) {
				frm.add_custom_button(__('Find Open POs'), function() {
					frappe.call({
						method: 'erpocr_integration.dn_api.get_open_purchase_orders_for_dn',
						args: {
							supplier: frm.doc.supplier,
							company: frm.doc.company
						},
						callback: function(r) {
							if (r.message && r.message.length) {
								dn_show_po_selection_dialog(frm, r.message);
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
						method: 'erpocr_integration.dn_api.match_dn_po_items',
						args: {
							ocr_dn: frm.doc.name,
							purchase_order: frm.doc.purchase_order
						},
						callback: function(r) {
							if (r.message) {
								dn_show_po_match_dialog(frm, r.message);
							}
						}
					});
				}, __('Purchase Order'));
			}
		}

		// "No Action Required" button
		if (!frm.is_new() && !['Completed', 'Draft Created', 'No Action', 'Pending'].includes(frm.doc.status)) {
			frm.add_custom_button(__('No Action Required'), function() {
				frappe.prompt(
					{
						fieldname: 'reason',
						fieldtype: 'Small Text',
						label: __('Reason'),
						reqd: 1,
						description: __('e.g., "Packing slip — not a delivery note", "Duplicate scan"')
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

		// Retry button for failed extractions
		if (frm.doc.status === 'Error') {
			frm.add_custom_button(__('Retry Extraction'), function() {
				frappe.call({
					method: 'erpocr_integration.dn_api.retry_dn_extraction',
					args: { ocr_dn: frm.doc.name },
					callback: function(r) {
						if (!r.exc) {
							frm.reload_doc();
							frappe.show_alert({
								message: __('Retrying extraction...'),
								indicator: 'blue'
							});
						}
					}
				});
			}, __('Actions'));
		}

		// Make Drive link clickable
		if (!frm.is_new() && frm.doc.drive_link && frm.doc.drive_link.startsWith('https://')) {
			frm.add_custom_button(__('View Original Scan'), function() {
				window.open(frm.doc.drive_link, '_blank');
			}, __('Actions'));

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
	},

	// Stale field clearing: when supplier changes, clear PO and item-level refs
	supplier: function(frm) {
		frm.set_value('purchase_order', '');
		dn_clear_item_po_fields(frm);
	},

	// When PO changes, clear item-level PO refs
	purchase_order: function(frm) {
		dn_clear_item_po_fields(frm);
	}
});

function dn_clear_item_po_fields(frm) {
	let changed = false;
	(frm.doc.items || []).forEach(function(item) {
		if (item.purchase_order_item || item.po_qty || item.po_remaining_qty) {
			frappe.model.set_value(item.doctype, item.name, 'purchase_order_item', '');
			frappe.model.set_value(item.doctype, item.name, 'po_qty', 0);
			frappe.model.set_value(item.doctype, item.name, 'po_remaining_qty', 0);
			changed = true;
		}
	});
	if (changed) {
		frm.refresh_fields();
	}
}

function dn_show_po_selection_dialog(frm, purchase_orders) {
	let esc = frappe.utils.escape_html;
	let rows = purchase_orders.map(function(po) {
		return `<tr>
			<td><a href="/app/purchase-order/${encodeURIComponent(po.name)}" target="_blank">${esc(po.name)}</a></td>
			<td>${esc(po.transaction_date)}</td>
			<td>${format_currency(po.grand_total)}</td>
			<td>${esc(po.status)}</td>
			<td><button class="btn btn-xs btn-primary dn-select-po-btn" data-po="${esc(po.name)}">${__('Select')}</button></td>
		</tr>`;
	}).join('');

	let html = `<table class="table table-bordered table-hover">
		<thead><tr>
			<th>${__('PO #')}</th>
			<th>${__('Date')}</th>
			<th>${__('Total')}</th>
			<th>${__('Status')}</th>
			<th></th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>`;

	let d = new frappe.ui.Dialog({
		title: __('Open Purchase Orders'),
		fields: [{ fieldtype: 'HTML', fieldname: 'po_list', options: html }],
		size: 'large'
	});
	d.show();

	d.$wrapper.find('.dn-select-po-btn').on('click', function() {
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

function dn_show_po_match_dialog(frm, data) {
	let esc = frappe.utils.escape_html;

	// Build match results — qty-focused (no rate column for DN)
	let rows = data.matches.map(function(m) {
		let badge, po_info;
		if (m.match) {
			badge = '<span class="indicator-pill green">Matched</span>';
			let remaining = m.match.po_remaining_qty;
			let dn_qty = m.qty || 0;
			let qty_class = dn_qty > remaining ? 'text-danger' : '';
			po_info = `${esc(m.match.po_item_code)} — PO Qty: ${m.match.po_qty}, Remaining: <span class="${qty_class}">${remaining}</span>`;
		} else {
			badge = '<span class="indicator-pill orange">Unmatched</span>';
			po_info = '—';
		}
		return `<tr>
			<td>${m.idx}</td>
			<td>${esc(m.description_ocr || '')}</td>
			<td>${esc(m.item_code || '—')}</td>
			<td>${m.qty || 0}</td>
			<td>${po_info}</td>
			<td>${badge}</td>
		</tr>`;
	}).join('');

	let unmatched_html = '';
	if (data.unmatched_po && data.unmatched_po.length) {
		let unmatched_rows = data.unmatched_po.map(function(p) {
			return `<tr>
				<td>${esc(p.item_code)}</td>
				<td>${esc(p.item_name)}</td>
				<td>${p.qty}</td>
				<td>${p.remaining_qty}</td>
			</tr>`;
		}).join('');
		unmatched_html = `<h5 class="mt-3">${__('Unmatched PO Items')}</h5>
			<table class="table table-bordered"><thead><tr>
				<th>${__('Item Code')}</th><th>${__('Item Name')}</th>
				<th>${__('PO Qty')}</th><th>${__('Remaining')}</th>
			</tr></thead><tbody>${unmatched_rows}</tbody></table>`;
	}

	let match_html = `<table class="table table-bordered">
		<thead><tr>
			<th>#</th>
			<th>${__('OCR Description')}</th>
			<th>${__('Matched Item')}</th>
			<th>${__('DN Qty')}</th>
			<th>${__('PO Item')}</th>
			<th>${__('Status')}</th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>` + unmatched_html;

	let d = new frappe.ui.Dialog({
		title: __('Match PO Items'),
		fields: [{
			fieldtype: 'HTML',
			fieldname: 'match_results',
			options: match_html
		}],
		size: 'extra-large',
		primary_action_label: __('Apply Matches'),
		primary_action: function() {
			dn_apply_po_matches(frm, data.matches);
			frm.dirty();
			frm.save();
			d.hide();
			frappe.show_alert({
				message: __('PO item matches applied.'),
				indicator: 'green'
			}, 5);
		}
	});
	d.show();
}

function dn_apply_po_matches(frm, matches) {
	matches.forEach(function(m) {
		if (m.match) {
			let item = frm.doc.items[m.idx - 1];
			if (item) {
				frappe.model.set_value(item.doctype, item.name, 'purchase_order_item', m.match.purchase_order_item);
				frappe.model.set_value(item.doctype, item.name, 'po_qty', m.match.po_qty);
				frappe.model.set_value(item.doctype, item.name, 'po_remaining_qty', m.match.po_remaining_qty);
			}
		}
	});
	frm.refresh_fields();
}

function dn_create_document(frm, doc_type, method_name) {
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

	if (frm.doc.document_type === doc_type && !frm.is_dirty()) {
		call_create();
	} else {
		frm.set_value('document_type', doc_type);
		frm.save().then(() => {
			call_create();
		});
	}
}

function dn_set_status_intro(frm) {
	frm.set_intro('');
	if (frm.is_new()) return;

	const doc = frm.doc;

	if (doc.status === 'Pending') {
		frm.set_intro(__('Extracting data from delivery note scan... Please wait.'), 'blue');
	} else if (doc.status === 'Error') {
		frm.set_intro(__('Extraction failed. Check the Error Log or click Retry Extraction.'), 'red');
	} else if (doc.status === 'Needs Review') {
		frm.set_intro(__('Review the extracted data below. Confirm or correct supplier and item matches, then use the Create menu.'), 'orange');
	} else if (doc.status === 'Matched') {
		frm.set_intro(__('All items matched. Use the <b>Create</b> dropdown to create a Purchase Order or Purchase Receipt.'), 'blue');
	} else if (doc.status === 'Draft Created') {
		let link = '';
		if (doc.purchase_order_result) {
			link = `<a href="/app/purchase-order/${encodeURIComponent(doc.purchase_order_result)}">${frappe.utils.escape_html(doc.purchase_order_result)}</a>`;
			frm.set_intro(__('Draft Purchase Order {0} created. Submit it when ready, or use Actions > Unlink & Reset to start over.', [link]), 'blue');
		} else if (doc.purchase_receipt) {
			link = `<a href="/app/purchase-receipt/${encodeURIComponent(doc.purchase_receipt)}">${frappe.utils.escape_html(doc.purchase_receipt)}</a>`;
			frm.set_intro(__('Draft Purchase Receipt {0} created. Submit it when ready, or use Actions > Unlink & Reset to start over.', [link]), 'blue');
		} else {
			frm.set_intro(__('Draft created.'), 'blue');
		}
	} else if (doc.status === 'No Action') {
		frm.set_intro(__('No Action Required: {0}', [frappe.utils.escape_html(doc.no_action_reason || '')]), 'grey');
	} else if (doc.status === 'Completed') {
		let link = '';
		if (doc.purchase_order_result) {
			link = `<a href="/app/purchase-order/${encodeURIComponent(doc.purchase_order_result)}">${frappe.utils.escape_html(doc.purchase_order_result)}</a>`;
			frm.set_intro(__('Purchase Order {0} submitted.', [link]), 'green');
		} else if (doc.purchase_receipt) {
			link = `<a href="/app/purchase-receipt/${encodeURIComponent(doc.purchase_receipt)}">${frappe.utils.escape_html(doc.purchase_receipt)}</a>`;
			frm.set_intro(__('Purchase Receipt {0} submitted.', [link]), 'green');
		} else {
			frm.set_intro(__('Completed.'), 'green');
		}
	}
}
