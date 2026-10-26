# Deploy-request — erpocr_integration v1.13.1 · **v16 blocker** for erp-test; routine for prod

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-10-09

- **Ref:** tag `v1.13.1`. Tag object `1deeb10`, **peeled `6a33044`**, on `origin/master`. **`version-16` = `409d2e7`** (the
  tag plus the pin only).
- **Hop:** 1.13.0 → 1.13.1. **No migrate needed** (no schema, no patch). **No build** (Desk JS only, served from source). A
  normal migrate is harmless.
- **Rollback:** `v1.13.0` (`34b71e3`); `version-16` back to `201fce7`.

## Why
Found on the v16 end-user walk on erp-test (frappe 16.51.0) as `test.ocr.clerk`:
1. **v16 blocker.** Service-item matching sorted with `order_by="LENGTH(description_pattern) DESC"`. v16's query builder
   rejects an expression in `order_by`, so **every upload that reaches matching ends in Error on v16** (OCR-IMP-01802). The
   app now sorts in Python. On the v15 dev bench it gives the same order as the old SQL on all 82 real generic mappings.
   v15 behaviour is unchanged.
2. **Upload screen, v15 too.** After a manual upload the form didn't refresh or show the result message, because the poll
   called a function that doesn't exist (`frappe.ui.form.get_open_form`). It now uses the open form.

1,060 → 1,062 tests, ruff clean. Small diff (matching.py +18/−3, one JS line). No external review this time.

## Rows (list them separately)
| # | site | who | ref | after landing |
|---|---|---|---|---|
| 1 | **erp-test** (v16) | Dirk | `version-16` head `409d2e7` | `get_versions` → 1.13.1; then I re-walk the upload path |
| 2 | erp.starpops.co.za + erp.cactuscraft.co.za | Starktail | `v1.13.1` | `get_versions` → 1.13.1. Routine, any train |
| 3 | Empire Vending (erp-small) | a session, on Willie's go (ADR-037) | `v1.13.1` | `get_versions` → 1.13.1 |

**Status:** REQUESTED 2026-10-09. Row 1 unblocks the erpocr v16 walk; rows 2–3 can ride the next train.
