// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.ui.form.on("Razorpay Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () =>
			// The server reads the saved keys, so unsaved edits are saved first.
			(frm.is_dirty() ? frm.save() : Promise.resolve()).then(() => test_connection())
		);
	},
});

function test_connection() {
	frappe
		.call({
			method: "field_sales_woocommerce.payments.razorpay.test_connection",
			freeze: true,
			freeze_message: __("Testing connection..."),
		})
		.then(({ message: result }) => {
			frappe.msgprint({
				title: result.ok ? __("Connection Successful") : __("Connection Failed"),
				indicator: result.ok ? "green" : "red",
				message: frappe.utils.escape_html(result.message),
			});
		});
}
