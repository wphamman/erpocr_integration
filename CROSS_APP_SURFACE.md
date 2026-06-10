# CROSS_APP_SURFACE.md — erpocr_integration

Canonical record of this app's external surface (portfolio rule **R3**: one documented
whitelisted API layer per app). Authored against **v1.2.0**.

`erpocr_integration` is an **underlying app, not a shell** — it knows nothing of its
consumers. Its only cross-app coupling is a **soft, runtime feature-detected** integration
with `fleet_management` via ERPNext Custom Fields (§4). There is **no `required_apps`** in
`hooks.py`; install order is irrelevant and either app works standalone.

---

## 1. Dependency posture
- **No hard dependency** on any sibling app (verified: no `required_apps`, no
  `import fleet_management`/`starpops*` in code).
- **Consumers read-only, via REST** — `fleet_management` (`monthly_summary.py` reads OCR Fleet
  Slips), the Fleet Dashboard (see [FLEET_DASHBOARD_DATA_SPEC.md](FLEET_DASHBOARD_DATA_SPEC.md)),
  and the planned `starpops_accounts` fold-in. This app imports/references none of them.
- Arrow direction is clean: consumers → erpocr_integration. Never the reverse.

## 2. Whitelisted RPC endpoints (31 total; **0 `allow_guest`**)

### 2a. Module-level endpoints (`frappe.call` method paths)
| Method path | HTTP | Guard | Purpose |
|---|---|---|---|
| `erpocr_integration.stats_api.get_ocr_stats` | GET | **System Manager / Accounts Manager only** | Aggregated processing stats — *the one endpoint intended for an external dashboard consumer* |
| `erpocr_integration.statement_api.rereconcile_statement` | POST-ish | OCR Statement write | Re-run statement reconciliation after manual supplier change |
| `erpocr_integration.api.upload_pdf` | POST | create perm on OCR Import | Manual file upload |
| `erpocr_integration.api.retry_gemini_extraction` | POST | OCR Import perm | Retry failed extraction |
| `erpocr_integration.api.check_duplicates` | GET | OCR Import perm | Duplicate detection pre-create |
| `erpocr_integration.api.get_open_purchase_orders` | GET | read | PO picker (UI) |
| `erpocr_integration.api.get_purchase_receipts_for_po` | GET | read | PR picker (UI) |
| `erpocr_integration.api.purchase_receipt_link_query` | GET | read | PR link-field query (UI) |
| `erpocr_integration.api.match_po_items` | GET | per-doc read | PO item match (UI) |
| `erpocr_integration.api.match_pr_items` | GET | per-doc read | PR item match (UI) |
| `erpocr_integration.dn_api.retry_dn_extraction` | POST | OCR DN perm | Retry DN extraction |
| `erpocr_integration.dn_api.get_open_purchase_orders_for_dn` | GET | read | DN PO picker (UI) |
| `erpocr_integration.dn_api.match_dn_po_items` | GET | per-doc read | DN PO item match (UI) |
| `erpocr_integration.fleet_api.retry_fleet_extraction` | POST | OCR Fleet Slip perm | Retry fleet extraction |
| `erpocr_integration.fleet_api.route_to_invoice_pipeline` | POST | OCR Fleet Slip perm | Re-route mis-foldered slip to invoice pipeline |
| `erpocr_integration.tasks.drive_integration.test_drive_connection` | GET | System Manager | Config self-test |
| `erpocr_integration.tasks.email_monitor.trigger_email_check` | POST | System Manager | Manual email poll |

### 2b. DocType controller methods (Desk-form actions — invoked via `run_doc_method`)
**Intended for this app's own Desk JS only — NOT a cross-app contract.** Each enforces
status + `document_type` + cross-document lock guards (see CLAUDE.md → *Server-Side Guards*).

- **OCR Import**: `create_purchase_invoice`, `create_purchase_receipt`, `create_journal_entry`, `unlink_document`, `mark_no_action`
- **OCR Delivery Note**: `create_purchase_order`, `create_purchase_receipt`, `unlink_document`, `mark_no_action`
- **OCR Fleet Slip**: `create_purchase_invoice`, `mark_recorded`, `unlink_document`, `mark_no_action`
- **OCR Statement**: `mark_reviewed`

## 3. Data read surface (REST resource / `frappe.client.*`)
External consumers read these DocTypes; **their field names are the de-facto contract**:
- **OCR Fleet Slip** — read by `fleet_management` `monthly_summary.py` and the Fleet Dashboard. Field contract: [FLEET_DASHBOARD_DATA_SPEC.md](FLEET_DASHBOARD_DATA_SPEC.md).
- **OCR Statement / OCR Statement Item** — reconciliation results (status, `recon_status`, `matched_invoice`, `difference`).
- **OCR Import** — processing records / stats backing.

## 4. Custom-Field integration contract (the real cross-app coupling)
All bidirectional, **feature-detected**, install-order-independent.

**4a — We PLANT on `Fleet Vehicle`** (`fixtures/custom_field.json`; read when matching a vehicle):
| Field | Type → Options |
|---|---|
| `custom_fleet_card_provider` | Link → Supplier |
| `custom_fleet_control_account` | Link → Account |
| `custom_cost_center` | Link → Cost Center |
| `custom_ocr_section`, `custom_column_break_ocr` | layout |

Guard: `frappe.db.exists("DocType", "Fleet Vehicle")`.

**4b — We PLANT on `OCR Import`** as a **conditional Custom Field, NOT doctype JSON**
(`install.py` `setup_optional_custom_fields()`, gated on Fleet Vehicle existing; wired to
`after_install` + `after_migrate`):
- `fleet_vehicle` (Link → Fleet Vehicle)

> Rationale (load-bearing): a `Link → <optional-app doctype>` declared in doctype JSON breaks
> meta resolution on sites without that app. Always use the conditional Custom Field pattern.

**4c — `fleet_management` PLANTS `custom_fleet_vehicle` on `Purchase Invoice`; we POPULATE it**
on OCR-built fuel/toll PIs:
- Feature-detected via `frappe.get_meta("Purchase Invoice").has_field("custom_fleet_vehicle")`.
- Backfill: `patches/v1_0_5/backfill_fleet_pi_vehicle.py`.
- If `fleet_management` is absent, the field doesn't exist and population is skipped silently.

## 5. Roles (shipped via `fixtures/role.json`)
- **OCR Manager** — operations: review imports, create documents.
- **OCR Fleet Slip Reader** — read access to fleet slip data.
- Stats endpoint (§2a) is gated separately to **System Manager / Accounts Manager**.

## 6. Stability / change policy
- **Load-bearing contract**: the Custom-Field names in §4 and the read-surface field names in
  §3. Changing them requires coordinating with `fleet_management` + the Fleet Dashboard.
- **Internal / unstable for external callers**: the §2b controller actions (this app's Desk
  surface). §2a methods are stable but mostly UI helpers — only `get_ocr_stats` is a
  deliberate consumer endpoint.
- Keep this file in sync when adding/renaming a whitelisted method or a cross-app Custom Field.
