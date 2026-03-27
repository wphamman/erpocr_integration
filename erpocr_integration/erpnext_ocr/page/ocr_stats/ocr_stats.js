frappe.pages['ocr-stats'].on_page_load = function(wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'OCR Processing Stats',
		single_column: true
	});

	page.main.html(frappe.render_template('ocr_stats'));

	page.add_field({
		fieldname: 'from_date',
		label: __('From'),
		fieldtype: 'Date',
		default: frappe.datetime.add_days(frappe.datetime.nowdate(), -90),
		change: function() { load_stats(page); }
	});

	page.add_field({
		fieldname: 'to_date',
		label: __('To'),
		fieldtype: 'Date',
		default: frappe.datetime.nowdate(),
		change: function() { load_stats(page); }
	});

	load_stats(page);
};

function load_stats(page) {
	let from_date = page.fields_dict.from_date.get_value();
	let to_date = page.fields_dict.to_date.get_value();

	frappe.call({
		method: 'erpocr_integration.stats_api.get_ocr_stats',
		args: { from_date: from_date, to_date: to_date },
		callback: function(r) {
			if (r.message) {
				render_stats(page, r.message);
			}
		}
	});
}

function render_stats(page, stats) {
	let $main = page.main;

	$main.find('.stat-total').text(stats.total);
	$main.find('.stat-touchless').text(stats.touchless_draft_rate + '%');
	$main.find('.stat-exception').text(stats.exception_rate + '%');
	$main.find('.stat-auto-drafted').text(stats.auto_drafted_count);
	$main.find('.stat-manual').text(stats.manual_count);

	let status_html = '';
	let status_order = ['Completed', 'Draft Created', 'Matched', 'Needs Review', 'Error', 'No Action', 'Pending'];
	let status_colors = {
		'Completed': 'green', 'Draft Created': 'blue', 'Matched': 'cyan',
		'Needs Review': 'orange', 'Error': 'red', 'No Action': 'grey', 'Pending': 'yellow'
	};
	status_order.forEach(function(s) {
		let count = stats.by_status[s] || 0;
		if (count > 0) {
			let color = status_colors[s] || 'grey';
			status_html += '<div class="stat-row"><span class="indicator-pill ' + color + '">' +
				s + '</span><strong>' + count + '</strong></div>';
		}
	});
	$main.find('.status-breakdown').html(status_html);

	let source_html = '';
	Object.keys(stats.by_source || {}).forEach(function(src) {
		source_html += '<div class="stat-row"><span>' + frappe.utils.escape_html(src) + '</span><strong>' +
			(parseInt(stats.by_source[src]) || 0) + '</strong></div>';
	});
	$main.find('.source-breakdown').html(source_html);
}
