# Changelog

All notable changes to the ERPNext OCR Integration app are documented here. Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
