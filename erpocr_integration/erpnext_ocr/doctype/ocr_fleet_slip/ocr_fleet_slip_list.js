// Copyright (c) 2025, ERPNext OCR Integration Contributors
// For license information, please see license.txt

// OCR Fleet Slip list view — bulk Mark Recorded (Q8, v1.8.0).
//
// The action is offered whenever rows are selected; the SERVER re-validates
// every row (status Matched + posting_mode Fleet Card + the single
// mark_recorded() guards) — the selection here is UI sugar, not the gate.
frappe.listview_settings['OCR Fleet Slip'] = {
	add_fields: ['status', 'posting_mode'],

	onload: function(listview) {
		listview.page.add_actions_menu_item(__('Mark Recorded (Fleet Card)'), function() {
			var checked = listview.get_checked_items();
			if (!checked.length) {
				frappe.msgprint(__('Select one or more Fleet Card slips first.'));
				return;
			}
			var names = checked.map(function(d) { return d.name; });

			frappe.confirm(
				__('Mark {0} slip(s) as Recorded? Only Matched Fleet Card slips will be completed; the rest are skipped with a reason.', [names.length]),
				function() {
					frappe.call({
						method: 'erpocr_integration.fleet_api.bulk_mark_recorded',
						args: { names: names },
						freeze: true,
						freeze_message: __('Marking slips as Recorded…'),
						callback: function(r) {
							if (!r.message) return;
							var recorded = r.message.recorded || [];
							var skipped = r.message.skipped || [];
							var html = __('{0} slip(s) marked Recorded.', [recorded.length]);
							if (skipped.length) {
								html += '<br><br>' + __('{0} skipped:', [skipped.length]) + '<ul>';
								skipped.forEach(function(s) {
									html += '<li><b>' + frappe.utils.escape_html(s.name) + '</b>: '
										+ frappe.utils.escape_html(s.reason) + '</li>';
								});
								html += '</ul>';
							}
							frappe.msgprint({
								title: __('Bulk Mark Recorded'),
								message: html,
								indicator: skipped.length ? 'orange' : 'green'
							});
							listview.refresh();
						}
					});
				}
			);
		}, false);
	}
};
