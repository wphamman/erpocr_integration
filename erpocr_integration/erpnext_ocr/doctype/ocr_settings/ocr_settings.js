// Duplicate-invoice protection status (Q20). Server: duplicate_bill.get_status (System Manager only).
frappe.ui.form.on("OCR Settings", {
	refresh(frm) {
		const wrapper = frm.fields_dict.duplicate_bill_status && frm.fields_dict.duplicate_bill_status.$wrapper;
		if (!wrapper) return;
		frappe.call({
			method: "erpocr_integration.duplicate_bill.get_status",
			type: "GET",
			callback(r) {
				const colors = { red: "var(--red-600)", amber: "var(--orange-600)", green: "var(--green-600)" };
				const html = (r.message || [])
					.map(
						(m) =>
							`<div style="margin-bottom:6px;color:${colors[m.color] || "inherit"}">` +
							`${frappe.utils.escape_html(m.text)}</div>`
					)
					.join("");
				wrapper.html(html);
			},
		});
	},
});
