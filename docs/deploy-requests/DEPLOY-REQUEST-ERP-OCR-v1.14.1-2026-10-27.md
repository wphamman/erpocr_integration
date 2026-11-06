# Deploy request: erpocr_integration v1.14.1

From: erpocr_integration architect · To: portfolio coordinator · 2026-10-27

## What
- Tag `v1.14.1`: tag object `2b22462` → commit `691455d` (master HEAD).
- `version-16` branch: `38e45a2` (master merged, v16 pin kept).
- Patch release on top of 1.14.0. No new fields, no patches, no surface change. Migrate is a no-op apart from the version.
- Tests: 1,166 pass. Reviewed by Terra (gpt-5.6-terra). Its one FAIL, header-less email re-import, is fixed in `691455d` and pinned by a test.

## Why
1. **Emailed invoices that were already read are no longer skipped.** The hourly poll now takes every message in the Gmail label "OCR Invoices", oldest first, at most 20 per run. Before, it took unread mail only, and Gmail marks mail forwarded to your own alias as read. Star Pops got nothing from email between 14 and 27 Oct.
2. **Auto-draft skips a re-used invoice number cleanly while the 2023 'Unique' setting exists**, instead of failing with a database error. Live case: SP `INV-000068`. On SP the setting is already gone (11:11 today), so there this only matters as a safety net.

## Rows

| # | Site | Who | Gate |
|---|---|---|---|
| 1 | Star Pops (erp.starpops.co.za) | Starktail | **Ready.** Its label was sorted and verified read-only (20 unread messages, nothing older than 31 Aug). |
| 2 | Cactus (erp.cactuscraft.co.za) | Starktail | **HOLD: Cactus's label has NOT been checked.** Cactus email intake reads willie@cactuscraft.co.za's "OCR Invoices" label. Its last email import was 11 Oct, so read mail has probably piled up there too. Everything left in that label will be imported, 20 an hour. Before this row: Willie sorts that label (mark wanted mail unread, remove the label from the rest), or Cactus email monitoring is switched off. |
| 3 | Empire Vending (erp.empirevending.co.za, erp-small) | session, on Willie's go | Still 1.13.1. This would also bring 1.14.0, and the erp-test v16 menu proof (row 4) is still pending. EV's mailbox had no OCR labels on 10-09, so its email poll can't import anything until they exist. |
| 4 | erp-test (v16) | Dirk | Pull `version-16` `38e45a2`. Still owed from 1.14.0: the v16 menu check, then one more migrate to show it survives. Its email monitoring must stay OFF (it is a prod copy). |

**Starktail deploys master HEAD.** Master HEAD is this tag. If Starktail deploys SP and Cactus together, as last night, row 2's gate is skipped. Please make the Cactus hold explicit to Starktail, or have Willie switch Cactus email monitoring off before the deploy (OCR Settings → untick "Enable Email Monitoring").

## After landing (row 1)
The coordinator's read-only check:
- `get_versions` shows 1.14.1;
- the next hourly poll leaves the label holding only what is genuinely still to be processed;
- new email imports appear;
- no "Email Monitoring Error" rows.

The real proof is a FORWARDED (read) invoice arriving as a "Gemini Email" OCR Import.
