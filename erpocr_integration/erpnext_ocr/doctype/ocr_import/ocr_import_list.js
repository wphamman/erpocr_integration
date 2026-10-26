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
			'No Action':     [__('No Action'), 'grey', 'status'],
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
	},

	// ------------------------------------------------------------------
	// Bulk actions — fill workflow gaps left by the standard Actions menu.
	// Bulk Delete is built into Frappe; these two actions cover:
	//   * Create Purchase Invoice for a batch of Matched records when
	//     auto-draft is off or didn't trigger (low-confidence matches).
	//   * Unlink & Reset a batch of stale Draft Created records.
	// Both reuse the whitelisted per-doc methods, so status guards,
	// permission checks, and row locks are inherited unchanged.
	// ------------------------------------------------------------------
	onload: function(listview) {
		// Red banner while Google is refusing payment for the AI service (v1.14.0, Q23).
		// Server returns only {down, since}; cleared by the next successful read.
		frappe.call({
			method: 'erpocr_integration.tasks.billing_alert.get_billing_status',
			type: 'GET',
			callback: function(r) {
				const status = r.message || {};
				listview.page.main.find('.ocr-billing-banner').remove();
				if (!status.down) return;
				const since = status.since ? frappe.datetime.str_to_user(status.since) : '';
				const banner = $('<div class="ocr-billing-banner alert alert-danger" style="margin-bottom: 10px;"></div>');
				banner.text(__('OCR is stopped: Google refused payment for the AI service since {0}. Ask Willie to top up the billing account. New uploads will fail until then.', [since]));
				listview.page.main.prepend(banner);
			}
		});

		listview.page.add_action_item(__('Create Purchase Invoice'), function() {
			_bulk_create_pi(listview);
		});

		listview.page.add_action_item(__('Unlink & Reset Drafts'), function() {
			_bulk_unlink(listview);
		});
	}
};

function _bulk_create_pi(listview) {
	const selected = listview.get_checked_items();
	if (!selected.length) {
		frappe.msgprint(__('Select one or more records first.'));
		return;
	}
	const eligible = selected.filter(d => ['Matched', 'Needs Review'].includes(d.status));
	const skipped_status = selected.length - eligible.length;
	if (!eligible.length) {
		frappe.msgprint(__('No selected records are in Matched or Needs Review status.'));
		return;
	}

	// Pre-flight duplicate check across all eligible records so we can warn
	// the user up front — the per-record form flow already does this, and
	// bypassing it for bulk would quietly create duplicate PI drafts.
	frappe.show_progress(__('Checking for duplicates'), 0, eligible.length);
	const duplicate_map = {};  // record name → list of duplicate rows
	let checked = 0;

	function check_next() {
		if (checked >= eligible.length) {
			frappe.hide_progress();
			_confirm_and_run_bulk_pi(eligible, duplicate_map, skipped_status, listview);
			return;
		}
		const rec = eligible[checked];
		frappe.show_progress(__('Checking for duplicates'), checked, eligible.length, rec.name);

		frappe.call({
			method: 'erpocr_integration.api.check_duplicates',
			args: { ocr_import: rec.name }
		}).then(r => {
			if (r.message && r.message.length) {
				duplicate_map[rec.name] = r.message;
			}
		}).finally(() => { checked += 1; check_next(); });
	}
	check_next();
}

function _confirm_and_run_bulk_pi(eligible, duplicate_map, skipped_status, listview) {
	const esc = frappe.utils.escape_html;
	const dup_names = Object.keys(duplicate_map);
	let intro = '';
	if (dup_names.length) {
		const lines = dup_names.map(n => {
			const dups = duplicate_map[n].map(d =>
				`${esc((d.doctype || 'OCR Import'))} ${esc(d.name)} (${esc(d.match_reason)})`
			).join('; ');
			return `<li><a href="/app/ocr-import/${encodeURIComponent(n)}" target="_blank">${esc(n)}</a> — ${lines}</li>`;
		}).join('');
		intro = `<p><b>${__('Duplicates detected on {0} record(s):', [dup_names.length])}</b></p><ul>${lines}</ul>
			<p>${__('Records with duplicates will be skipped. Review them individually from the form view.')}</p>`;
	}

	const to_create = eligible.filter(r => !(r.name in duplicate_map));
	if (!to_create.length) {
		frappe.msgprint({
			title: __('Nothing to create'),
			message: intro + `<p>${__('Every selected record has a potential duplicate. Nothing will be created.')}</p>`,
			indicator: 'orange'
		});
		return;
	}

	const msg_parts = [];
	if (skipped_status) msg_parts.push(__('{0} record(s) skipped — wrong status.', [skipped_status]));
	if (dup_names.length) msg_parts.push(__('{0} record(s) skipped — duplicate exists.', [dup_names.length]));
	const msg = `${intro}<p>${__('Create Purchase Invoice for {0} record(s)?', [to_create.length])}</p>
		${msg_parts.length ? `<p>${msg_parts.join(' ')}</p>` : ''}`;

	frappe.confirm(msg, () => _run_bulk_create_pi(to_create, duplicate_map, listview));
}

function _run_bulk_create_pi(records, duplicate_map, listview) {
	const total = records.length;
	let done = 0, succeeded = 0;
	const errors = [];

	// Seed errors with duplicate-skipped records so they appear in the summary
	for (const name of Object.keys(duplicate_map)) {
		errors.push({ name: name, error: __('Skipped: duplicate already exists') });
	}

	frappe.show_progress(__('Creating Purchase Invoices'), 0, total);

	function next() {
		if (done >= total) {
			frappe.hide_progress();
			_show_bulk_summary(__('Create Purchase Invoice'), succeeded, errors);
			listview.refresh();
			return;
		}
		const rec = records[done];
		frappe.show_progress(__('Creating Purchase Invoices'), done, total, rec.name);

		frappe.db.set_value('OCR Import', rec.name, 'document_type', 'Purchase Invoice')
			.then(() => frappe.call({
				method: 'frappe.client.run_doc_method',
				args: { method: 'create_purchase_invoice', dt: 'OCR Import', dn: rec.name }
			}))
			.then(() => { succeeded += 1; })
			.catch(err => { errors.push({ name: rec.name, error: err.message || 'Unknown error' }); })
			.finally(() => { done += 1; next(); });
	}
	next();
}

function _bulk_unlink(listview) {
	const selected = listview.get_checked_items();
	if (!selected.length) {
		frappe.msgprint(__('Select one or more records first.'));
		return;
	}
	const eligible = selected.filter(d => d.status === 'Draft Created');
	const skipped = selected.length - eligible.length;
	if (!eligible.length) {
		frappe.msgprint(__('No selected records are in Draft Created status.'));
		return;
	}

	const msg = skipped
		? __('Unlink & Reset {0} record(s)? ({1} skipped — wrong status). The draft documents will be deleted if still in draft state.', [eligible.length, skipped])
		: __('Unlink & Reset {0} record(s)? The draft documents will be deleted if still in draft state.', [eligible.length]);

	frappe.confirm(msg, () => _run_bulk_unlink(eligible, listview));
}

function _run_bulk_unlink(records, listview) {
	const total = records.length;
	let done = 0, succeeded = 0;
	const errors = [];

	frappe.show_progress(__('Unlinking Drafts'), 0, total);

	function next() {
		if (done >= total) {
			frappe.hide_progress();
			_show_bulk_summary(__('Unlink & Reset'), succeeded, errors);
			listview.refresh();
			return;
		}
		const rec = records[done];
		frappe.show_progress(__('Unlinking Drafts'), done, total, rec.name);

		frappe.call({
			method: 'frappe.client.run_doc_method',
			args: { method: 'unlink_document', dt: 'OCR Import', dn: rec.name }
		})
			.then(() => { succeeded += 1; })
			.catch(err => { errors.push({ name: rec.name, error: err.message || 'Unknown error' }); })
			.finally(() => { done += 1; next(); });
	}
	next();
}

function _show_bulk_summary(action, succeeded, errors) {
	const esc = frappe.utils.escape_html;
	let html = `<p>${__('{0} succeeded: {1}', [esc(action), succeeded])}</p>`;
	if (errors.length) {
		const lines = errors.map(e =>
			`<li><a href="/app/ocr-import/${encodeURIComponent(e.name)}" target="_blank">${esc(e.name)}</a> — ${esc(e.error)}</li>`
		).join('');
		html += `<p>${__('{0} failed or skipped:', [errors.length])}</p><ul>${lines}</ul>`;
	}
	frappe.msgprint({
		title: __('{0} — Bulk Result', [action]),
		message: html,
		indicator: errors.length ? 'orange' : 'green'
	});
}
