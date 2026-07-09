# CROSS_APP_SURFACE.md — erpocr_integration

Canonical record of this app's external surface (portfolio rule **R3**: one documented
whitelisted API layer per app). Authored against **v1.2.0**; the §2c driver-shell upload
contract (and the `OCR Fleet Driver` role) added for **P4** on the v1.3.0/v1.4.0 line.

**Current through v1.6.0** (baselined at `ba21ec1`): §2c/§2a/§5/§6 reflect the D0 driver-perm
widening (`upload_fleet_slip` accepts the plain `Driver` role, endpoint-scoped) and the
owner-scoped idempotent replay. See ADR-0006/ADR-0007 in [docs/architecture/DECISIONS.md](docs/architecture/DECISIONS.md).

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

## 2. Whitelisted RPC endpoints (32 total; **0 `allow_guest`**)

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
| `erpocr_integration.fleet_api.route_to_invoice_pipeline` | POST | OCR Fleet Slip write (per-doc) + OCR Import create | Re-route mis-foldered slip to invoice pipeline |
| `erpocr_integration.fleet_api.upload_fleet_slip` | **POST** | **OCR Fleet Slip create OR plain `Driver` role** (driver shell; D0) | Phone-captured fleet-slip upload — idempotent, async, recon-only (§2c) |
| `erpocr_integration.tasks.drive_integration.test_drive_connection` | GET | System Manager | Config self-test |
| `erpocr_integration.tasks.email_monitor.trigger_email_check` | POST | System Manager | Manual email poll |

### 2b. DocType controller methods (Desk-form actions — invoked via `run_doc_method`)
**Intended for this app's own Desk JS only — NOT a cross-app contract.** Each enforces
status + `document_type` + cross-document lock guards (see CLAUDE.md → *Server-Side Guards*).

- **OCR Import**: `create_purchase_invoice`, `create_purchase_receipt`, `create_journal_entry`, `unlink_document`, `mark_no_action`
- **OCR Delivery Note**: `create_purchase_order`, `create_purchase_receipt`, `unlink_document`, `mark_no_action`
- **OCR Fleet Slip**: `create_purchase_invoice`, `mark_recorded`, `unlink_document`, `mark_no_action`
- **OCR Statement**: `mark_reviewed`

### 2c. Driver-shell fleet-slip upload contract (P4 — the one deliberate cross-app *write* surface here)

`erpocr_integration.fleet_api.upload_fleet_slip` — a shell-agnostic POST that lands a
phone-captured fleet slip as an **OCR Fleet Slip recon record** (image attached, Gemini
extraction queued async). Consumed by the `driver_ui_shell` Fleet-Slip screen; this app stays
unaware of the shell.

```
upload_fleet_slip(client_request_id, fleet_vehicle=None, vehicle_registration=None, captured_at=None)
  @frappe.whitelist(methods=["POST"])   # multipart/form-data — binary `file` field (JPEG/PNG/PDF)

Returns (same shape fresh + idempotent replay):
  {"ocr_fleet_slip": <OCR-FS-…>, "status": <str>, "client_request_id": <uuid>, "duplicate": bool}
```

- **Recon-only, never invoice.** Creates an OCR Fleet Slip with `purchase_invoice` NULL (the
  v1.2.0 invariant). The endpoint is structurally incapable of creating/feeding a Purchase
  Invoice or an OCR Import — its permission gate never consults OCR Import.
- **Permission posture (v1.6.0; D0 2026-07-06): `OCR Fleet Slip` create OR the plain
  `Driver` role.** Possession-based driver writes accept `Driver` — the same posture as
  `fleet_management.api.submit_vehicle_inspection` — so real drivers need **no site-level
  role provisioning**. The widening is endpoint-scoped (an in-code role check, not a
  doctype-perm row): Desk posture is unchanged, and a Custom-DocPerm shadow on the doctype
  cannot disable it. The `OCR Fleet Driver` role (§5) still passes via the doctype perm and
  remains the belt-and-braces grant in deploy runbooks. Guest denied explicitly.
- **Idempotency = the R-B house write-contract template, verbatim.** A client UUID
  (`client_request_id`) under a DB **nullable-unique** constraint; **insert-and-catch** the
  unique violation (not check-then-insert) + **full** `frappe.db.rollback()` (REPEATABLE-READ
  correctness) → a 3G retry returns the original slip with `duplicate: true`. Identical shape to
  `fleet_management.api.submit_vehicle_inspection` (P3) — see that repo's
  `handback-p3-inspection-contract-2026-06-11.md`. NULL for Drive/Desk slips (multiple NULLs
  coexist), so the Drive pipeline is untouched. **Replay is owner-scoped (v1.6.0):** only the
  user who created the slip receives the duplicate envelope; any other authenticated caller
  presenting the key gets a PermissionError (never the slip's name/status). The shell generates
  the UUID per capture on one device, so a legitimate replay is always same-user.
- **Fail-safe provider fork.** `posting_mode` derives from the vehicle's
  `custom_fleet_card_provider`. If the provider is missing the slip lands in **Needs Review**
  with a blank `posting_mode`/supplier (and the PI guard `posting_mode != "Direct Expense"`
  blocks any invoice) — **never silently routed toward the invoice path**. The recon-vs-invoice
  fork must not depend on a data field being perfectly maintained. (Applies to shell-sourced
  slips throughout, incl. async OCR re-matching; the Drive path keeps its Direct-Expense
  fallback.)
- **Driver-supplied vehicle beats plate-OCR.** A supplied `fleet_vehicle` is set + marked
  `Confirmed`; async extraction does **not** re-match (it would clobber the driver's pick).
- **`captured_at`** = device-truth capture time, stored distinctly from the server creation
  timestamp (offline-queued uploads arrive late).
- **File:** multipart binary (not base64), **≤2MB** server-enforced (the contract's own
  boundary, tighter than the 10MB invoice uploader), magic-byte validated, stored as a
  **private** `File` → office-visible via OCR Manager read perm, never publicly exposed.
- **`source_type` vocabulary (the source discriminator — set as a constant, NEVER from client
  input):** `"Gemini Drive Scan"` (Drive poll) · `"Gemini Shell Upload"` (this contract).
  Consumers must treat `source_type` as the discriminator. **As of P4 T1, no consumer reads it**
  — `fleet_management` (`monthly_summary.py`, Fuel Efficiency Tracker) consumes slips by record
  (`fleet_vehicle` + `transaction_date` + `status`), so an API slip and a Drive slip are
  identical to downstream code except this field. If a consumer ever starts discriminating on
  it, these two values are the whole vocabulary.
- **Forward notes — the shared phone photo/upload contract (designed once, separately; not built
  here).** The shell side names, but this contract does not implement: client-side compression
  to **≤1.5MB** (long edge ~1280–1600px, JPEG q~0.7) before upload; an **offline IndexedDB
  queue** keyed on `client_request_id` (compressed blob + key + `captured_at`) that drains with
  backoff when signal returns — the idempotency key is what makes that drain safe. PODs (P6) and
  the Wesbank-recon consumption of API slips are out of P4 scope.

## 3. Data read surface (REST resource / `frappe.client.*`)
External consumers read these DocTypes; **their field names are the de-facto contract**:
- **OCR Fleet Slip** — read by `fleet_management` `monthly_summary.py` and the Fleet Dashboard. Field contract: [FLEET_DASHBOARD_DATA_SPEC.md](FLEET_DASHBOARD_DATA_SPEC.md).
- **OCR Statement / OCR Statement Item** — reconciliation results (status, `recon_status`, `matched_invoice`, `difference`).
- **OCR Import** — processing records / stats backing.

## 4. Custom-Field integration contract (the real cross-app coupling)
All bidirectional, **feature-detected**, install-order-independent.

**4a — We PLANT on `Fleet Vehicle`** (gated `install.setup_optional_custom_fields()` — NOT fixtures; a Custom Field fixture parented on an optional-app doctype breaks standalone install. Read when matching a vehicle):
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
- **OCR Fleet Slip Reader** — read + write (no create/delete) on fleet slip data (Desk review).
- **OCR Fleet Driver** (P4) — **create on OCR Fleet Slip ONLY**, reads `if_owner`-scoped, no
  Desk access. Was the sole driver-shell upload identity until v1.6.0; **since D0 the §2c
  endpoint also accepts the plain `Driver` role**, so this role is no longer required for
  uploads — it remains the belt-and-braces grant (and the way to give a driver `if_owner`
  Desk read of their own slips). Deliberately NOT granted OCR Import create, so a
  driver can never open the invoice surface. (Note: the existing `OCR Fleet Slip Reader` grants
  broad read+write on *all* slips — if a driver should be strictly own-slips-only, assign
  `OCR Fleet Driver` and NOT Reader; deployment/shell decision.)
- Stats endpoint (§2a) is gated separately to **System Manager / Accounts Manager**.

## 6. Stability / change policy
- **Load-bearing contract**: the Custom-Field names in §4, the read-surface field names in §3,
  the §2c upload contract (signature + return shape + `client_request_id` field +
  `source_type` vocabulary). Changing them requires coordinating with `fleet_management`, the
  Fleet Dashboard, and the `driver_ui_shell`.
- **Internal / unstable for external callers**: the §2b controller actions (this app's Desk
  surface). §2a methods are stable but mostly UI helpers — only `get_ocr_stats` is a
  deliberate consumer endpoint.
- Keep this file in sync when adding/renaming a whitelisted method or a cross-app Custom Field.
