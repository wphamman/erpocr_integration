# Changelog

All notable changes to the ERPNext OCR Integration app are documented here. Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
