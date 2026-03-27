frappe.ui.form.on('OCR Statement', {
    refresh: function(frm) {
        // Summary intro
        if (frm.doc.status === 'Reconciled' && frm.doc.total_lines) {
            let parts = [];
            if (frm.doc.matched_count) parts.push(frm.doc.matched_count + ' matched');
            if (frm.doc.mismatch_count) parts.push('<span style="color:var(--orange-500)">' + frm.doc.mismatch_count + ' mismatches</span>');
            if (frm.doc.missing_count) parts.push('<span style="color:var(--red-500)">' + frm.doc.missing_count + ' missing</span>');
            if (frm.doc.not_in_statement_count) parts.push('<span style="color:var(--red-500)">' + frm.doc.not_in_statement_count + ' not in statement</span>');
            if (frm.doc.payment_count) parts.push(frm.doc.payment_count + ' payments');

            let has_issues = frm.doc.mismatch_count || frm.doc.missing_count || frm.doc.not_in_statement_count;
            frm.set_intro(frm.doc.total_lines + ' lines: ' + parts.join(', '), has_issues ? 'orange' : 'green');
        }

        if (frm.doc.reverse_check_skipped) {
            frm.set_intro('Reverse check skipped — statement period dates missing. PIs not on the statement are not shown.', 'yellow');
        }

        // Mark Reviewed button
        if (frm.doc.status === 'Reconciled') {
            frm.add_custom_button(__('Mark Reviewed'), function() {
                frappe.call({
                    method: 'mark_reviewed',
                    doc: frm.doc,
                    callback: function() { frm.reload_doc(); }
                });
            }, __('Actions'));
        }

        // Re-reconcile button
        if (frm.doc.supplier && frm.doc.status !== 'Error') {
            frm.add_custom_button(__('Re-Reconcile'), function() {
                frappe.call({
                    method: 'erpocr_integration.statement_api.rereconcile_statement',
                    args: { statement_name: frm.doc.name },
                    callback: function() {
                        frm.reload_doc();
                        frappe.show_alert({message: __('Reconciliation updated.'), indicator: 'green'});
                    }
                });
            });
        }
    }
});
