# Deploy-request — erpocr_integration v1.13.0 · **routine** (no behaviour change until a site switches it on)

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-10-06

- **Ref:** tag `v1.13.0`. Tag object `933895f`, **peeled `34b71e3`**. `origin/master` == `34b71e3` at tagging. CI green.
- **Hop:** **v1.12.0 → v1.13.0**, both sites (both run 1.12.0 since the 2026-09-21 train).
- **MIGRATE: yes.** It adds:
  - one new child doctype, `OCR Duplicate Bill Approver`;
  - three OCR Settings fields (off by default);
  - **four Custom Fields on Purchase Invoice** (plain fields at the end of the Supplier Invoice section);
  - one post-model-sync patch, which re-anchors our Fleet Vehicle section.

  **No build. No config at deploy.**
- **Rollback:** re-deploy `v1.12.0` (`43ef66a`). The new fields stay, unused. The validate hook is gone with the code.
- **erp-test (v16):** `version-16` is now `201fce7` (master + pin only).

## What it is
**v1.13.0 — re-used supplier invoice numbers are blocked in the app, and a named approver can allow one with a reason
(Q20 → ADR-0024; Willie ruled 2026-10-06).** It replaces the habit of adding "-1" to the supplier's real number to get
past a 2023 database-level "Unique" setting. Those suffixes:
- were added to 301 SP and 73 Cactus invoices;
- break statement reconciliation;
- in 8 cases let the same amount be paid twice.

ERPNext's own same-supplier, same-year check stays on. **Off by default: deploying changes nothing anyone sees** beyond
an unticked "Approve duplicate invoice number" box on Purchase Invoice. Also fixes Q19: our fleet-card fields on
Fleet Vehicle move out of fleet_management's collapsed "Cartrack Raw Data" heading into our own section.

Review:
- Sonnet builder; Terra (GPT-5.6) + Grok 4.5 review; a Terra re-pass with all findings fixed.
- Real-bench smoke on the SP prod copy: 13/13 with the Unique setting present, 5/5 without it.
- ADR-033 browser pass as a clerk and an approver test user.
- 1,014 → **1,060 tests**, ruff clean.

## Steps
1. Deploy `v1.13.0` on both sites (get-app at the tag; **migrate**).
2. **Smoke:** open any Purchase Invoice draft as Danell. It looks as before, plus one unticked "Approve duplicate
   invoice number" box under Supplier Invoice Date. Save it: it saves.
3. **Architect verification (read-only):**
   - `get_versions` → 1.13.0;
   - `enable_duplicate_bill_check` = 0 on both sites;
   - Fleet Vehicle `custom_ocr_section` anchored on `driver_name`;
   - patch `v1_13_0.reanchor_fleet_ocr_section` in the Patch Log.

## After landing — per site, in THIS order (Willie decides when; NOT part of the deploy)
1. **Willie / a System Manager:** OCR Settings → *Duplicate supplier invoice numbers* → add the approver(s) (Willie's
   choice: Danell) → tick *Block duplicate supplier invoice numbers*. The status panel will show **red** for the
   database "Unique" setting. That is expected until step 2.
2. **Starktail, after step 1:** Customize Form → Purchase Invoice → *Supplier Invoice No* → **untick Unique** → Update.
   Verified on the prod copy: this drops the database index by itself; no SQL needed. Optional check:
   `SHOW INDEX FROM \`tabPurchase Invoice\`` shows `bill_no_index` (non-unique) only.
3. The status panel turns **green**.

**Never step 2 before step 1.** Unticking first would leave only ERPNext's same-supplier, same-year check, which is
less protection than today.

**Status:** REQUESTED 2026-10-06.

## Addendum 2026-10-06 (evening): Empire Vending, a third site
Willie confirmed `erp.empirevending.co.za` runs only erpocr (Frappe Cloud, own server). Coordinator probe with Willie's OK: erpocr
1.12.0 on frappe 15.120.0 / erpnext 15.121.0 (newer than SP/Cactus), no fleet_management. **v1.13.0 is safe there as the same
1.12.0 → 1.13.0 hop**: the framework internals it relies on are identical at those versions (upstream source diffed), and the
Fleet patch and fields skip a site without Fleet Vehicle. Post-deploy: 1.13.0, `enable_duplicate_bill_check` = 0, patch logged.
Who deploys there is Willie's call.
