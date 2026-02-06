# Copyright (c) 2025, ERPNext OCR Integration Contributors
# For license information, please see license.txt

import json
import time
from http import HTTPStatus

import frappe
from frappe import _
from werkzeug.wrappers import Response


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook(*args, **kwargs):
	"""
	Receives OCR extraction data from Nanonets webhook.

	URL: /api/method/erpocr_integration.api.webhook?token=YOUR_TOKEN

	Configure in Nanonets:
	- Export → Webhook → set URL to above
	- Trigger: On Approval
	- Level: Document
	"""
	start_time = time.time()

	# 1. Validate token
	valid, status, msg = _validate_webhook_token()
	if not valid:
		_log_request("webhook", "POST", status="Error", error=msg, start_time=start_time)
		return Response(response=msg, status=status)

	# 2. Parse payload
	if not frappe.request or not frappe.request.data:
		_log_request("webhook", "POST", status="Error", error="Empty request body", start_time=start_time)
		return Response(response=_("Empty request body"), status=HTTPStatus.BAD_REQUEST)

	try:
		raw_payload = frappe.request.data.decode("utf-8")
		payload = json.loads(raw_payload)
	except (ValueError, UnicodeDecodeError) as e:
		_log_request("webhook", "POST", status="Error", error=str(e), start_time=start_time)
		return Response(response=_("Invalid JSON payload"), status=HTTPStatus.BAD_REQUEST)

	# 3. Log the request (async to avoid blocking)
	_log_request("webhook", "POST", request_data=raw_payload, status="Success", start_time=start_time)

	# 4. Enqueue processing on long queue
	frappe.enqueue(
		"erpocr_integration.tasks.process_import.process",
		queue="long",
		raw_payload=raw_payload,
	)

	return Response(status=HTTPStatus.OK)


def _validate_webhook_token() -> tuple[bool, HTTPStatus | None, str | None]:
	"""Validate the webhook token from the query parameter."""
	token = frappe.request.args.get("token", "")

	if not token:
		return False, HTTPStatus.UNAUTHORIZED, _("Missing token")

	settings = frappe.get_cached_doc("OCR Settings")

	if not settings.enabled:
		return False, HTTPStatus.SERVICE_UNAVAILABLE, _("OCR Integration is disabled")

	if token != settings.webhook_token:
		return False, HTTPStatus.UNAUTHORIZED, _("Invalid token")

	return True, None, None


def _log_request(endpoint, method, request_data=None, response_data=None, status="Success", error=None, start_time=None):
	"""Log a webhook request asynchronously."""
	elapsed = time.time() - start_time if start_time else 0

	try:
		frappe.enqueue(
			_create_request_log,
			queue="short",
			enqueue_after_commit=True,
			endpoint=endpoint,
			method=method,
			request_data=request_data,
			response_data=response_data,
			status=status,
			error=error,
			time_elapsed=elapsed,
		)
	except Exception:
		# Don't let logging failures break the webhook
		pass


def _create_request_log(endpoint, method, request_data=None, response_data=None, status="Success", error=None, time_elapsed=0):
	"""Create an OCR Request Log entry."""
	frappe.get_doc({
		"doctype": "OCR Request Log",
		"endpoint": endpoint,
		"method": method,
		"url": frappe.request.url if frappe.request else "",
		"request_data": request_data,
		"response_data": response_data,
		"status": status,
		"error": error,
		"time_elapsed": time_elapsed,
	}).insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep — explicit commit in enqueued job
