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

# Accounts dashboard (React SPA) — served at /accounts.
# Build output (committed): erpocr_integration/public/accounts/ + www/accounts.html.
# The catch-all route rule sends any /accounts/<...> URL to the accounts www
# page so client-side react-router can take over (Mint/Raven pattern).
website_route_rules = [
	{"from_route": "/accounts/<path:app_path>", "to_route": "accounts"},
]

# App tile on /apps. `has_permission` hides the tile from users with no OCR
# read perm (frappe/apps.py::get_apps calls it server-side, no args, expects a
# bool; a raising callback is swallowed into a hidden tile). Route is a plain
# /accounts (NOT /app/...) so frappe.apps.get_route returns it verbatim (a
# Desk-page route would be rewritten to a workspace).
#
# This gates the TILE only. The /accounts www page is a bare PUBLIC shell (no
# www/accounts.py get_context), so a Guest / unpermitted user can load it but
# sees only the SPA's login/empty state — every data call is a frappe.client
# get_list/get_count run AS the logged-in user and enforces per-doctype read
# perms. That per-API check is the authoritative gate; the app is read-only, so
# no server-side route gate or CSRF bridge is needed. (There is no top-level
# `has_app_permission` hook — Frappe reads the tile's `has_permission` only.)
add_to_apps_screen = [
	{
		"name": "erpocr_integration",
		"logo": "/assets/erpocr_integration/images/ocr-logo.svg",
		"title": "OCR Accounts",
		"route": "/accounts",
		"has_permission": "erpocr_integration.dashboard.permission.has_app_permission",
	},
]

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
after_install = "erpocr_integration.install.after_install"

# after_migrate: re-runs the optional Custom Field setup on every migrate so that
# installing fleet_management *after* erpocr_integration (or upgrading either
# direction) leaves the fleet_vehicle Custom Field on OCR Import in the right
# state. The setup function is idempotent.
after_migrate = "erpocr_integration.install.after_migrate"

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

# NOTE: no Custom Field fixtures. Fields parented on an optional sibling app's
# doctype (Fleet Vehicle) MUST be provisioned via install.setup_optional_custom_fields()
# — fixture sync runs unconditionally and breaks install on sites without that app.
# Fields on core doctypes (the PI/PR/JE → OCR Import back-link) live in
# install.setup_custom_fields() for the same single-owner reason.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["OCR Manager", "OCR Fleet Slip Reader", "OCR Fleet Driver"]]]},
	{"dt": "Number Card", "filters": [["module", "=", "ERPNext OCR"]]},
	{"dt": "Dashboard Chart", "filters": [["module", "=", "ERPNext OCR"]]},
]
