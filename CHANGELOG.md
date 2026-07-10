# Changelog

All notable changes to the ERPNext OCR Integration app are documented here. Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.8.0] — 2026-07-10

Architecture-register backlog sweep (OPEN-QUESTIONS Q6, Q7a–d, Q8 as ruled by Willie 2026-07-09, plus the ADR-0011 CI gate).

### Added
- **Opt-in Fleet Card auto-record (Q8 lever 1).** New OCR Settings check **Enable Fleet Card Auto-Record** (off by default — ADR-0002's review-first philosophy). When on, a Fleet Card slip that lands or updates to `Matched` with **high confidence** is completed via the existing `mark_recorded()` path, clearing the grew-to-34+ manual backlog at the source. High confidence = `fleet_vehicle` set with a **confirmed** match (exact `Auto Matched` or driver/operator `Confirmed` — never a fuzzy `Suggested`) AND the recon payload present (Fuel: litres + total; Toll: total). `Other`-type slips (unauthorized flag) never auto-record. Audit mirrors auto-draft: an `auto_recorded` flag plus `auto_record_skipped_reason`, so "why didn't it auto-record" is a group-by, not a debugging session. Triggers on both the extraction pipeline (`fleet_gemini_process`) and Desk-side saves that reach `Matched` (controller `on_update`). ADR-0003 invariants hold and are regression-tested: `purchase_invoice` stays NULL, Direct Expense slips are never touched, and with the setting off behavior is test-proven identical to v1.7.0. `fleet_management`'s `monthly_summary.py` reads both `Matched` and `Completed` (verified in that repo), so the consumer sees no change.
- **Bulk Mark Recorded (Q8 lever 2).** New list action on OCR Fleet Slip backed by a new whitelisted endpoint `fleet_api.bulk_mark_recorded` (documented in CROSS_APP_SURFACE.md §2a; count 32 → 33). The selection is UI sugar — the server re-validates **every** row: status must be `Matched` (deliberately stricter than single Mark Recorded, which also accepts Needs Review), `posting_mode` must be Fleet Card, then each row runs the existing `mark_recorded()` guards including per-document write permission. Failing rows are skipped with a per-row reason; the rest proceed. Capped at 200 rows per call.
- **Supplier-scoped item aliases (Q7c — ruled IN 2026-07-09).** `OCR Item Alias` gains an optional **Supplier** link; the item-matching order is now *Item Supplier lookup → supplier-scoped alias → global alias → exact → service mapping → fuzzy → default_item*. Additive and non-destructive: every pre-v1.8.0 alias stays global (the fallback tier — regression-tested unchanged); the doctype's autoname flips to hash so the same OCR text can exist per-supplier (existing rows keep their names). **Learning is supplier-scoped when the parent supplier is known** — confirming "Bracket 40mm" → ITEM-A for supplier A no longer clobbers the mapping supplier B relies on (the motivating cross-supplier collision, now resolved per-supplier in tests).
- **CI dist-freshness gate (ADR-0011's caveat enforced).** New GitHub Actions job rebuilds the SPA (`frontend/`, Node 22, `npm ci && npm run build`) and **fails if the output differs from the committed dist** (`public/accounts/` + `www/accounts.html`) — i.e. someone edited `frontend/src` without rebuilding. Vite hashes are content-based, so identical source ⇒ identical output. Proven green on HEAD and red on a deliberate source tweak.

### Changed
- **Fleet Actual-VAT injection (Q7a).** Fleet-slip PI creation now delegates its tax table to the invoice pipeline's shared `_build_taxes_from_template`: an operator picking an Actual-type import template gets the slip's extracted VAT injected into the Actual row instead of a 0-tax draft. Same pure-Actual scoping as the invoice path — a mixed (percentage + auxiliary Actual) template is never injected, so the pre-v1.5.0 double-tax bug is not reintroduced.
- **Q6 cleanup: the vestigial control-account capture on the Fleet Card path is retired.** Since v1.2.0 no PI is created from a Fleet Card slip, so the `custom_fleet_control_account` value copied per-slip flowed nowhere. Both capture sites (`fleet_api._apply_vehicle_config` and the controller's `_apply_vehicle_config_from_link`) now leave `expense_account` blank on the Fleet Card branch; Direct Expense matching behavior is unchanged. Follow-through on the one path Q6 newly exposes — a Fleet Card slip flipped to Direct Expense during review arrives at PI creation with a blank expense account: it falls back to `OCR Settings.fleet_expense_account`, and if that is also unset, PI creation now **throws an actionable error at create time** ("Set Fleet Expense Account in OCR Settings, or set an expense account on this slip") instead of `ignore_mandatory` inserting an account-less draft that only fails at submit. The `Fleet Vehicle.custom_fleet_control_account` Custom Field itself stays — it's §4a cross-app surface.

### Fixed
- **Decode-verify gate on every image ingest path (Q7b).** The PIL decode gate (previously only on `upload_fleet_slip`) moved to the shared `api.is_image_decodable` and now runs on manual upload, email-attachment ingest, and all three Drive polls (invoice/DN/fleet) — a corrupt-but-magic-valid JPEG is rejected up front instead of 500-ing in PIL when Frappe builds a thumbnail. On Drive paths a decode failure lands in the existing `_record_drive_scan_failure` accounting, so it counts toward `MAX_DRIVE_RETRIES` rather than bypassing the cap.
- **`getdate()` hardening on statement-recon date compares (Q7d).** Both compare sites (`_in_period` and the reverse check) parse via `frappe.utils.getdate` instead of raw `str()` ordering — a type/format mismatch can no longer silently mis-order, and a candidate PI without a posting date is treated as brought-forward (the old string compare ranked `None` as in-period). Test conftest gained real `getdate` semantics per ADR-0009 so these compares are exercised against genuine date objects.

## [1.7.0] — 2026-07-09

### Added
- **Folded the `starpops_accounts` read-only React dashboard into this app (ADR-0010 executed; OPEN-QUESTIONS Q5).** The standalone Mint-pattern SPA is now part of `erpocr_integration` — one `bench get-app`, one version, one CHANGELOG. It serves at the website route **`/accounts`** (`website_route_rules` catch-all → `www/accounts.html`; the standalone's already-shipped, UAT-passed serving model, preserved as-is) and appears as an **OCR Accounts** tile on `/apps`. Source lives in `frontend/` at the repo root (Vite + React 19 + TS + Tailwind 4 + frappe-react-sdk); the build emits to `erpocr_integration/public/accounts/` (base `/assets/erpocr_integration/accounts/`) and copies the shell to `erpocr_integration/www/accounts.html`. The dashboard is **read-only, zero writes** — it reads OCR Import / OCR Delivery Note / OCR Fleet Slip counts and lists via generic `frappe.client` (`get_count`/`get_list`) as the logged-in user; there is no write path and no new whitelisted method (data access rides the existing `frappe.client` read surface — CROSS_APP_SURFACE.md §3, unchanged).
- **App-tile permission gate — 3-doctype read union.** `erpocr_integration.dashboard.permission.has_app_permission` (wired via the `add_to_apps_screen` tile's `has_permission` callback — Frappe has no top-level `has_app_permission` hook) passes for Administrator, System Manager, or **read on any of** OCR Import / OCR Delivery Note / OCR Fleet Slip — so a user who can see any one queue gets the tile, and someone with none doesn't. It gates the **tile only**: the `/accounts` route is a public www shell, and the authoritative check is Frappe's per-doctype permission on every `get_list`/`get_count` the SPA makes (run as the logged-in user), so an unpermitted user who navigates directly lands on the login/empty state, never data.
- **`ocr-logo.svg`** tile icon (the launcher tile referenced a logo that never shipped).

### Deploy note
- **The built SPA dist is committed** (`erpocr_integration/public/accounts/*` + `www/accounts.html`), so the app installs and serves the dashboard with **zero Node/npm step at deploy** — the same managed-host pattern `fleet_management` uses for its dashboard. Rebuild after changing `frontend/`: `cd frontend && npm ci && npm run build` (writes the committed dist; no source map is emitted). This supersedes ADR-0010's "Starktail needs a Node build step" consequence — no Starktail image-build change is required for the SPA to serve.
- Both Codex-review fixes from the standalone rolled forward: the 3-doctype permission union (above) and clear-password-on-failed-login in the SPA login form.

## [1.6.0] — 2026-07-07

### Changed
- **`upload_fleet_slip` accepts the plain `Driver` role (driver-shell GAP 2 closed at root — architecture decision D0, 2026-07-06).** Real drivers hold only the portfolio-wide `Driver` persona role (used by `fleet_management` and `starpops_assets` — any future holder of it gains slip upload, which is D0's intent), so every shell slip 403'd unless the site-provisioned `OCR Fleet Driver` role was granted per user (hit live twice: Phase-6 device smoke 2026-06-26, driver-shell WP1 verification 2026-07-06). The endpoint's permission gate now passes on `OCR Fleet Slip` create **or** `"Driver" in frappe.get_roles()` — the same possession-based posture as `fleet_management.api.submit_vehicle_inspection`. The widening is deliberately **endpoint-scoped, not a doctype-perm row**: Desk posture is unchanged (a Driver System User still can't create slips in Desk), no migrate is needed, and the known prod Custom-DocPerm shadow on OCR Fleet Slip cannot render it inert. `OCR Fleet Driver` still passes via the doctype perm and is demoted to a belt-and-braces runbook grant. Everything else about the contract — same-user idempotent `client_request_id` replay (cross-user replay is newly rejected, see Security below), `captured_at` normalization, the recon-only invariant, the `vehicle_registration` relief path — is unchanged (posture tests added).

### Security
- **Idempotent replay is now owner-scoped** (review finding on the widened surface). Previously any authenticated caller presenting another user's `client_request_id` received the duplicate envelope (the slip's name + status). The replay path now returns it only to the slip's owner; anyone else gets a PermissionError. A legitimate replay is always same-user (the shell generates the UUID per capture on one device). Same gap exists in `fleet_management`'s `submit_vehicle_inspection` replay — flagged for that repo.

## [1.5.1] — 2026-07-06

### Fixed
- **Auto-draft was 100% blocked by the fiscal-year guard (prod root cause found via skip-reason data).** `_invoice_date_in_fiscal_year` called `frappe.utils.get_fiscal_year` — a function that does **not exist** (it lives in `erpnext.accounts.utils`; verified on a real bench). The `AttributeError` raised on every call and the blanket `except` reported every date as "outside any active Fiscal Year", so **every invoice that passed the auto-draft confidence gate was skipped** (~10 on prod since the guard shipped) while valid Fiscal Years existed. Invisible to the mocked test suite (a MagicMock attribute never raises) — same failure class as the v1.4.1 tz-mock lesson. Now imports from `erpnext.accounts.utils` with a separate ImportError path (a missing module can never masquerade as a fiscal-year rejection), plus a conftest `erpnext` module registration so tests exercise the real import location.
- **CI installs Pillow** — the v1.5.0 merge brought the decode-verify tests (which build a real JPEG via PIL) onto master; the CI venv lacked Pillow and collection failed. App runtime unaffected (Frappe ships Pillow).

## [1.5.0] — 2026-07-06

Roadmap build from the 2026-07-06 live-system review (`docs/reviews/REVIEW-LIVE-erpocr_integration-2026-07-06.md`) — findings referenced below by review ID.

### Added
- **Customs/import VAT handled correctly (review V1 — the Cargo Compass fix).** New OCR Settings field **Import (Actual VAT) Tax Template**. When set, template auto-selection runs a ratio test: extracted VAT that is far from the standard percentage of the subtotal (customs brokers bill import VAT as a fixed amount — observed on prod at 7.5x-111% of subtotal vs ~15%) selects the Actual-type import template instead of the percentage default. `_build_taxes_from_template` then **injects the extracted `tax_amount` into the template's first Actual row**, so the draft PI posts the real customs VAT against the VAT-control account — previously the accountant re-keyed the tax table on every customs invoice. Acceptance-tested against prod PI `ACC-PINV-2026-00416` (net R57,614.30 + Actual VAT R64,038.90). Setting unset → behavior unchanged.
- **Back-link from created documents to their OCR Import (review U1).** New read-only `custom_ocr_import` Link field on Purchase Invoice / Purchase Receipt / Journal Entry, set at creation — one click from the accounting document back to the OCR staging record (raw extraction, match state, retry). Installed by the new `install.setup_custom_fields()` (runs on install + every migrate).

### Fixed
- **Multi-invoice partial failure no longer strands orphan records (review C1).** `gemini_process` now rolls back the open transaction before writing the Error status. Previously, a failure on invoice N of a multi-invoice PDF committed invoices 1..N-1 as orphans — never archived, dedup-skipped forever, and auto-draftable from a "failed" extraction.
- **Standalone install no longer breaks on Fleet Vehicle fixtures (review O1).** The 5 `Fleet Vehicle-custom_*` Custom Fields moved from `fixtures/custom_field.json` (synced unconditionally — fails on sites without fleet_management) into the gated `setup_optional_custom_fields()`, the pattern the install module's own docstring prescribes.
- **Statement reconciliation: brought-forward invoices + duplicate bill_no (reviews R1, R2/O2).** The forward-match candidate pool now reaches 365 days before the statement period (open-item statements list unpaid prior-period invoices; these were mis-flagged "Missing from ERPNext"), while the reverse check stays strictly period-bounded. Duplicate normalized `bill_no` candidates are now resolved deterministically: prefer the not-yet-matched PI whose grand_total equals the statement debit, else earliest posting date (`order_by` added — also v16 default-sort-flip safe).
- **Alias re-learning upsert (review M1).** Correcting a supplier/item alias now UPDATES the existing `OCR Supplier Alias` / `OCR Item Alias` row. Previously the first mapping won forever — a wrong alias kept auto-matching at tier-1 confidence (high enough to auto-draft) and corrections were silently dropped.
- **JE multi-tax-account split (review M2).** A multi-row tax template no longer books the entire tax amount to the first account: rated rows split the extracted tax proportionally (rounding remainder on the last row); non-inferable splits (zero-rate rows) book to the first account with a loud review warning.
- **Image fleet slips are decode-verified at upload**: `upload_fleet_slip` runs a PIL decode-gate so a corrupt-but-magic-valid JPEG/PNG is rejected at the endpoint instead of landing a slip whose extraction can never succeed.
- **Review-pass hardening of the above** (8-angle adversarial review of this release's diff):
  - Actual-VAT injection is scoped to **pure-Actual templates** (a percentage template with an auxiliary Actual row is never injected — would have double-taxed) and warns when a template has multiple Actual rows; inclusive-rate detection never flags an Actual row `included_in_print_rate` (ERPNext rejects that at insert).
  - The `_select_tax_template` ratio anchor subtracts **Deduct** rows (an Add+Deduct default template no longer misroutes ordinary invoices to the import template).
  - Statement recon candidate preference: **in-period beats brought-forward** at equal evidence (a recycled invoice number from last year can't shadow this period's invoice; recurring same-ref/same-amount charges match the in-period PI), and a full-amount re-reference re-uses the already-matched PI.
  - `gemini_process` commits after auto-draft and isolates the trailing notification, so a late notify failure can't roll back drafts or mark a successful run Error.
  - Alias corrections only rewrite an existing alias when the row actually changed in that save (a stale still-Confirmed record being re-saved can no longer revert a newer curated alias); JE proportional split stays exact under rounding overshoot.

### Security
- **`OCRImport.unlink_document` now requires write permission on the OCR Import (review S1)** — previously a role with only read on OCR Import plus delete on Purchase Invoice could delete the draft and reset the record via `db_set`.
- **All 15 state-changing whitelisted document methods are now `methods=["POST"]` (review S2)** — create/unlink/mark methods across OCR Import, OCR Fleet Slip, OCR Delivery Note, OCR Statement, and `rereconcile_statement` were GET-callable, bypassing Frappe's CSRF check. Matches the app's own v16-safe convention; the desk UI already POSTs, so no client change.

## [1.4.1] — 2026-06-23

### Fixed
- **Driver-shell fleet-slip upload no longer 500s on every submit (timezone bug).** `upload_fleet_slip` stored the tz-aware datetime returned by `get_datetime(captured_at)` straight into the naive MariaDB `captured_at` column. The driver shell always sends `captured_at = new Date().toISOString()` (UTC `Z`), so MariaDB rejected it with error 1292 — and the failure fires at `insert()`, **outside** the parse `try/except` — making **every** shell slip submit 500. The client (correctly) treats 500 as retriable, so the driver UI sat stuck on "Queued" with zero records landing. Now mirrors the fix `fleet_management` already shipped for the identical bug (P3.5 `submit_vehicle_inspection`): a tz-aware value is converted to the site timezone, then `tzinfo`/microseconds are stripped so the stored value is naive site-local — exactly like `now_datetime()`. The `None` / unparseable / already-naive paths are unchanged (a malformed device timestamp is logged + dropped, never blocks the recon upload); the Drive pipeline (`fleet_gemini_process`) never sets `captured_at` and is unaffected.

## [1.4.0] — 2026-06-12

### Added — Driver-shell fleet-slip upload contract (P4)
- **`fleet_api.upload_fleet_slip(client_request_id, fleet_vehicle=None, vehicle_registration=None, captured_at=None)`** — a whitelisted POST that lands a phone-captured fleet slip as an **OCR Fleet Slip recon record** (image attached, Gemini extraction queued async). Multipart binary upload, own **2MB** server cap, magic-byte validated, private File attachment. Structurally **recon-only** — it can never create or feed a Purchase Invoice. Contract documented in [CROSS_APP_SURFACE.md §2c](CROSS_APP_SURFACE.md).
- **Idempotency = the R-B house template** (matches `fleet_management.submit_vehicle_inspection`): a nullable-unique `client_request_id` field on OCR Fleet Slip + insert-and-catch + full rollback → a 3G retry returns the original slip with `duplicate: true`, never a second.
- **Fail-safe provider fork** — `posting_mode` derives from the vehicle's `custom_fleet_card_provider`; provider missing → the slip lands in Needs Review with blank `posting_mode` (PI guard blocks any invoice), **never silently routed toward the invoice path**. Applies to shell-sourced slips throughout (async OCR matching *and* the controller's `_apply_vehicle_config_from_link` on (re-)link); the Drive path keeps its Direct-Expense fallback.
- **New `OCR Fleet Driver` role** — create on OCR Fleet Slip ONLY, reads `if_owner`-scoped (a driver cannot read other drivers' slips), no Desk access; Guest denied.
- New fields on OCR Fleet Slip: `client_request_id` (Data, nullable-unique, no_copy, hidden), `captured_at` (Datetime, device-truth). `source_type` reused as the Drive-vs-API discriminator (`"Gemini Drive Scan"` / `"Gemini Shell Upload"`, server-set constant).
- Upload + attach + enqueue land on a single commit (`enqueue_after_commit=True`) so a failure can't strand a keyed slip without its image/job.

> **Deploy note:** the OCR Fleet Slip **Custom DocPerm shadow** on prod masks the new role (and `raw_payload`). The deploy MUST run **Customize Form → Restore Original Permissions** on OCR Fleet Slip, or the driver role is silently dead. See the P4 handback §14f.

## [1.3.0] — 2026-06-11

### Added
- **Catch-all items are now learnable (auto-draft fix).** When a confirmed line uses the configured `default_item` (a generic non-stock catch-all), the system now learns its GL coding as a `(supplier, pattern) → expense account + cost center` service mapping. Previously *all* learning was skipped for default-item lines, so they stayed "Suggested" and never reached the auto-draft confidence gate. Recurring descriptions now auto-code and can auto-draft.
- **Supplier default coding.** An OCR Service Mapping whose Description Pattern is a single `*` (with a Supplier set) codes any otherwise-unmatched line for that supplier — for suppliers whose descriptions vary every time (e.g. a transport subcontractor where each line names a different route/driver/vehicle). Last-resort tier, after supplier-specific and generic patterns.

### Fixed
- **Auto-draft no longer silently fails on a misread invoice date.** A Gemini date misread (e.g. 2001 for 2026) outside any active Fiscal Year now skips auto-draft cleanly to "Needs Review" with a clear reason, instead of firing a create that fails deep in ERPNext's Fiscal Year validation and only surfaces in the Error Log.

## [1.2.0] — 2026-06-03

OCR Fleet Slip workflow now branches by `posting_mode`. Fleet Card slips (paid via a fleet card provider like Wesbank) close as **control records** with no Purchase Invoice — the provider's monthly invoice in `fleet_management` is the source of truth for the cost, and a per-slip PI would double-count. Direct Expense slips (paid on a business debit/credit card) keep the existing PI flow.

### Fleet Card terminal disposition
- New whitelisted `mark_recorded()` method on [ocr_fleet_slip.py](erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py) — Fleet Card-only, transitions `Matched` / `Needs Review` → `Completed` with `purchase_invoice` NULL. Guards: posting_mode must be `Fleet Card`, status must be reviewable, `fleet_vehicle` must be linked.
- New **Mark Recorded** primary-action button on the form for Fleet Card slips, gated on the same reviewable-status condition as the existing Create > Purchase Invoice button ([ocr_fleet_slip.js](erpocr_integration/public/js/ocr_fleet_slip.js)).
- Form intro text branches by `posting_mode` at the `Needs Review` and `Matched` states so operators see the correct next step (Mark Recorded vs Create PI), and the `Completed` intro renders a "Recorded as control record" message when `purchase_invoice` is NULL.

### PI guard
- [create_purchase_invoice()](erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py) now raises if `posting_mode != "Direct Expense"`. Defence in depth — the UI also hides the button for Fleet Card mode, but this catches API bypasses and stale clients. Docstring corrected (the v1.0.x docstring said "for fleet card mode" — inverted from intent).
- Server guard ordering: `status not in (Matched, Needs Review)` → `posting_mode != "Direct Expense"` → `document_type != "Purchase Invoice"` → existing duplicate/vehicle/supplier/item checks.

### `posting_mode` operator-editable
- Dropped `read_only: 1` on [ocr_fleet_slip.json](erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.json). Auto-set from `Fleet Vehicle.custom_fleet_card_provider` on vehicle match (both `fleet_api._apply_vehicle_config` and the controller's `_apply_vehicle_config_from_link`); operator can flip per-slip during review for the edge case where a fleet-card vehicle was filled on a business card (or vice-versa). The PI guard and `mark_recorded()` guard both key off the slip's current `posting_mode`, not a re-derive from the vehicle.

### Status-semantic shift
- **`Completed` no longer implies `purchase_invoice IS NOT NULL`**. A Fleet Card slip in `Completed` has the link NULL by design. Downstream consumers that query Fleet Slips for document linkage must now check both `posting_mode` and `purchase_invoice`. `fleet_management`'s `monthly_summary.py` reads OCR Fleet Slips for `status in [Completed, Draft Created, Matched, Needs Review]` regardless of `posting_mode`, so Fleet Card slips in `Completed` continue to feed LITRES_MISMATCH and the Fuel Efficiency Tracker — zero cross-app change required.

### Tests
- 9 new tests in [test_fleet_controller.py](erpocr_integration/tests/test_fleet_controller.py): PI refusal on Fleet Card + unset posting_mode; `mark_recorded` happy path from Matched + Needs Review; mark_recorded guards (wrong mode, no vehicle, wrong status across 4 states); manual-override inversion (slip flipped between modes gets the right behaviour on each side).
- Factory `_make_fleet_slip` default flipped to `posting_mode = "Direct Expense"` since most active controller tests exercise the PI path; Fleet Card tests override per-case.
- 664 tests pass (+9 over v1.1.6's 655). ruff + ruff-format clean.

### Scope (deliberate)
- **No backfill patch** for existing prod slips. Pre-deploy audit (read-only API probe against erp.starpops.co.za on 2026-06-03) shows: **17 Matched/Draft-Created slips with `posting_mode = "Fleet Card"`** — accounting closes 16 of them manually via the new Mark Recorded button (the 1 Draft Created completes naturally via doc_events when the PI submits). **Plus 19 Matched/Needs-Review slips with `posting_mode = ""`** (empty — never auto-set, likely from a vehicle linked without `vehicle_match_status = Confirmed`); these show neither button under v1.2.0's mode-gated UI, but `posting_mode` is now operator-editable so accounting picks the right mode per slip during review. No Direct Expense slips currently in flight on prod.
- **`custom_fleet_control_account` left in place** but is now vestigial on the Fleet Card path (still captured on the slip, no longer flows into a PI since no PI is created on Fleet Card slips). Cleanup TBD in a future release once usage patterns are stable.
- No changes to fleet_management. Reuse of `Completed` for the new control-record disposition means `fleet_management/monthly_summary.py`'s reader filter doesn't need updating.

## [1.1.6] — 2026-05-28

Hotfix for the v1.1.5 graceful-degradation regression. Surfaced on the Cactus site, where `fleet_management` isn't installed — the OCR workspace crashed with "Field fleet_vehicle is referring to non-existing doctype Fleet Vehicle" the moment the workspace number cards loaded.

### Conditional Custom Field install for `OCR Import.fleet_vehicle`
- v1.1.5 declared `fleet_vehicle` as a hard `Link → Fleet Vehicle` in [ocr_import.json](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json). That ate meta resolution on any site without the Fleet Vehicle doctype, regardless of the v1.1.5 JS toggle — meta loads before JS runs, and the workspace eagerly resolves OCR Import meta to render its number cards.
- Removed the field from the doctype JSON. The field is now provisioned at runtime as a Custom Field on OCR Import — but only when `frappe.db.exists("DocType", "Fleet Vehicle")` is true.
- New [install.py](erpocr_integration/install.py) exposes `setup_optional_custom_fields()` and is wired to both `after_install` and `after_migrate` in [hooks.py](erpocr_integration/hooks.py). Idempotent; safe to re-run.
- Sites that install `fleet_management` AFTER `erpocr_integration` get the field picked up on the next `bench migrate`.
- [ocr_import.py](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py) `create_purchase_invoice()` now reads via `self.get("fleet_vehicle")` instead of attribute access — safe when the Custom Field isn't installed (`.get` returns `None`, the existing `has_field` guard on the PI side still applies).
- Cleaned up the now-redundant async `frappe.db.exists` + `toggle_display` in [ocr_import.js](erpocr_integration/public/js/ocr_import.js). When the field isn't installed, Frappe omits it from the form entirely — nothing for the JS to hide.

### Migration patch: `v1_1_6.migrate_fleet_vehicle_to_custom_field`
- Wired into [patches.txt](erpocr_integration/patches.txt) — runs automatically on every `bench migrate`.
- Removes any Property Setter row that was added as a stopgap workaround for the v1.1.5 bug (operator-applied `(doc_type, fleet_vehicle, options) → User` overrides on OCR Import or OCR Fleet Slip). These become orphans once the underlying field is gone; the patch sweeps them on the upgrade.
- Re-runs `setup_optional_custom_fields()` so sites that already have `fleet_management` come out with the Custom Field provisioned in one upgrade step. Frappe's standard JSON sync handles removal of the lingering DocField record from the old install.

### Scope (deliberate)
- **OCR Fleet Slip's `fleet_vehicle` is unchanged.** Same Link-options shape, same theoretical vulnerability — but it's load-bearing for the fleet pipeline (vehicle matching, posting mode, supplier resolution) and there's no expectation that doctype works on a site without `fleet_management`. Cactus doesn't reference OCR Fleet Slip from any workspace card, so it doesn't trip the same crash. Out-of-scope for this hotfix.
- No data migration. The DB column `fleet_vehicle` on `tabOCR Import` survives across the field-type change (DocField → Custom Field, same fieldname). Existing values are preserved on sites with `fleet_management`.

### Tests
- New [test_install.py](erpocr_integration/tests/test_install.py) — 6 unit tests covering: install no-op when Fleet Vehicle absent, install runs when present, after_install / after_migrate delegate correctly, patch clears stopgap Property Setters, patch runs install on fleet-bearing sites.
- New `test_pi_handles_missing_fleet_vehicle_attr` in [test_ocr_import.py](erpocr_integration/tests/test_ocr_import.py) — verifies the PI creation path no longer raises AttributeError when the Custom Field isn't installed.
- conftest gains a mock for the `frappe.custom.doctype.custom_field.custom_field` import chain so the install module is importable in the test env.

## [1.1.5] — 2026-05-27

Fleet Vehicle tagging at OCR review time. Pairs with `fleet_management` v0.11.2 (`allow_on_submit` on `Purchase Invoice.custom_fleet_vehicle`, for retagging already-submitted PIs).

### Tag a Fleet Vehicle on the OCR Import → flows to the created Purchase Invoice
- New optional `fleet_vehicle` Link field (→ Fleet Vehicle) on [OCR Import](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json), in the supplier section so the operator sets it during supplier-match review. Not `reqd` — most scans aren't vehicle-related.
- [ocr_import.py](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py) `create_purchase_invoice()` carries the tag onto the PI as `custom_fleet_vehicle` so vehicle-specific spend (repairs, tyres, service) lands in `fleet_management`'s per-vehicle cost reports without the operator having to open the draft PI and fill it in. Set only when a tag is present **and** Purchase Invoice has the field (runtime `has_field` guard — same pattern as `ocr_fleet_slip`). Whitespace-only values are `.strip()`-treated as untagged; left NULL (never `""`) when blank.
- Graceful when `fleet_management` isn't installed: the field is hidden via an async `frappe.db.exists` → `toggle_display` in [ocr_import.js](erpocr_integration/public/js/ocr_import.js) `refresh` (not `depends_on`, which can't synchronously evaluate the Promise that `frappe.db.exists` returns), and the server-side `has_field` guard skips the write. No import/dependency on `fleet_management`.
- Scope (deliberate): no auto-population from supplier/line text (operator is source of truth), Purchase Invoice path only (no PR/JE), no backfill of historical OCR Import records.
- Codex external review pass: 8/10 PASS + 2 CONCERN, both addressed (whitespace guard, reliable hide). 648 tests pass (+4); ruff + ruff-format clean.

## [1.1.4] — 2026-05-26

Drive-scan hardening + OCR-aware plate canonicalization, surfaced by inspecting live prod (`erp.starpops.co.za`). Three independent fixes plus a fleet-workflow docs correction.

### Drive scan: persist Error placeholder on pre-extraction failures
- Prod was logging the same `Empty content for Scanned_20260320-0831.pdf` Error Log entry every 15-minute fleet poll (~28×/day for >2 months, 200 entries in the last 7 days alone). Root cause: in [drive_integration.py](erpocr_integration/tasks/drive_integration.py), when `_download_file` returned empty / the file was oversize / magic bytes mismatched, the scan handler logged an error and returned `False` **without** creating a placeholder OCR Import (or OCR Delivery Note / OCR Fleet Slip). The existing `MAX_DRIVE_RETRIES=3` dedup branch counts rows by `drive_file_id` — with no row, every poll re-discovered and re-failed the same bad file forever.
- New `_record_drive_scan_failure()` helper inserts a `status=Error` placeholder with `drive_file_id`, `drive_retry_count`, and `error_log` set so the dedup branch on the next poll counts it and eventually skips the file after `MAX_DRIVE_RETRIES`. Wired into all three scan paths (invoice `_process_scan_file`, DN `_process_dn_scan_file`, fleet `_process_fleet_scan_file`) at all three failure branches (empty / oversize / bad magic bytes).
- Tolerates the per-doctype field shape diff: OCR Fleet Slip lacks `source_filename`; its `error_log` is Small Text rather than a Link to Error Log.
- Total downloads on a permanently-bad file are now bounded at `MAX_DRIVE_RETRIES + 1 = 4`, then silently skipped until the file is manually removed from the scan folder.

### `_fuzzy_match_vehicle` adds OCR-canonical scoring tier
- The existing fuzzy matcher (from 1.1.1) caught single-character Gemini misreads but not double-confusable plates. Prod had 9 OCR Fleet Slip records stuck in Needs Review from plates like `CXXS79C` (S↔5 AND L↔C — raw SequenceMatcher score 0.71, below the 0.78 threshold) — all real `CXX579L` slips that the dedup couldn't recognize.
- `_fuzzy_match_vehicle` in [fleet_api.py](erpocr_integration/fleet_api.py) now scores each candidate **twice** — raw normalized form, and an OCR-canonicalized form folding `S↔5 / L↔1 / B↔8 / O↔0 / Z↔2 / G↔6 / I↔1 / Q↔0` via a new `_canonicalize_plate()` helper — and takes the higher ratio. `CXXS79C` → canonical `CXX579C` vs canonical `CXX5791` = 0.857, passes the threshold.
- Same 0.78 threshold and same three Codex-review ambiguity guards (length / plausibility-band 0.15 / tight-ambiguity 0.05) still apply on top. Genuinely different plates like `CKK879L` stay below threshold (canonical 0.571). Two real plates that canonicalize to the same form (e.g. `CXX579L` + `CXX579C` both active when OCR yields `CXXS79C`) are correctly refused by the plausibility-band guard rather than silently mis-matched.

### `move_file_to_archive` tolerates Drive 404
- A `Drive Move Error` was firing on prod when a service-account-owned file got moved/deleted out-of-band — `HttpError 404` from `files().get` was caught by the generic `except Exception` and logged as a move failure. Now the 404 path is logged at `info` level and returned as already-archived; other `HttpError` statuses and non-HttpError exceptions still escalate to Error Log.
- Return shape (`{file_id, shareable_link, folder_path}`) unchanged across all exit paths.

### Fleet workflow docs corrected
- `CLAUDE.md` and `OCR_Fleet_Slip_Guide.md` previously claimed fleet slips "always create Purchase Invoice — both fleet card and direct expense vehicles create PIs". In practice the accounts team reconciles fleet-card slips against the monthly Wesbank invoice rather than creating individual PIs — matching the 32-slip / 0-PI ratio observed in prod.
- Updated to reflect that `Matched` is the primary terminal state for fleet-card slips, and `create_purchase_invoice()` is the exception path for unauthorized / off-card spend (slip_type=Other / unauthorized_flag=1). Driver-facing guide aligned similarly.

### Tests
- 644 pass (+9 new — 4 canonical-fuzzy-match incl. genuinely-different-plate rejection, 1 empty-download placeholder, 1 404-as-already-archived; +3 from parametrize expansion). `conftest.py` gains a real `_FakeHttpError(Exception)` subclass so `except HttpError` works in mocked tests.
- Codex external review pass: 7/7 PASS with zero additional findings; targeted retry-cap walk and plausibility-band-under-canonicalization scenario verified against source.

## [1.1.3] — 2026-05-12

Bulk-review ergonomics: a doc-level Cost Center selector on OCR Import so the accounts reviewer sets the cost centre once per record instead of filling it in on every line.

### Doc-level `cost_center` on OCR Import
- New optional Link field on [OCR Import](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.json) (between `tax_template` and the items section), filtered client-side to the parent's company with `is_group=0, disabled=0`.
- New precedence at every PI / PR / JE creation site in [ocr_import.py](erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py): **line override → doc-level parent → `OCR Settings.default_cost_center`**. Applied to PI per-line, PR per-line, JE per-item debit, JE tax line, and JE credit line.
- Service-mapping rows still populate line-level `cost_center` and that line override still wins — per-supplier cost-centre splits keep working; the new field is the bulk-review shortcut for everything else.
- Five new unit tests in `TestCostCenterPrecedence` cover line-wins, doc-fallback, and settings-fallback on PI; doc-fallback on PR; and doc-applied-to-every-line on JE.
- Codex external review pass: 6/6 PASS. 638 tests pass; ruff and ruff-format clean.

## [1.1.2] — 2026-05-12

Reliability fix for the Gemini ingestion path: a Drive batch on a fresh Empire Vending site surfaced `rq.timeouts.JobTimeoutException` on a large PDF because rate-limit stagger was being double-counted (caller-side `time.sleep(5)` between enqueues **and** in-job `time.sleep(queue_position * 5)` inside each worker), and the 300s job timeout left no headroom for the 5-attempt × ≤60s + ≤225s retry shape in `_call_gemini_api`. Documentation also gained a prepay-credit-depletion warning — the same incident's first failure mode was a 429 that read as a rate-limit error but was actually a depleted Tier 1 prepay balance.

### Stagger refactor — single source of truth at the caller
- Removed in-job stagger sleep from all four processor functions: `gemini_process` ([api.py](erpocr_integration/api.py)), `dn_gemini_process` ([dn_api.py](erpocr_integration/dn_api.py)), `fleet_gemini_process` ([fleet_api.py](erpocr_integration/fleet_api.py)), `statement_gemini_process` ([statement_api.py](erpocr_integration/statement_api.py)). Dropped the `queue_position` kwarg from those signatures and from four helpers in [drive_integration.py](erpocr_integration/tasks/drive_integration.py) (`_process_scan_file`, `_process_statement_file`, `_process_dn_scan_file`, `_process_fleet_scan_file`).
- Stagger now lives only at the batched ingestion callers — `poll_drive_scan_folder`, `poll_drive_dn_folder`, `poll_drive_fleet_folder`, and the email_monitor loop — which already (or now) sleep 5s between successive `frappe.enqueue` calls. Drive pollers had this caller-side sleep already; email_monitor gained a new caller-side `time.sleep(5)` gated on a fresh `enqueued_count` counter.
- Manual upload ([api.py](erpocr_integration/api.py) upload endpoint) intentionally has no caller-side stagger — UI uploads are one-at-a-time and the request-layer 429 retry handles the rare burst case without an extra layer of throttling.

### Job timeout raised from 300s to 600s on Gemini extraction enqueues
- All 10 Gemini-extraction enqueue sites (manual upload, manual retry, email monitor, four Drive scan paths, fleet retry, DN retry, fleet "Move to Invoice Pipeline" re-route) now use `timeout=600`. Covers the worst retry shape in `_call_gemini_api`: up to 4 attempts × 60s request timeout + 15s + 30s + 60s + 120s of 429 backoff sleep + a final ~60s successful call ≈ 525s.
- The unrelated PI-submit statement-reconciliation refresh in [statement_api.py](erpocr_integration/statement_api.py) stays at `timeout=300` — it doesn't run Gemini retries.

### email_monitor counter fix — skipped attachments no longer trigger a wasted 5s sleep
- Caught by Codex review pass on the v1.1.2 changes. `pdfs_to_process` was incremented before the magic-byte validation, so a failed first attachment would leave the counter at 1 and cause the next successful attachment's enqueue to sleep 5s as if it were the second item. New `enqueued_count` increments only after `frappe.enqueue` succeeds, and the stagger gate now reads `enqueued_count > 0`. `pdfs_to_process` keeps its existing semantic for the "move email to processed" decision.

### Documentation — prepay credit depletion warning
- [README.md](README.md) and [CLAUDE.md](CLAUDE.md) now warn that new Google AI Studio Tier 1 accounts default to **Prepay** mode, where a depleted balance returns `HTTP 429` with `RESOURCE_EXHAUSTED` and the message *"Your prepayment credits are depleted"* — visually indistinguishable in our retry logs from a true rate-limit error. Prevention: switch the linked Google Cloud Billing account to pay-as-you-go in AI Studio → Billing, or set a low-balance alert.
- Corrected README's stale "free tier: 15 requests/minute" to the actual 10 RPM / 500 RPD figure.

### Tests
- Dropped two `test_fleet_api` tests (`test_stagger_with_queue_position`, `test_stagger_capped_at_240`) that asserted the removed in-job `time.sleep` behaviour. 619 tests still pass; ruff and ruff-format clean.

## [1.1.1] — 2026-05-07

Operational polish for the OCR Fleet Slip Reader role and a defence-in-depth fix on `unlink_document` across all three OCR pipelines.

### OCR Fleet Slip Reader gains write permission
- The role can now resolve "Needs Review" records — correct a misread vehicle plate, mark a non-fleet slip as No Action — without holding the broader OCR Manager role. `create`, `delete`, `submit` stay zero, so the reader can't fabricate or destroy fleet slips.
- Driven by a real workflow: fleet card slips are a control register reconciled against Wesbank statements; the operations user reviewing them needs to fix Gemini misreads (registration plates on photographed slips are unreliable) but should not be able to create new slips or touch the rest of the OCR pipeline.

### `ignore_user_permissions` on `fleet_vehicle` and `cost_center`
- Added `"ignore_user_permissions": 1` to the `fleet_vehicle` and `cost_center` Link fields on `OCR Fleet Slip` so depot-scoped User Permissions on those linked DocTypes don't filter the Fleet Slip list. Fleet card slips are company-wide; a depot-scoped user monitoring fleet card usage needs to see every slip regardless of which depot owns the vehicle.
- Tight scope: only these two fields, only on this DocType. Other Link fields (`expense_account`, `fleet_card_supplier`, `purchase_invoice`, `company`) keep vanilla Frappe UP semantics. Sister app `fleet_management` follows vanilla semantics throughout — this is a deliberate, narrow opt-out.

### Privilege-escalation fix in `unlink_document` (all three pipelines)
- `unlink_document` on `OCR Fleet Slip`, `OCR Import`, and `OCR Delivery Note` now calls `frappe.has_permission(linked_doctype, "delete", linked_name, throw=True)` before `frappe.delete_doc`. Previously only the parent OCR DocType's write permission was checked — a user with parent-write but no delete permission on the linked Purchase Invoice / Purchase Receipt / Purchase Order / Journal Entry could delete a draft they otherwise couldn't touch. Reachable today via the new Reader-write on OCR Fleet Slip; latent on OCR Import and OCR Delivery Note pending any future narrow role.
- Caught by external (Codex) review pass on the v1.1.1 changes.

### Vehicle plate fuzzy matching
- `_match_vehicle` gains a third tier — `_fuzzy_match_vehicle` uses `difflib.SequenceMatcher` to rescue Gemini character-level misreads on photographed registration plates (L↔1, 5↔S, X↔N, etc.). Threshold 0.78, length-difference cap of 2, and a 0.05 ambiguity-band guard so the matcher never silently picks between two near-equal candidates. Status set to "Suggested" — user still confirms.
- Real impact from this morning's prod batch: 19 fleet slips arrived, 7 exact-matched, 12 went to Needs Review with character misreads of one fleet vehicle's plate (CXX 579 L → CMX579L, CXX5792, CXX579C, CXX529L, etc.). With this tier the four single-character misreads auto-suggest the correct vehicle; the worse misreads (BVC 558 L is a different vehicle, CXXS79C has two substitutions) correctly stay unmatched.

### "Move to Invoice Pipeline" — wrong-folder routing
- New `route_to_invoice_pipeline` whitelisted method on `fleet_api.py` plus an "Actions → Move to Invoice Pipeline" button on the OCR Fleet Slip form. Re-routes a fleet slip into the regular invoice (OCR Import) pipeline by creating a new placeholder, copying the original scan as an attachment, enqueueing the standard `gemini_process` job (invoice prompt, full supplier matching), and marking the source fleet slip as No Action with a reference to the new OCR Import.
- Why: drivers occasionally drop a non-fleet-card slip in the fleet Drive folder (e.g. fuel paid with a personal or company credit card). The fleet pipeline assumes the slip is a fleet card transaction and resolves the supplier from the vehicle config or the `fleet_default_supplier` — wrong supplier, wrong account, wrong everything for these cases. Re-routing through the invoice pipeline runs the proper supplier-matching pipeline with the actual supplier on the slip.
- Permission posture: requires write on OCR Fleet Slip AND create on OCR Import. The narrow `OCR Fleet Slip Reader` role (write on Fleet Slip only) cannot use this button — they can mark No Action and walk away, but cannot open a creation surface they shouldn't have.
- Status guard: cannot re-route from Completed / Draft Created / No Action. The status of the new OCR Import follows its own normal pipeline; the fleet slip becomes terminal No Action with the reason field linking to the new record.
- **Race guard** (added after Codex pre-deploy review): before the final `save` that flips the slip to No Action, `ocr_fleet.reload()` re-fetches the row and re-checks for terminal statuses. If a concurrent doc_event flipped the slip to Completed / Draft Created / No Action while the new OCR Import was being created, the re-route aborts with a clear error and an Error Log entry — the new OCR Import sits in its normal pipeline; the source slip is NOT overwritten. Caller sees both record names in the error message and verifies manually.

### Fuzzy plate matching — plausibility-band guard
- Added a third guard to `_fuzzy_match_vehicle` (after Codex pre-deploy review caught a real false-positive scenario): if more than one candidate vehicle scores within 0.15 of the best, the OCR input is considered ambiguous and the match is refused — even if the best candidate clears the 0.78 threshold and the second-best is more than 0.05 below it. Catches sequential-plate fleets (e.g. CXX 578 L and CXX 579 L both active) where a single-char OCR misread of one plate would otherwise silently match the wrong vehicle.
- Trade-off: in a fleet with sequential plates, fuzzy matching becomes more conservative — even genuine misreads would require manual matching when a near-twin exists. That's the right trade since posting an expense to the wrong vehicle is worse than asking the user to pick.

### Tests
- `test_fleet_slip_reader_role.test_fleet_slip_grants_read_write_to_role` — guard renamed and updated to assert `write=1` while keeping create/delete/submit/cancel/amend/export/email/share locked at 0.
- `test_fleet_controller.test_blocks_when_user_lacks_pi_delete_permission` — new regression test: simulates Reader-style perms (OCR Fleet Slip write OK, Purchase Invoice delete denied) and asserts `unlink_document` raises and does NOT call `frappe.delete_doc`.
- `test_workflow_integration.test_blocks_when_user_lacks_linked_doc_delete_permission` and `test_dn_controller.test_blocks_when_user_lacks_linked_doc_delete_permission` — same regression shape applied to OCR Import and OCR Delivery Note unlink paths.
- `test_fleet_api.test_similarity_fuzzy_*` — five new tests covering single-char rescue, ambiguity guard, threshold cutoff, short-input guard, and length-mismatch skip.

### Known issues
- Cross-Shared-Drive archive (fleet, DN, invoice scan folders → archive on a separate Shared Drive) silently fails to move files after extraction. Files stay in the scan folder; `drive_file_id` dedup prevents re-processing on the next scheduler tick, so it's noisy not broken. Suspected cause: Drive API folder search in `_get_or_create_folder` lacks `corpora='allDrives'` when archive is on a different Shared Drive. Deferred — fix needs live Drive testing not available in this release window.

### Operator note
Sites that already have Custom DocPerm rows on OCR Fleet Slip (typically created as a workaround for the v1.0.x DocPerm shadowing surfaced before this version) should delete those rows after `bench migrate` completes. The built-in DocPerm now matches the intended shape, and any Custom DocPerm rows continue to shadow the built-in array. SQL hint: `DELETE FROM \`tabCustom DocPerm\` WHERE parent = 'OCR Fleet Slip'` — review with `SELECT * FROM \`tabCustom DocPerm\` WHERE parent = 'OCR Fleet Slip'` first.

## [1.1.0] — 2026-04-28

OCR Import item matching now uses ERPNext's standard `Item Supplier` table, and learns supplier-product mappings as users confirm matches. Defaults adapt to the deploying site's data — no Starpops-specific behaviour.

### Schema
- New field `product_code` on `OCR Import Item` (Data, length 140, read-only). Stores the supplier's own SKU as printed on the invoice — previously this signal was packed into `item_name` as a matching shortcut, which has been dropped in favour of a dedicated field.
- `_populate_ocr_import` now stores `product_code` separately. `item_name` always holds the description; the old "product_code or description" coupling is gone.

### Matching pipeline
The item matching pipeline gains two new tiers. New precedence (highest specificity first):
1. **NEW: `match_item_by_supplier_part`** — looks up `Item Supplier` rows by `(parent.supplier, supplier_part_no=product_code)`. Highest precision because it's supplier-scoped and deterministic. Runs first so a correct supplier-product mapping isn't shadowed by `OCR Item Alias` (which is global, not supplier-scoped). Multi-hit policy: ambiguity is logged and skipped — matching falls through to description tiers rather than picking arbitrarily.
2. `match_item` — alias / `Item.item_name` / `Item.name` exact match on description (existing).
3. `match_service_item` — pattern-based service mapping (existing).
4. `match_item_fuzzy` — difflib fuzzy match on description (existing).
5. **NEW: `default_item` fallback** — only fires if `OCR Settings.default_item` is configured. Returns `match_status="Suggested"` so the user still confirms (and so `auto_draft` skips it). For sites that haven't set `default_item`, behaviour is unchanged from previous versions.

### Incremental learning (Item Supplier auto-populate)
- New background job `tasks/learn_item_supplier.py`. When a user confirms an OCR Import item that has all of: `item_code`, `product_code`, parent supplier, and is NOT the `default_item` — the job appends a row to ERPNext's standard `Item Supplier` child table on the matched Item, populating tier 2's lookup for future invoices.
- **Permission posture**: the job sets the originating user (`frappe.set_user`) and checks `frappe.has_permission("Item", "write")` before touching Item master. **No `ignore_permissions` on `Item.save`** — sites that don't grant Item write to OCR Manager get silent skip + log; matching still works without learning.
- **Async, deduplicated**: enqueued on the `short` queue with dedup key `item_code:supplier:product_code` so concurrent confirms collapse without races. The job ALSO does a final DB existence re-check before append (defence-in-depth).
- **Failure-tolerant**: queue glitches and Item validate failures are caught and logged, never break the user's confirm flow.
- Existing `_save_item_alias` and `_save_service_mapping` now skip when `item_code == default_item` — those mappings would be redundant with tier 5 fallback and pollute the alias / service tables with one-shot rows.

### Why this matters
Sites that populate `Item Supplier` (the ERPNext-recommended workflow) — or build it up incrementally as users confirm OCR matches — get supplier-product item matches first time, every time, with no per-supplier alias training. Sites that prefer description-only matching keep the existing tier 1/3/4 behaviour. Sites that bulk-process expense invoices can set `default_item` and skip individual confirmation clicks.

### Tests
- +20 tests across `test_matching.py` (tier 2 with multi-hit), `test_learn_item_supplier.py` (background job: permissions, dedup, idempotency, failure tolerance), `test_ocr_import.py` (on_update flow: default_item skip, enqueue, no-supplier guard, queue-failure resilience), `test_api.py` (product_code stored in own field). 449 tests pass.

## [1.0.5] — 2026-04-28

Optional `fleet_management` integration: tag fleet PIs with the matched vehicle when both apps are installed.

### Integration
- `OCRFleetSlip.create_purchase_invoice` now sets `custom_fleet_vehicle` on the created Purchase Invoice when that field exists on the doctype. `fleet_management` plants this field on PI and uses it for vehicle-level cost reports and cost-centre auto-fill. Populating it here means OCR-generated fuel/toll PIs land in those reports without users having to remember to tag the vehicle. Pure runtime feature-detect via `frappe.get_meta(...).has_field(...)` — no import or app dependency on `fleet_management`. When `fleet_management` is not installed the field doesn't exist, the conditional skips, and PI insert is unchanged.

### Operations
- New manual-trigger backfill script: `erpocr_integration.patches.v1_0_5.backfill_fleet_pi_vehicle.execute`. **Not** registered in `patches.txt` — operators run it via `bench --site <site> execute …` when ready to consolidate historical fleet PIs into vehicle reports. Scoped to `posting_date >= 2026-01-01` (fleet_management data scope) and idempotent (skips PIs already tagged or out of scope). Uses `update_modified=False` so backfilled rows don't clutter the PI audit trail.

### Tests
- +2 tests covering both branches of the feature-detect: PI dict carries `custom_fleet_vehicle` when the field is present; omits the key cleanly when absent.

## [1.0.4] — 2026-04-24

Fixes from a dual-model review pass (second-opinion audit surfaced gaps the first review missed).

### Security
- All `create_*` whitelisted methods on OCR Import, OCR Delivery Note, and OCR Fleet Slip now explicitly check `frappe.has_permission(<source>, "write", self.name)` before creating the downstream document. `frappe.client.run_doc_method` only checks read permission by default, so a user with read-but-not-write on an OCR record could otherwise trigger document creation without being able to edit the source. Not a reported exploit — defence in depth.

### Correctness
- Statement auto-refresh on Purchase Invoice submit/cancel is now enqueued on the `short` queue (was synchronous). For a supplier with 50+ Reconciled statements, a PI submit previously re-reconciled all of them inside the submit transaction; now the submit returns immediately and reconciliation happens out-of-band. `update_statements_on_pi_submit` / `_on_pi_cancel` swallow enqueue failures so a flaky worker can't block PI submit.
- `reconcile_statement()` now bounds its Purchase Invoice fetch to the last 365 days when statement period dates are missing. An unbounded `get_all` could return 10k+ rows for a busy supplier without a statement period set.

### Hardening
- `purchase_receipt_link_query()` caps the user-supplied `txt` at 80 chars and escapes LIKE wildcards (`%`, `_`, `\`) before the LIKE scan. Prevents a malicious or garbage search string from forcing a full-table scan or silently mismatching via embedded wildcards.
- `get_ocr_stats()` now validates the date range: unparseable input is rejected, inverted ranges (from > to) are rejected, and ranges over 365 days are rejected. Previously any string reached `get_all` and downstream.

### Idiom
- `OCRServiceMapping.validate()` now wraps its cross-company expense-account error in `_()` (was a bare f-string). Matches project convention.

### Tests
- +4 tests covering the new stats_api date-range validation + enqueue-based statement refresh. 591 tests pass.

### Not changed — reviewed and deferred
- Drive polling unbounded run-time (Codex M4) — real but needs architectural decision on per-run budget vs. per-file timeout.
- Drive SDK missing per-request timeout (Codex M5) — Google SDK has internal timeouts; deferring until we observe a hang in prod.
- Fuzzy matching loads all suppliers/items per OCR line (Codex M9) — would need incremental indexing; current volume doesn't warrant it.
- Email `\Seen` race on failed move (Codex M1) — existing behaviour is intentional: email is a one-shot ingestion, OCR Import is the persistent retry unit. Failed Gemini calls retry via the OCR Import "Retry Extraction" button, not by re-reading the email.

## [1.0.3] — 2026-04-23

### Added
- New role **OCR Fleet Slip Reader** — narrow, read-only access to `OCR Fleet Slip` only. Intended for cross-app integration with `fleet_management`, where the Fleet Manager needs to click through from the Fuel Efficiency Tracker to a fuel slip scan for fraud review (Wesbank-reported litres vs driver's handwritten correction). Grants `read + report + print` on `OCR Fleet Slip` and **nothing else** — explicitly excluded from `OCR Statement`, `OCR Supplier Alias`, `OCR Delivery Note`, `OCR Import`, and every other OCR doctype so supplier/invoice data stays private. Shipped as a fixture; assign via `User → Roles` after migrate. Scope is locked down by a regression test (`tests/test_fleet_slip_reader_role.py`) that fails the build if the role appears anywhere beyond its intended doctype.

## [1.0.2] — 2026-04-23

### Fixed
- Item codes selected manually on an OCR Import now flow through to the created Purchase Invoice / Purchase Receipt correctly. If the user had previously run "Match PO Items" or "Match PR Items" and then changed an item_code on the OCR row, the draft PI/PR was being created with the *old* item_code because ERPNext's PI-from-PR sync resynced it from the stale `pr_detail` / `po_detail` ref. Both the client and server now drop those refs when the OCR row's item_code no longer matches the referenced PO/PR item's item_code.
- Manual supplier and item overrides now teach the system. Previously, the alias-save on_update gate required `match_status == "Confirmed"`, but the UI never flipped match_status when the user picked a value manually — so the same OCR text hit the matcher as Unmatched forever. The form now auto-sets `match_status = "Confirmed"` (and `supplier_match_status = "Confirmed"`) when the user picks from the Link dropdown, so the alias saves on the next write.

## [1.0.1] — 2026-04-22

### Fixed
- Bulk actions on OCR Import list view were clobbering the existing listview config (`add_fields`, `get_indicator`, currency formatter). Consolidated everything into the auto-loaded `erpnext_ocr/doctype/ocr_import/ocr_import_list.js` and removed the unused `doctype_list_js` hook entry.
- Bulk Create Purchase Invoice now runs `check_duplicates` on each selected record up front and surfaces any hits in a single confirm dialog. Records with duplicates are skipped and reported in the summary — matching the single-record form flow, preventing silent duplicate PI drafts.

## [1.0.0] — 2026-04-22

First stable release. The full pipeline — invoices, delivery notes, fleet slips, and statements — has been in active production use, and the Phase 9 reliability hardening closed the last outstanding item on the roadmap. 578 tests pass, CI green, no known regressions from the 0.9.x line.

**No behavioural changes vs 0.9.1.** This is a version-number promotion to signal API and workflow stability.

## [0.9.1] — 2026-04-22

### Fixed
- Email monitor: added `\Seen` removal guard. Phase 2 now re-selects the folder in read-write mode whenever any emails were examined (not only on successful moves), and runs `STORE -FLAGS \Seen` on every fetched UID that was NOT moved. Prevents misbehaving IMAP proxies from stranding failed emails out of the UNSEEN search.

## [0.9.0] — 2026-04-22

### Added
- Email move now uses standard IMAP `COPY` + `STORE \Deleted` as the primary path, with Gmail's X-GM-LABELS extension as a fallback for label-only setups. More reliable across Gmail Workspace variants and native IMAP providers.
- Statement auto-refresh: Purchase Invoice `on_submit` / `on_cancel` re-runs reconciliation on any OCR Statement in status "Reconciled" for that supplier. Reviewed statements are left untouched; failures never block the PI submit.

### Changed
- Stats dashboard role widened from `System Manager` only to `System Manager` + `Accounts Manager` (owner/finance roles). OCR Manager (operations) stays off the dashboard.

## [0.8.4] — 2026-04-14

### Changed
- `rereconcile_statement()` and `OCRStatement.mark_reviewed()` now guard with explicit `frappe.has_permission("OCR Statement", "write", name)` up front, matching the explicit-guard convention used by every other whitelisted endpoint.

## [0.8.3] — 2026-04-14

### Added
- `check_duplicates()` now also queries `Purchase Invoice` by `bill_no + matched supplier`, scoped to company and excluding the OCR Import's own linked PI. Accounts see duplicates on form load instead of discovering them when clicking Create.

### Fixed
- Item-code leak into PI/PR description. The restore loop now uses `description_ocr` → `item_name` (only when edited away from raw `item_code`) → empty. For invoices that carry only a product code, the Item master's description wins instead of the product code overwriting it.

## [0.8.2] — 2026-04-13

### Added
- Statement user guide (`OCR_Statement_Guide.md`).

### Changed
- Business names sanitized from the public repo.
- Statement pipeline error handling hardened; tax-template deduplication helper shared between PI and PR creation paths.

### Fixed
- Retry endpoints for Import/DN/Fleet now clear stale supplier/vehicle/item links and child tables before re-extraction (prevents stale data from persisting across retries).
- Fleet stale-link clearing completed with regression tests.
- DN Purchase Order fallback and fleet vehicle guard tightened.

## [0.8.1] — 2026-04-10

### Fixed
- Shipped missing `ocr_statement_item.py` controller module.

## [0.8.0] — 2026-04-08 — Phase 8: Statement Reconciliation

### Added
- OCR Statement + OCR Statement Item DocTypes (period, opening/closing balance, reconciliation status per line).
- Gemini-based document classifier routes each Drive scan to invoice or statement pipeline (defaults to invoice on any error).
- `extract_statement_data()` in `gemini_extract.py` with statement-specific prompt and schema.
- `statement_api.statement_gemini_process()` background job (extraction + matching + reconciliation).
- `tasks/reconcile.py` — matches statement lines to Purchase Invoices by supplier + `bill_no`, using `normalize_for_matching()` for reference variations (`INV/00123` vs `INV-00123`).
- Reverse check — flags ERPNext PIs in the statement period that aren't on the statement (gated on both `period_from` and `period_to` being present).
- `classification_result` + `classification_confidence` audit fields on both OCR Import and OCR Statement.
- `MAX_DRIVE_RETRIES = 3` retry cap applied to statements.

## [0.7.0] — 2026-04-03 — Phase 7: Auto-Draft + Stats Dashboard

### Added
- `tasks/auto_draft.py` — confidence check (alias/exact matches only), doc type detection, PO auto-link, orchestration.
- `enable_auto_draft` checkbox in OCR Settings (opt-in, defaults off).
- `auto_drafted` + `auto_draft_skipped_reason` fields on OCR Import for audit trail.
- Auto-draft hooked into `gemini_process()` after matching — low-confidence records fall through to "Needs Review" unchanged.
- `stats_api.py` — whitelisted aggregation endpoint gated to owner/finance roles.
- `erpnext_ocr/page/ocr_stats/` — Frappe page with counts, auto-draft ratio, fallback reasons, per-supplier throughput.

## [0.6.6] — 2026-03-27

### Fixed
- Auto-match PO/PR item refs when user selects a PO but skips the Match dialog — prevents orphaned PO linkage on PI/PR/DN creation.

## [0.6.5] — 2026-03-25

### Fixed
- Drive query injection hardened.
- Fleet try/except scope corrected.

## [0.6.3] — 2026-03-22

### Fixed
- Item name now truncated to 140 chars in all document creation paths (PI/PR/JE/PO) to prevent `CharacterLengthExceededError`.

## [0.6.2] — 2026-03-20

### Fixed
- Journal Entry tax double-counting corrected.
- Fleet retry race condition eliminated with try/except wrap on enqueue.
- Confidence score parsing hardened.

## [0.6.1] — 2026-03-18

### Fixed
- Item name truncation to 140 chars (first pass — completed in 0.6.3).

## [0.6.0] — 2026-03-15 — Phase 6: OCR Fleet Slip

### Added
- OCR Fleet Slip DocType (single transaction — no child table).
- Gemini fleet slip extraction (fuel/toll/other classification, vehicle registration).
- Drive integration: `fleet_scan_folder_id` in OCR Settings, reuses existing archive folder.
- Fleet processing pipeline: `fleet_gemini_process()`, `_populate_ocr_fleet()`, `_run_fleet_matching()`.
- Vehicle matching: registration → Fleet Vehicle → auto-set posting mode + accounts.
- Per-vehicle posting mode: Fleet Card (supplier from vehicle) vs Direct Expense (default supplier from settings).
- Custom fields on Fleet Vehicle via fixtures (`custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`).
- Always creates Purchase Invoice; supplier resolved from fleet card provider or default supplier.
- Unauthorized purchase flagging (slip_type = Other → orange warning).
- Full No Action workflow, Unlink & Reset, Retry Extraction, doc_events hooks, client script.
- OCR Settings: `fleet_scan_folder_id`, `fleet_fuel_item`, `fleet_toll_item`, `fleet_default_supplier`, `fleet_expense_account`.
- Workspace: OCR Fleet Slip shortcut + link.

### Changed
- Refactored to remove JE path from fleet slips — always creates Purchase Invoice.

## [0.5.0] — 2026-03-08 — Phase 5: OCR Delivery Note

### Added
- OCR Delivery Note + OCR Delivery Note Item DocTypes (no financial fields).
- Gemini DN extraction (delivery-note-specific prompt, schema, transform).
- Drive integration: separate `dn_scan_folder_id` / `dn_archive_folder_id` in OCR Settings.
- DN processing pipeline: `dn_gemini_process()`, `_populate_ocr_dn()`, `_run_dn_matching()`.
- Create Purchase Order from DN (draft, rates filled by accounts team).
- Create Purchase Receipt from DN (rates from linked PO or item master).
- PO matching (qty-focused: DN qty vs PO remaining qty).
- No Action workflow for non-DN scans.
- Unlink & Reset for draft PO/PR; doc_events hooks for PO/PR submit/cancel.
- Client script: Create dropdown (PO/PR), Find Open POs, Match PO Items, No Action.
- Scan attachment copied to created PO/PR.

## [0.4.0] — 2026-02-28 — Phase 4: User-Driven Workflow + PO Linking + JE

### Added
- Journal Entry creation path for expense receipts (with account validation).
- Purchase Order linking (find open POs, match items, apply refs).
- Purchase Receipt linking for PI creation (constrained by PO, closes full PO→PR→PI chain).
- Server-side guards hardened (document_type enforcement, cross-doc duplicate lock).
- Stale field clearing (supplier/PO/PR cascade).
- Migration patch: normalize document_type on in-flight records.
- Full test suite (251 tests — unit + integration + workflow).
- Image support: JPEG and PNG accepted alongside PDF (upload, email, Drive scan).

### Changed
- Blank `document_type` default (user must explicitly select PI/PR/JE — no auto-detection).
- Removed auto-creation of documents: no more `_auto_create_documents` / `_detect_document_type` — user action required.

## [0.3.0] — 2026-02-15 — Phase 3: Polish & Enhancements

### Added
- Fuzzy matching with configurable threshold (difflib SequenceMatcher).
- Tax template mapping (auto-set VAT vs non-VAT based on tax detection).
- Service mapping (OCR Service Mapping doctype — pattern → item + GL + cost center).
- Multi-invoice PDF support (one PDF → multiple OCR Imports).
- Google Drive folder polling (15-min scan inbox + move to archive).
- Google Drive archiving (Year/Month/Supplier folder structure).
- Batch upload via Drive scan folder (drop multiple PDFs, auto-processed).
- OCR confidence scores (Gemini self-reported, color-coded badge on form).
- Dashboard workspace (number cards, status chart, shortcuts, link cards).
- Default Item for unmatched lines (configurable in OCR Settings).
- Purchase Receipt creation method (`create_purchase_receipt`).
- OCR Manager role for access control.

## [0.2.0] — 2026-02-01 — Phase 2: Gemini Integration

### Added
- Gemini 2.5 Flash API integration (`gemini_extract.py`).
- Manual PDF upload via OCR Import form with real-time progress.
- Whitelisted upload endpoint with file validation (PDF/JPEG/PNG, ≤10MB, magic bytes).
- Background processing with `frappe.publish_realtime()` progress updates.
- Email monitoring (hourly scheduled job).
- Supplier + item matching (alias → exact → service mapping → fuzzy).
- Purchase Invoice draft auto-creation.
- Error Log integration and retry logic (429-specific long backoff + shorter 5xx backoff).

### Removed
- Nanonets integration (replaced by Gemini).

## [0.1.0] — 2026-01-15 — Phase 1: Nanonets Pipeline (deprecated)

### Added
- Webhook-based Nanonets OCR → ERPNext Purchase Invoice pipeline.
- Supplier and item matching with alias learning.
- Automatic draft PI creation with tax, currency, and PO linkage.

[1.4.1]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.4.1
[1.3.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.3.0
[1.0.4]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.0.4
[1.0.3]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.0.3
[1.0.2]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.0.2
[1.0.1]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.0.1
[1.0.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v1.0.0
[0.9.1]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.9.1
[0.9.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.9.0
[0.8.4]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.8.4
[0.8.3]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.8.3
[0.8.2]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.8.2
[0.8.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.8.0
[0.7.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.7.0
[0.6.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.6.0
[0.5.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.5.0
[0.4.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.4.0
[0.3.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.3.0
[0.2.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.2.0
[0.1.0]: https://github.com/wphamman/erpocr_integration/releases/tag/v0.1.0
