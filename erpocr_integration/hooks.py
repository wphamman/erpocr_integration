app_name = "erpocr_integration"
app_title = "ERPNext OCR Integration"
app_publisher = "wphamman"
app_description = "Gemini AI OCR integration for ERPNext — automatic invoice data extraction and import"
app_email = "wphamman@users.noreply.github.com"
app_license = "GNU GPLv3"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/erpocr_integration/css/erpocr_integration.css"
# app_include_js = "/assets/erpocr_integration/js/erpocr_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/erpocr_integration/css/erpocr_integration.css"
# web_include_js = "/assets/erpocr_integration/js/erpocr_integration.js"

# include js in doctype views
doctype_js = {
	"OCR Import": "public/js/ocr_import.js",
	"OCR Delivery Note": "public/js/ocr_delivery_note.js",
	"OCR Fleet Slip": "public/js/ocr_fleet_slip.js",
	"OCR Statement": "public/js/ocr_statement.js",
}
# OCR Import list script lives next to its doctype JSON
# (erpnext_ocr/doctype/ocr_import/ocr_import_list.js) and is auto-loaded by
# Frappe — no doctype_list_js hook needed.

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "erpocr_integration.utils.jinja_methods",
# 	"filters": "erpocr_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "erpocr_integration.install.before_install"
# after_install = "erpocr_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "erpocr_integration.uninstall.before_uninstall"
# after_uninstall = "erpocr_integration.uninstall.after_uninstall"

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Purchase Invoice": {
		"on_submit": [
			"erpocr_integration.api.update_ocr_import_on_submit",
			"erpocr_integration.fleet_api.update_ocr_fleet_on_submit",
			"erpocr_integration.statement_api.update_statements_on_pi_submit",
		],
		"on_cancel": [
			"erpocr_integration.api.update_ocr_import_on_cancel",
			"erpocr_integration.fleet_api.update_ocr_fleet_on_cancel",
			"erpocr_integration.statement_api.update_statements_on_pi_cancel",
		],
	},
	"Purchase Receipt": {
		"on_submit": [
			"erpocr_integration.api.update_ocr_import_on_submit",
			"erpocr_integration.dn_api.update_ocr_dn_on_submit",
		],
		"on_cancel": [
			"erpocr_integration.api.update_ocr_import_on_cancel",
			"erpocr_integration.dn_api.update_ocr_dn_on_cancel",
		],
	},
	"Purchase Order": {
		"on_submit": "erpocr_integration.dn_api.update_ocr_dn_on_submit",
		"on_cancel": "erpocr_integration.dn_api.update_ocr_dn_on_cancel",
	},
	"Journal Entry": {
		"on_submit": "erpocr_integration.api.update_ocr_import_on_submit",
		"on_cancel": "erpocr_integration.api.update_ocr_import_on_cancel",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": ["erpocr_integration.tasks.email_monitor.poll_email_inbox"],
	"cron": {
		"*/15 * * * *": [
			"erpocr_integration.tasks.drive_integration.poll_drive_scan_folder",
			"erpocr_integration.tasks.drive_integration.poll_drive_dn_folder",
			"erpocr_integration.tasks.drive_integration.poll_drive_fleet_folder",
		]
	},
}

# Permissions
# -----------

# permission_query_conditions = {}
# has_permission = {}

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = []

# Request Events
# ----------------
# before_request = ["erpocr_integration.utils.before_request"]
# after_request = ["erpocr_integration.utils.after_request"]

# Authentication and authorization
# --------------------------------

# auth_hooks = []

# Fixtures
# --------

fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["OCR Manager", "OCR Fleet Slip Reader"]]]},
	{"dt": "Number Card", "filters": [["module", "=", "ERPNext OCR"]]},
	{"dt": "Dashboard Chart", "filters": [["module", "=", "ERPNext OCR"]]},
	{"dt": "Custom Field", "filters": [["name", "like", "Fleet Vehicle-custom_%"]]},
]
