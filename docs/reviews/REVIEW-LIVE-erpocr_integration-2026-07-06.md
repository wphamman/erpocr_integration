# Live System Review — erpocr_integration

**Date:** 2026-07-06 · **Reviewer:** Claude Code (read-only health assessment)
**Version reviewed:** 1.4.1 (branch `harden-image-decode-verify`, HEAD `656fafb`, one unreleased commit ahead of tag `v1.4.1`)
**Method:** full source read (5 parallel dimension agents) + baseline test run + static client-JS review. **No code changed.**
**Access:** code + local test suite (green) + **read-only REST queries against both prod instances** (`erp.starpops.co.za`, `erp.cactuscraft.co.za`). Prod data is folded into B2/B3 and the V1 finding. Every finding is grounded in code and/or live data; confidence is marked per item.

---

## Part A — State of the system

**What it does.** A Frappe v15 custom app that runs Gemini 2.5 Flash over PDFs/images to draft ERPNext documents across four pipelines — invoices (→ PI/PR/JE), delivery notes (→ PO/PR), fleet slips (→ PI or fleet-card control record), and statement reconciliation. Ingest via manual upload, email, Google Drive polling, and a driver-shell phone-upload contract. ~$0.0001/doc. Star Pops' accounts and factory workflows depend on it for data entry into ERPNext; drivers feed it fuel/toll slips.

**Baseline health (measured).**
- **Tests:** 724 passing in 1.4s, wholesale-mocked `frappe` (unit-style). Working tree clean.
- **Lint/build:** not run (no bench/venv here); the suite is the safety net locally, with `frappe-pre-deploy-gates` submit-smoke as the real integration backstop.
- **Structure:** ~10k LOC source + ~11k LOC tests. Clean separation (api / tasks / doctype controllers / client JS). Whitelist surface: 30 endpoints, inventoried and assessed (Part B, Security).
- **Prod (read-only, both instances):** Star Pops 924 OCR Imports, Cactus 376 — **no records in `Error`, `Pending`, or impossible states**; the v1.2.0 fleet NULL-PI invariant holds on real data. No fire to put out.

**Verdict: SOLID, with one quietly-wrong accounting path to fix.**
This is a mature, well-tested, well-hardened app — the security boundaries, idempotency contracts, cross-app feature-detection, and client UX are genuinely good, and most of what follows is refinement, not repair. **The one exception that rises above "debt" is VAT-on-customs/freight invoices (V1): it produces silently-wrong GL on your highest-value import invoices with nothing on the face of the document to flag it.** That, plus a multi-invoice partial-commit gap and a standalone-install fixture bug, are the items worth a build session soon. Nothing here is corrupting data in flight right now, so there is no emergency — but V1 has been mis-stating expense/VAT on every affected invoice to date and warrants prompt correction and a look-back.

---

## Part B — Findings

Severity: **Urgent** (active prod risk) / **Should-fix** (real debt or degradation ahead) / **Nice-to-have**. Confidence: Confirmed / Probable / Suspected.

### B1. Correctness under real conditions

#### ★ V1 — VAT rendered as a line item is mis-booked (Cargo Compass / Cactus customs & freight) — **Should-fix (accounting-Urgent), Confirmed**
*Where:* no guard at any layer — `tasks/gemini_extract.py:137-154` (prompt), `api.py:396-401` (template auto-select), `ocr_import.py:503-620` (PI item loop), `:159` (`_detect_tax_inclusive_rates`), `:896-948` (JE).
*Evidence:* A grep for any VAT/tax-line keyword filter or `is_tax` flag across the whole app returns nothing. The prompt tells Gemini to extract **every** table row as a `line_item` and separately to pull a "tax total" into `tax_amount`; nothing routes a row labelled "Import VAT"/"VAT" out of `line_items`. `create_purchase_invoice()` turns every extracted line into a PI item verbatim. Template selection is purely `tax_amount > 0 ? default_tax_template : non_vat_tax_template` (`api.py:398-401`). Two failure modes, both real:
- **Case A (silent — the dangerous one):** VAT lands only in `line_items`, `tax_amount = 0` → the **non-VAT** template is chosen → the VAT amount is booked to an **expense account** (via matched item or the `default_item` catch-all). **VAT input is never claimed; expense is overstated.** The PI grand total *still reconciles* to the invoice (the VAT line is in the item sum), so nothing looks wrong at review — the GL is quietly wrong on every affected invoice. JE path (`ocr_import.py:896-932`) has the identical defect.
- **Case B:** VAT appears in *both* `line_items` and `tax_amount` → the **VAT template applies 15% on top** of items that already include the VAT line → **double taxation and the PI fails to reconcile.** `_detect_tax_inclusive_rates` is also poisoned (its `sum(qty*rate)` now includes the VAT line), biasing inclusive/exclusive classification.
*Business impact:* recurring, material misstatement on customs/freight imports — precisely the class you flagged. Case A is undetectable at review; a look-back on Cargo Compass PIs is warranted.

*Confirmed against a real invoice (Cargo Compass JI279503, 2026-06-26) + the operator's target-state PI (screenshot).* The invoice splits cleanly by **per-line VAT code**:
- **`Z` (zero-rated) service lines** — the 12 charges (SLINE/DOCO/RHAUL/GENSET/AGENCY/BAILEE…) = **R57,614.30** = the invoice SUBTOTAL. These are the genuine cost lines → PI items.
- **`E` lines** — the two `CVAT CUSTOMS VAT ON GOODS` rows (54,699.45 + 9,339.45) = **R64,038.90** = the header VAT. This is recoverable **import VAT** paid to customs on the importer's behalf — not a service.

*Target state (operator does this by hand today):* items = R57,614.30; **one `Actual` tax row** against `9500/000 - VAT Control`, rate 0, amount **R64,038.90**; grand total R121,653.20; template **"9 - Import with Std VAT"** (Actual charge type), category "VAT (Oud) Inclusive".

*Live prod confirmation (Cactus instance, `erp.cactuscraft.co.za`):* the dominant failure mode on real Cargo Compass invoices is **the template-type mismatch, not the double-count.** Gemini actually reads these invoices well — it routes the header VAT into `tax_amount` and keeps *only* the Z-coded services as items (OCR-IMP-01892/01893: exactly 12 items = R57,614.30; OCR-IMP-01597: 4 items = R1,440). So the items are clean. But:
- `tax_amount > 0` → the pipeline auto-selects the **percentage** `default_tax_template` (`"1 - Standard VAT"`, ~15% On Net Total), and **`_build_taxes_from_template` never injects the extracted `tax_amount` into the tax row** — it just copies the template's percentage row. So the draft PI computes VAT as 15%×net, which for import VAT is wildly wrong: R216 instead of R10,912 (01597), R8,642 instead of R64,038 (01893).
- **The workaround is visible in the data:** every created Cargo Compass PI carries template `"9 - Import with Std VAT"` with an `Actual` row for the exact extracted amount against `9500/000 Vat Control`, while its parent OCR Import still records `tax_template = "1 - Standard VAT"`. **The accountant manually re-does the entire tax table on every one.** (Confirmed: PI `ACC-PINV-2026-00416` net R57,614.30 + Actual VAT R64,038.90 = R121,653.20, correct — but hand-corrected post-creation.)
- **The ratio test is a clean detector:** on all four Cargo Compass imports the VAT is 7.5×–111% of subtotal, never ~15% — so "extracted VAT ≠ standard-rate × subtotal" reliably flags an Actual-amount import VAT.
- *Scope on prod:* 4 Cargo Compass OCR Imports on Cactus, 3 already submitted to PIs (all hand-corrected). Small volume, high per-invoice value; a look-back is quick.

*Recommended fix (spec now concrete — see C1-#1), in priority order:*
1. **Inject the extracted `tax_amount` into an `Actual` tax row** for import/customs invoices, against a configured VAT-control account, using an **Actual-type import template** (`"9 - Import with Std VAT"`) instead of the percentage default. This is the core fix and directly reproduces the manual handling. Cleanest as a **per-supplier customs-broker profile** (Cargo Compass → Actual import template + `9500/000`), with the ratio test (`|tax − rate×subtotal| / tax > threshold`) as the generic fallback detector.
2. **Defense-in-depth for the double-count variant** (Case B, latent — not currently firing because Gemini keeps CVAT in `tax_amount`): add a per-line `vat_code` to the line schema + prompt (deterministic `E`/`Z`), and in a shared PI/JE helper partition `vat_code == "E"` / `CVAT` lines out of items if they ever appear there. Protects against Gemini drift on a differently-formatted clearing-agent invoice.

#### C1 — Multi-invoice partial-commit leaves orphan records (no rollback) — **Should-fix, Confirmed**
*Where:* `api.py:253-282` (loop) → `:347-357` (except).
*Evidence:* In `gemini_process`, invoice 0 is `.save()`d and invoices ≥1 `.insert()`ed into one open transaction, committed once at `:282`. If invoice N fails mid-loop, control jumps to the `except`, which does **not** `frappe.db.rollback()` — it sets the placeholder to `Error` and calls `frappe.db.commit()` at `:357`, flushing the already-inserted invoice-1 orphan. *Confirmed by reading:* the commit at 357 persists the half-written transaction.
*Failure scenario:* a 3-invoice PDF where invoice 2 is rejected → invoice 0 → `Error`, invoice 1 → committed orphan in Needs Review/Matched, invoice 2 lost. For a Drive scan all three share `drive_file_id`; the next poll sees "not all Error" → the file is **skipped forever, never archived**, orphan never cleaned. If auto-draft is on, the orphan can auto-draft a PI from a "failed" extraction.
*Fix:* `frappe.db.rollback()` as the first line of the except, or commit per-invoice inside the loop.

#### R1 — Statement recon flags prior-period (brought-forward) invoices as "Missing from ERPNext" — **Should-fix, Confirmed**
*Where:* `tasks/reconcile.py:33-50` (`all_pis` bounded by `posting_date between [period_from, period_to]`) → `:80`.
*Failure scenario:* an open-item statement lists an unpaid April invoice on the June statement; the PI's posting_date (April) is outside the window, so no candidate is found and the line is marked `Missing from ERPNext` though the PI exists. Accounts chase a non-issue. The reverse-check has the same bound, so the PI also never surfaces as "Not in Statement".
*Fix:* widen the PI candidate query beyond the statement period (or match on bill_no first, then check period), and/or add an "open item / brought forward" recon status.

#### R2 / O2 — Forward recon picks `candidates[0]` on duplicate `bill_no`; the query is unordered (v16 sort-flip risk) — **Should-fix, Confirmed**
*Where:* `tasks/reconcile.py:44` (`all_pis` has **no `order_by`**), `:77-88` (`candidates[0]`, "take the first match").
*Evidence:* `pi_by_normalized_ref` buckets multiple PIs under one normalized bill_no; the matcher takes the first with no amount/date disambiguation. Which PI wins depends on `get_all` order. **On v16 this silently changes** (default sort flips `modified`→`creation`), flipping `matched_invoice`/`erp_amount`/mismatch flags with no error.
*Fix:* add `order_by="posting_date"` + an explicit tie-break; surface a warning when >1 candidate exists.

#### M1 — Alias re-learning is silently ignored (first mapping wins forever) — **Should-fix, Probable**
*Where:* `ocr_import.py:340` (`_save_supplier_alias`), `:356` (`_save_item_alias`).
*Evidence:* dedup is `frappe.db.exists(...ocr_text)` keyed on text alone. Confirm "Widget → ITEM-A", later correct to "Widget → ITEM-B" → the correction is **dropped**; the stale alias auto-matches all future invoices at tier-2 "Auto Matched" confidence (high enough to auto-draft).
*Fix:* upsert the alias (update item_code/supplier when the row exists) rather than skip-if-exists.

#### M2 — JE tax line books only the *first* template tax account — **Should-fix, Probable**
*Where:* `ocr_import.py:938-957`. The JE path picks the first `tax_row` with an `account_head` and debits the whole `tax_amount` there. A multi-row template (VAT input + levy/withholding) collapses everything to the first account. The PI path iterates correctly; only JE has this. *Fix:* iterate and split, or throw on multi-row templates.

#### M3 — Tax-inclusive detection ignores line discounts (and any non-goods line) — **Should-fix, Probable**
*Where:* `ocr_import.py:159`. `sum(qty*rate)` ignores `item.amount`, so on line-discounted invoices the sum overstates vs subtotal and can flip inclusive/exclusive classification, silently back-calculating wrong rates. Also the vector by which V1-Case-B corrupts detection. *Fix:* sum `item.amount` (fallback `qty*rate`).

#### M4 — Cost center not validated for company; foreign-currency PI has no `conversion_rate` — **Should-fix, Suspected**
*Where:* `ocr_import.py:529-534/732-737/923` (cost center never company-checked, unlike `_validate_account`); `:587` (currency set, `conversion_rate` never set → defaults to 1.0 under `ignore_mandatory`, so base-currency amounts are wrong on the multi-currency invoices the app explicitly supports). *Fix:* company-validate the resolved cost center; set `conversion_rate` for non-base currency.

#### M5 — PI/JE never reconcile computed total to the extracted invoice total — **Nice-to-have, Suspected**
*Where:* `ocr_import.py:580-593` (PI), `:959-977` (JE). No check that Σ lines (+tax) == `total_amount`. A dropped/misread line posts a silently-wrong total (JE always balances internally, so no error). A soft warning at create time would have caught V1-Case-B and OCR arithmetic drift.

### B2. Evidence of real-world trouble — **read from prod (read-only), both instances**
*Source: live REST queries against `erp.starpops.co.za` (924 OCR Imports) and `erp.cactuscraft.co.za` (376). Star Pops is the fleet/main-OCR user; the Cargo Compass VAT issue lives on Cactus.*

**Good news — no records in impossible or stuck states.**
- Star Pops OCR Import status: Completed 630 / No Action 246 / Needs Review 39 / Matched 9 — **zero `Error`, zero `Pending`, zero `Draft Created`.** Cactus: Completed 190 / No Action 160 / Needs Review 22 / Matched 4 — same, no stuck records.
- Fleet Slip `posting_mode × status` (Star Pops): Fleet Card {Matched 34, Completed 1, Draft Created 1}, blank-mode {Matched 8, Needs Review 5}. **No "Fleet Card slip *with* a PI" anomaly** — the v1.2.0 NULL-PI invariant holds on real data.
- The correctness findings that *could* strand records (C1 orphan, stuck-`Pending`) are therefore **latent, not currently manifesting** — which lowers their urgency to Should-fix/Nice-to-have rather than active incidents.

**Confirmed live problems.**
- **V1 (VAT) is real and reaching submitted PIs on Cactus:** 4 Cargo Compass OCR Imports, 3 already submitted, every one hand-corrected from the wrong percentage template to the Actual import template (see V1). Small volume, high value, 100% rework rate.
- **34 Fleet Card slips sitting in `Matched`** on Star Pops awaiting a manual **Mark Recorded** click — an operational backlog (up from the ~16–17 noted in the v1.2.0 CHANGELOG). Not a bug, but a growing manual-step queue (feeds future-feature #2).

### B3. Usage reality vs. design intent — **measured on prod**
- **Auto-draft is effectively dead weight today.** Star Pops: 2 of 924 auto-drafted; Cactus: **0 of 376.** The whole Phase-7 subsystem (auto_draft, the default_item-learning fix, stats_api) is returning ~nothing on prod — either `enable_auto_draft` is off, or the confidence gate never trips on real invoices. *Upside:* the M1 (stale-alias) and V1 (auto-drafting a mis-VAT'd PI) auto-draft-poisoning risks are **low in practice.** *Question for you:* is auto-draft intentionally off, or trying-and-failing to fire? If the latter, it's worth either tuning or shelving — it's carrying maintenance cost for no return.
- **The P4 driver-shell upload (the flagship v1.4.0 build) has zero prod adoption:** all 49 fleet slips are `source_type = "Gemini Drive Scan"`, **none** `"Gemini Shell Upload"`. The contract is live and data-safe but no driver is using it yet — the investment isn't returning value until it's rolled out. Worth a deliberate rollout push or an explicit "parked" decision.
- **JE pipeline barely used** (1 of 924 on Star Pops) — expected (it's the niche expense-receipt path), not a concern, but confirms PI is the overwhelmingly dominant output.

### B4. Performance & scale headroom
- **No forever-growing full scans found.** Every `get_all`/`get_list` over OCR Import / File / statement tables is either filtered by a specific id (bounded) or carries `limit_page_length`. Good.
- **Poll-drain vs interval:** the three Drive polls share one `*/15` cron and each `time.sleep(5)` between enqueues. A large backlog (~180+ files) makes one run exceed 15 min. Frappe serializes same-type scheduled jobs, so this bounds the dedup race (below) but means a big backlog drains slowly. Fine now; watch at 2–3× volume.
- No N+1 or missing-index issues surfaced in the reviewed paths.

### B5. Code quality & simplification
High quality overall. Consistent guard patterns, shared helpers (`_build_taxes_from_template`, `_validate_account`), documented invariants. Small items: the `Pending`/`Extracting` status mislabel (below); an orphaned unregistered patch (O3); duplicated cost-center precedence blocks that could share a helper (low value, high change-risk — leave).

### B6. Security & access posture
**Well-hardened.** No `allow_guest` anywhere; upload validation (ext→MIME→magic-bytes→size, plus PIL decode-gate on fleet upload) is thorough; XSS is consistently `escape_html`/`encodeURIComponent`'d; the Gemini key is fetched via `get_password`, sent only in a header, never logged/returned; `stats_api` is role-gated; the `OCR Fleet Driver` role is correctly recon-only-scoped; `ignore_permissions=True` usages are all justified. Gaps:

- **S1 — `OCRImport.unlink_document` missing the source-doc write check — Should-fix, Confirmed.** `ocr_import.py:1024`. Every sibling (fleet/DN unlink, and the other OCR Import create/mark methods) opens with `has_permission("OCR Import","write",self.name)`; this one goes straight to `db_set()` + `delete_doc()`, guarded only by `delete` perm on the *linked* PI. A role with read-only OCR Import + delete PI could delete the draft and reset the record. One-line fix.
- **S2 — State-changing controller methods are not `methods=["POST"]` — Should-fix, Confirmed.** All `create_*`/`unlink_document`/`mark_no_action`/`mark_recorded`/`mark_reviewed`/`rereconcile_statement` are bare `@frappe.whitelist()` → GET-callable → bypass CSRF. Module-level upload/retry endpoints correctly set POST; the controllers don't. **Contradicts the app's own `frappe-v16-safe` convention.** One-line decorator per method.
- **S3 — `OCR Statement.raw_payload` at permlevel 0 while the other three doctypes restrict it to permlevel 1 — Nice-to-have, Confirmed.** Not a leak today (only OCR/System Manager read statements), but an inconsistent boundary that a future lower-priv statement-reader role would inherit.
- **S4 — `upload_fleet_slip` vehicle-existence oracle — Nice-to-have, Suspected.** Distinct error strings let a driver enumerate valid Fleet Vehicle names. Low sensitivity.

### B7. UI/UX (static review — REST-only, frontend not driven)
Client JS is **high quality**: contextual status intros, confirm dialogs on destructive/irreversible actions, consistent escaping, real-time progress polling, mode-branched fleet-slip guidance. The fleet-slip form in particular reads well for a non-accountant. Minor:
- **No visible "Extracting" state** — during extraction the record shows `Pending` (see below); users get a transient realtime toast but a reload shows the queued-looking `Pending`. A dead worker leaves it indistinguishable from "just queued".
- **U1 — No navigable back-link from a created document to its OCR Import — Should-fix (UX), Confirmed; you asked for this.** Today the link is one-directional: OCR Import → PI (`purchase_invoice` field), plus a *comment* on the PI holding the **Drive PDF** URL (`ocr_import.py:638-648`). There is **no field on the Purchase Invoice / PR / JE that links back to the OCR Import staging record**, so from a created/submitted document you can't click through to the OCR source (to see raw extraction, re-run, or check the match). *Fix:* add a `custom_ocr_import` (Link → OCR Import) field on Purchase Invoice / Purchase Receipt / Journal Entry, set at creation next to `self.purchase_invoice = pi.name`. OCR Import is this app's own doctype (always present), so a plain custom field is safe — no conditional-install needed. Small, low-risk. (Roadmap C2.)
- Cannot assess real friction/support-call sources without a drivable instance — flagged as a coverage limitation, not a clean bill.

### B8. Operational & upgrade readiness
- **O1 — Fixtures ship Fleet Vehicle Custom Fields unconditionally → breaks standalone install — Should-fix (Urgent if a standalone install is imminent), Confirmed.** `hooks.py:150-155` + `fixtures/custom_field.json` export 5 Custom Fields all parented on `Fleet Vehicle` (a `fleet_management` doctype). On a fresh site **without** fleet_management, fixture sync `insert()`s a Custom Field whose parent doctype doesn't exist → error. **This is the exact anti-pattern `install.py`'s own docstring warns against** ("fixtures get loaded unconditionally… raises a meta-resolution error… see v1.1.5→v1.1.6 hotfix") — yet the OCR-Import-side link field was moved to gated code while these five Fleet-Vehicle-side fields were left in the fixture. Latent because prod has fleet_management, but it breaks the repo's stated "installs anywhere" goal. `fleet_management` does *not* own `custom_fleet_card_provider`/`custom_fleet_control_account` (erpocr legitimately owns them), so the primary issue is the standalone break, not a clobber. *Fix:* move all 5 into the gated `setup_optional_custom_fields()`; drop the fixture block.
- **O2** — see R2 (unordered recon query; v16 sort-flip).
- **O3 — Orphaned backfill patch not registered — Nice-to-have, Confirmed.** `patches/v1_0_5/backfill_fleet_pi_vehicle.py` exists (with `print()`s) but is **not** in `patches.txt`, so it never runs. Confirm intent → remove or register.
- **O4 — v16 determinism sweep — Nice-to-have.** Fuzzy supplier/item match tie-breaks (`matching.py:193/211/253/271`) and vehicle exact-match (`fleet_api.py:229`) iterate unordered results; on a tie the first-seen wins and v16's sort-flip changes it. Impact is a reviewed "Suggested" match, so low blast radius — add `order_by="name"` for determinism if touched.
- **Minor ops items (Nice-to-have):** reconcile bg job (`statement_api.py:218-224`) relies on the runner's implicit commit, inconsistent with the app's "explicit commit in enqueued jobs" rule; empty `client_request_id` on Drive fleet slips relies on Frappe's `''→NULL` coercion for the unique constraint (undefended — worth a test); a slip can stick in `Pending` if the post-commit enqueue fails (no sweeper — only `Error` slips have a retry button); the `Pending`/`Extracting` mislabel (`api.py:191-192` comment says Extracting, sets Pending); a permanently-bad Drive file is never removed from the scan folder (re-scanned every poll, bounded work).
- **Logging/scheduler/patches otherwise sound:** every failure logs via `frappe.log_error` with traceback; no bare `except:` swallowing primary errors; no `print()` in runtime paths (only the unregistered patch); scheduler idempotency self-heals; registered patches are guarded and re-runnable.

### B9. Test suite as a safety net
Breadth is good (~25 files mapping cleanly to modules; existing tax path has 16 tests incl. inclusive/exclusive + real-data cases; the tz-aware `captured_at` test correctly feeds a real tz-aware value and would catch a regression). **Thin exactly where the roadmap will touch:**
1. **VAT-as-line-item — untested *and* unhandled** (couples V1). Highest-value gap.
2. **Statement duplicate-`bill_no` winner — untested** (couples R2/O2). Add a two-PI fixture; it forces the `order_by` fix.
3. **Matching 6-tier precedence end-to-end — thin** (tiers tested in isolation, not the ordering; a future reorder wouldn't fail a test).
4. **Reverse-check + strip/reseed idempotency — thin** (mutates statements on every PI submit/cancel).
5. **Drive retry-cap "3 fails then give up" transition — thin** (the backstop against infinite Gemini spend).
*Structural note (not a defect):* the suite mocks `frappe` wholesale, so it validates control flow and the dict payloads handed to `insert()` but never a real PI/JE submit — `frappe-pre-deploy-gates` submit-smoke is the correct backstop, but means V1 and tax-template edge cases won't surface in `pytest` alone.

### B10. Spec / doc defects (traps for future sessions)
- **D1** — `CLAUDE.md:10` says "Currently v1.4.0"; `__version__` and CHANGELOG are at **1.4.1**.
- **D2** — the `harden-image-decode-verify` commit (`656fafb`, 2026-06-25) is **unreleased and absent from the CHANGELOG**; knowledge docs (`architecture.md` last touched 2026-06-12) lag code by ~2 weeks (the P4/tz/decode-verify work). The SessionStart drift signal is real.
- **D3** — the CLAUDE.md invariant **"retry clears stale links" does not hold for `unlink_document`** (it clears only the link field + status, not item-level `po_detail`/`pr_detail` or header PO/PR refs). Mostly defused by the ref-vs-item_code safety checks in the create methods, but the documented invariant overstates the guarantee.

---

## Part C — Roadmap

Three separate lists. Each item is sized (S ≈ <½ day, M ≈ ½–2 days, L ≈ >2 days) with change-risk to the live system. **⚑ = needs your decision before it can start.**

### C1 — Stability & protection (protect what already works)

| # | Item | Finding | Size | Risk | Notes / acceptance |
|---|------|---------|------|------|--------------------|
| 1 | **Customs/import VAT: select the Actual template + inject the extracted VAT** | V1 | **M** | Med | Core fix (prod-confirmed): for import/customs invoices, select the **Actual** import template (`"9 - Import with Std VAT"`) not the percentage default, and put the extracted `tax_amount` into the `Actual` row against the VAT-control account (`9500/000`). Deliver as a **per-supplier customs-broker profile** (Cargo Compass) with the ratio-test fallback detector; add the `vat_code`/CVAT partition as defense-in-depth (item 1b). Accept: a Cargo Compass fixture reproduces `ACC-PINV-2026-00416` (net R57,614.30 + Actual VAT R64,038.90, template "Import with Std VAT") with **no manual correction**; reconciles to invoice total. Target-state and treatment are now confirmed by your screenshot + prod data — no accounting decision blocking, only the profile-vs-heuristic implementation choice (recommend profile). |
| 2 | **Rollback on multi-invoice partial failure** | C1 | S | Low | `rollback()` first in the except (or commit-per-invoice). Accept: a 3-invoice PDF with invoice 2 rejected leaves no orphan and the Drive file is not permanently skipped. |
| 3 | **Move Fleet Vehicle fields to gated install** | O1 | S | Low | Drop the fixture block; add the 5 fields to `setup_optional_custom_fields()`. Accept: fresh install on a site without fleet_management succeeds. |
| 4 | **Fix statement recon: prior-period + duplicate bill_no** | R1, R2/O2 | M | Med | Widen PI candidate window; `order_by="posting_date"` + tie-break; warn on >1 candidate; consider an "open item" status. Accept: a brought-forward invoice reconciles; a duplicate-bill_no case picks a deterministic, documented winner (with the new test). |
| 5 | **Security hardening: `unlink_document` write-check + POST decorators** | S1, S2 | S | Low | Add `has_permission` to `unlink_document`; add `methods=["POST"]` to all state-changing controller methods. Accept: GET on those methods is rejected; unlink requires OCR Import write. |
| 6 | **Alias re-learning upsert** | M1 | S | Low | Upsert on (ocr_text[, supplier]) instead of skip-if-exists. Accept: correcting a description→item mapping takes effect on the next invoice. |
| 7 | **JE multi-tax-account split** | M2 | S | Low | Iterate template tax rows in the JE path (mirror PI). Accept: a two-row template books both accounts. |

*Ordering:* 1 is the headline but is L and decision-gated — start the decision now, build in parallel with the quick wins 2/3/5/6/7 (all S, low-risk). 4 is independent.

### C2 — Improvement backlog (quality / perf / UX / simplification)

| Item | Finding | Benefit | Size | Risk |
|------|---------|---------|------|------|
| Tax-inclusive detection uses `item.amount` (respect discounts) | M3 | Correct tax classification on discounted invoices | S | Low |
| Cost-center company-validation + PI `conversion_rate` | M4 | Prevents deep submit-time errors + wrong base-currency on FX invoices | S | Low |
| Soft "total doesn't reconcile" warning at create | M5 | Catches OCR arithmetic drift + would flag V1-Case-B | S | Low |
| v16 determinism `order_by` sweep (matching/vehicle ties) + explicit commit in reconcile bg job | O4, ops | v16-safe, matches app's own conventions | S | Low |
| Register or remove the orphaned backfill patch | O3 | Removes dead/confusing code | S | Low |
| Reconcile knowledge layer with code (run `/update`) | D1–D3 | Stops future sessions trusting stale docs; correct the "retry clears stale links" wording | S | None (docs) |
| Test-coverage top-ups (VAT-line, dup-bill_no, tier precedence, reverse-check, retry-cap) | B9 | Safety net for the roadmap items that touch these paths | M | None |
| Visible "Extracting" state + fix the `Pending`/`Extracting` mislabel | B5/B7 | Operator clarity; enables reaping dead-worker records | S | Low |
| **Back-link field: created PI/PR/JE → OCR Import** (you requested) | U1 | One-click traceability from the accounting doc to its OCR source; audit + re-run | S | Low |

### C3 — Future feature opportunities (evidence-derived, ranked)

1. **Customs/freight (customs-broker) invoice profile** — *derived from V1.* The business problem is broader than a bug: freight/customs-clearing invoices (Cargo Compass) are a recurring, high-value, structurally-different document class (import VAT as recoverable disbursement, customs duty as cost, agency fee VATable) that the generic invoice model mishandles. A per-supplier "invoice profile" that maps known line labels → treatment (VAT-input / duty-cost / fee) would fix V1 durably *and* cut review time on your most error-prone invoices. **Value-for-effort: high** — it's the fix (C1-#1) plus a small config surface. Who benefits: accounts. Evidence: your own report + confirmed code path. Size: L (folds into C1-#1).
2. **Stuck-record visibility & sweeper** — *derived from C1, the Pending-mislabel, and the post-commit-enqueue gap.* A small dashboard/scheduled sweep surfacing OCR records stuck in `Pending`/`Error` (and Drive files that exhausted the retry cap) turns today's invisible failure modes into an operator worklist. **Value-for-effort: medium.** Evidence: three distinct code paths that can strand a record with no UI. Size: M. *(A prod probe of stuck-record counts would confirm whether this is real volume or theoretical — run the probe first.)*
3. **Statement open-item reconciliation mode** — *derived from R1.* Support open-item statements (prior-period invoices carried forward) as a first-class recon status instead of false "Missing" flags. **Value-for-effort: medium**, contingent on how many of your suppliers issue open-item statements. Size: M (overlaps C1-#4).

*Kept deliberately short.* I did not pad this with generic "systems like this usually have…" features; each of the three traces to a confirmed code path or your stated pain.

### Decisions needed from you (⚑)
1. **V1 accounting treatment — resolved.** Your target-state screenshot + the created PI `ACC-PINV-2026-00416` define it exactly: Z-services → items; extracted VAT → one `Actual` row to `9500/000 Vat Control` via the "Import with Std VAT" template. The only remaining implementation choice is **per-supplier customs-broker profile (recommended)** vs generic ratio-test heuristic — a build-time call, not a blocker.
2. **Is a standalone install (a site without `fleet_management`) a live near-term requirement?** Both prod instances co-install with fleet_management, so O1 (C1-#3) is currently latent → stays Should-fix. If you plan a standalone deployment, it rises to Urgent.
3. **Auto-draft: intentionally off, or trying-and-failing?** Prod shows ~0 auto-drafts (2/924, 0/376). Decides whether to tune, enable, or shelve the Phase-7 subsystem (it carries maintenance cost for no current return).
4. **Driver-shell (P4): roll out to drivers, or park?** Zero prod adoption today. Decides whether the v1.4.0 investment gets activated or explicitly parked.

---

## Part D — Handback

**Open questions for the architecture chat**
- **V1 accounting model** (as above) — the one decision that shapes a whole build session. Recommend confirming with the accounts team how Cargo Compass invoices *should* post before building.
- **Standalone-install posture** (O1 severity).
- **Auto-draft on prod?** If enabled, M1 (stale-alias auto-match) and V1 (auto-drafting a mis-VAT'd PI) carry more weight. The probe's `auto_drafted` count answers this.

**Production validation — done (read-only, both instances)**
- Completed via REST against `erp.starpops.co.za` + `erp.cactuscraft.co.za` (credentials from the gitignored `.env`). Results in B2/B3 and V1. Headlines: no stuck/impossible-state records; v1.2.0 fleet invariant holds; V1 confirmed on 3 submitted Cactus PIs (all hand-corrected); auto-draft ~unused; driver-shell zero adoption. The prod read **sharpened** severities and reframed V1's dominant mode (template mismatch, not double-count) — it did not overturn any finding.
- *Quick V1 look-back:* the 3 submitted Cargo Compass PIs on Cactus (`ACC-PINV-2026-00416/00305/00112`) were manually corrected, so they're likely fine — worth a spot-check that each carries the Actual "Import with Std VAT" row before the fix ships.

**Memory / doc delta candidates**
- The **V1 VAT-as-line-item** gap is a durable, non-obvious accounting invariant worth a `project` memory once the fix approach is ratified (and a CLAUDE.md gotcha).
- **O1** (fixtures vs gated install for optional-app fields) reinforces the existing `feedback_optional_app_link_as_custom_field` memory — the fixture path is the *same* trap the Custom-Field-link rule already covers; worth extending that memory to "fixtures too, not just doctype-JSON Links."
- Run **`/update`** to clear D1–D3 (version string, CHANGELOG entry for decode-verify, the "retry clears stale links" wording) — the knowledge layer lags code by ~2 weeks.

**Risks**
- **No code was changed.** Baseline tests were green before and are untouched.
- Findings are code-grounded; those marked Probable/Suspected (M4, M5, S4, and the minor ops items) would benefit from the prod probe or a targeted repro before a build session commits to them.
- The unreleased `decode-verify` commit on the current branch is in-flight work outside this review's scope; it appears sound but lacks a CHANGELOG entry (D2).

---
*Every acceptance criterion above is written to be concrete enough to serve as the spec for its future build session.*
