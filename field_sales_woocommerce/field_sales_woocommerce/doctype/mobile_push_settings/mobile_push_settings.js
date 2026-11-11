// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mobile Push Settings", {
	refresh(frm) {
		const benchProject = frm.doc.__onload && frm.doc.__onload.bench_key_project;
		if (benchProject && !frm.doc.enabled) {
			frm.set_intro(
				__(
					"Push is on through the server-wide Firebase key (project {0}). Upload a key here only to use a different one for this site.",
					[benchProject]
				),
				"green"
			);
		}
		if (!(frm.doc.enabled || benchProject) || frm.is_dirty()) return;
		frm.add_custom_button(__("Send Test Notification"), () => pickUserAndSend());
	},
});

const METHOD = "field_sales_woocommerce.field_sales_woocommerce.doctype.mobile_push_settings.mobile_push_settings";

function pickUserAndSend() {
	frappe.call({ method: `${METHOD}.users_with_phones`, type: "GET" }).then(({ message: users }) => {
		if (!users || !users.length) {
			frappe.msgprint(
				__("No phone is registered yet. Sign in to the Field Sales mobile app and allow notifications, then try again.")
			);
			return;
		}
		const me = frappe.session.user;
		const dialog = new frappe.ui.Dialog({
			title: __("Send Test Notification"),
			fields: [
				{
					fieldname: "user",
					fieldtype: "Select",
					label: __("Send to"),
					reqd: 1,
					options: users.map((u) => ({
						value: u.user,
						label: `${u.full_name} (${u.user}${u.user === me ? ", " + __("you") : ""}) · ${__("{0} phone(s)", [u.phones])}`,
					})),
					default: users[0].user,
					description: __("Only users signed in to the mobile app with notifications allowed are listed."),
				},
			],
			primary_action_label: __("Send"),
			primary_action({ user }) {
				frappe
					.call({ method: `${METHOD}.send_test_notification`, args: { user }, freeze: true })
					.then(({ message }) => {
						if (!message) return;
						dialog.hide();
						const { sent, failed, disabled } = message;
						frappe.msgprint(
							__("Sent to {0} phone(s) of {1}. Failed: {2}. Removed as no longer valid: {3}.", [
								sent,
								user,
								failed,
								disabled,
							])
						);
					});
			},
		});
		dialog.show();
	});
}
