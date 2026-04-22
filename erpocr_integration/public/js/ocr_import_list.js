// List view actions for OCR Import — bulk operations on selected records.
//
// Bulk Delete is already provided by Frappe out of the box. These two actions
// fill the workflow gaps: resetting a batch of stale Draft Created records,
// and creating PI drafts for a batch of Matched records when auto-draft is
// off (or didn't trigger because matches were below the high-confidence bar).

frappe.listview_settings['OCR Import'] = {
	onload: function(listview) {
		// Bulk Create Purchase Invoice — for Matched / Needs Review records
		listview.page.add_action_item(__('Create Purchase Invoice'), function() {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__('Select one or more records first.'));
				return;
			}
			const eligible = selected.filter(d => ['Matched', 'Needs Review'].includes(d.status));
			const skipped = selected.length - eligible.length;
			if (!eligible.length) {
				frappe.msgprint(__('No selected records are in Matched or Needs Review status.'));
				return;
			}

			const msg = skipped
				? __('Create Purchase Invoice for {0} record(s)? ({1} skipped — wrong status).', [eligible.length, skipped])
				: __('Create Purchase Invoice for {0} record(s)?', [eligible.length]);

			frappe.confirm(msg, () => _bulk_create_pi(eligible, listview));
		});

		// Bulk Unlink & Reset — for Draft Created records whose draft is still safe to delete
		listview.page.add_action_item(__('Unlink & Reset Drafts'), function() {
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

			frappe.confirm(msg, () => _bulk_unlink(eligible, listview));
		});
	}
};

// Helper: iterate selected records, calling create_purchase_invoice on each.
// Reports successes and failures in a summary message at the end.
function _bulk_create_pi(records, listview) {
	const total = records.length;
	let done = 0, succeeded = 0;
	const errors = [];

	frappe.show_progress(__('Creating Purchase Invoices'), 0, total);

	function next() {
		if (done >= total) {
			frappe.hide_progress();
			_show_bulk_summary('Create Purchase Invoice', succeeded, errors);
			listview.refresh();
			return;
		}
		const rec = records[done];
		frappe.show_progress(__('Creating Purchase Invoices'), done, total, rec.name);

		// Document type must be set before create — set it via db_set then call create
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

function _bulk_unlink(records, listview) {
	const total = records.length;
	let done = 0, succeeded = 0;
	const errors = [];

	frappe.show_progress(__('Unlinking Drafts'), 0, total);

	function next() {
		if (done >= total) {
			frappe.hide_progress();
			_show_bulk_summary('Unlink & Reset', succeeded, errors);
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
		const lines = errors.map(e => `<li><a href="/app/ocr-import/${encodeURIComponent(e.name)}" target="_blank">${esc(e.name)}</a> — ${esc(e.error)}</li>`).join('');
		html += `<p>${__('{0} failed:', [errors.length])}</p><ul>${lines}</ul>`;
	}
	frappe.msgprint({
		title: __('{0} — Bulk Result', [action]),
		message: html,
		indicator: errors.length ? 'orange' : 'green'
	});
}
