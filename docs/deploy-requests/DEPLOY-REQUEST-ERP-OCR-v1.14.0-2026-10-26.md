# Deploy-request — erpocr_integration v1.14.0 · erp-test FIRST (prove the v16 menu), then prod

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-10-26. **Ruled by Willie** (desk, 2026-10-26):
v1.14.0 yes to all three; Q23 "yes, and also email Danell"; Lesego "only delete draft invoices", i.e. option A.

- **Ref:** tag `v1.14.0`, object `9e9f576`, **peeled `43980c9`**, on `origin/master`. **`version-16` = `e5b6bd1`** (the tag plus the
  pin only).
- **Hop:** 1.13.1 → 1.14.0 on all sites. **MIGRATE: yes** (new OCR Settings fields, one new PI Custom Field; on v16 a Sidebar file and an
  `after_migrate` tidy-up). **No build** (the committed SPA dist changed only its `<title>`).
- **Rollback:** `v1.13.1` (`6a33044`); `version-16` → `409d2e7`. The new fields stay, unused.

## What it is
1. **Payment-failure alert (Q23).**
   - When Google refuses payment for Gemini (HTTP 402, or a pre-paid-credit 429), extraction stops cleanly.
   - **System Managers + the people in OCR Settings → "Billing alert recipients"** get an email, at most once every 6 hours.
   - The OCR Import and Fleet Slip lists show a red banner until it works again.
   - Drive scans no longer use up their 3 tries during an outage; they pick themselves up afterwards.
   - Fixed on the way: Gemini "busy" (429) and server errors (5xx) are now actually retried. They never were before.
2. **v16 OCR menu (Q22).** On v16 only: the app ships its own "ERPNext OCR" menu, retires the converted custom copy, and removes the
   empty leftover module. It is inert on v15.
3. **Wording:**
   - the dashboard is "OCR Accounts" everywhere;
   - "Approved By" shows the person's name;
   - the duplicate-number message shows money as `R 45,747.00`;
   - "Upload File" is a visible button again.
4. **Unlink & Reset:** a clerk can discard a **draft she created herself** from her OCR record, with edit rights only. Cancelled and
   other people's drafts still need Delete. No role change.

**Review:** Sonnet builder → Terra + Grok → 4 fix rounds. A **real-bench smoke on frappe 15.95** caught two defects the mocked suite
couldn't see, both fixed and re-verified:
- an empty Datetime setting reading as `0001-01-01`, which would have meant a permanent false banner plus one-file-per-poll Drive
  scanning everywhere;
- alert state lost when the mail failed.

1,062 → **1,156 tests**, ruff clean. **Not verified:** the alert email actually sending (the bench mutes mail); the v16 menu on a
real v16 site (row 1).

## Rows (in this order)
| # | site | who | ref | after landing (the architect probes) |
|---|---|---|---|---|
| 1 | **erp-test** (v16.51) | Dirk | `version-16` `e5b6bd1` | 1.14.0; `Sidebar` "ERPNext OCR" exists with `standard = 1`, app erpocr_integration; "ERPNext OCR (Custom)" neutralised; ghost Module Def gone. **Then ONE more migrate** (any app's, or a plain `bench migrate`): the Sidebar is still there. **Prod rows wait for this.** |
| 2 | erp.starpops.co.za + erp.cactuscraft.co.za | Starktail | `v1.14.0` | 1.14.0; OCR Settings has "Billing alert recipients"; the OCR Import list loads with no banner; a PI shows "Approve duplicate invoice number" as before |
| 3 | Empire Vending (erp-small) | a session, on Willie's go (ADR-037) | `v1.14.0` | same as row 2 |

## After landing (config, not deploy)
- **Star Pops:** OCR Settings → **Billing alert recipients** = Danell (Willie's ruling). The coordinator, or a System Manager, sets it.
- **Cactus / Empire Vending:** Willie names a recipient if he wants one besides the System Managers. Default: System Managers only.

**Status:** REQUESTED 2026-10-26.
