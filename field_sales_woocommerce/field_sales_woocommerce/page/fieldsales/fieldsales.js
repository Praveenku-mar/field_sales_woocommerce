// The desk's Apps menu opens an app's route inside the desk (/desk/fieldsales).
// The Field Sales app is served outside the desk, at /fieldsales: go there.
frappe.pages["fieldsales"].on_page_load = function () {
	window.location.replace("/fieldsales");
};
