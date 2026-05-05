# Changelog

All notable changes to the ERPNext OCR Integration app are documented here. Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
