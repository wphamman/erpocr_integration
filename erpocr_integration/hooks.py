app_name = "erpocr_integration"
app_title = "ERPNext OCR Integration"
app_publisher = "wphamman"
app_description = "Nanonets OCR integration for ERPNext — automatic invoice data extraction and import"
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
# doctype_js = {"DocType": "public/js/doctype.js"}
# doctype_list_js = {"DocType": "public/js/doctype_list.js"}

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

# doc_events = {}

# Scheduled Tasks
# ---------------

# scheduler_events = {}

# Permissions
# -----------

# permission_query_conditions = {}
# has_permission = {}

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

ignore_links_on_delete = [
	"OCR Request Log",
]

# Request Events
# ----------------
# before_request = ["erpocr_integration.utils.before_request"]
# after_request = ["erpocr_integration.utils.after_request"]

# Authentication and authorization
# --------------------------------

# auth_hooks = []

# Fixtures
# --------

fixtures = []

# Log Clearing
# ------------

default_log_clearing_doctypes = {
	"OCR Request Log": 7,
}
