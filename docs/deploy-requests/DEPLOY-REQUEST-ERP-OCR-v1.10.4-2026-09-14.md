# Deploy-request — erpocr_integration v1.10.4 · **routine**

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-09-14
**Shape:** `maintenance_ui_shell/docs/deploy-requests/v0.2.1.md`.

**Why:** the 2026-09-10 sibling-migrate seed audit. Our `fixtures/role.json` was deleted+reinserted on
every sibling app's Press migrate (measured: OCR roles' `creation` reset 2026-09-09 13:09:19 by the payroll
deploy). Grants survived only because `for_reload` skips `Role.on_trash`. Plus the unfinished half of our
own v1.1.1 cost-centre User-Permission fix. **Routine, not EXPEDITE** — no behaviour change on prod today.

- **Ref:** tag `v1.10.4` — tag object `e038a2b`, **peeled `b9a7f13`**.
- **Branch-reachability:** `origin/master` HEAD == `b9a7f13` — **tag-equal**, verified.
- **Hop:** **v1.10.3 → v1.10.4**, both sites. Baseline: `get_versions` → 1.10.3 on SP (portfolio probe
  2026-09-03) and Cactus (architect probe 2026-08-31).
- **Delta class:** Python (install seed), 3 doctype-JSON field-property edits, one fixture file REMOVED,
  docs/tests. **MIGRATE: yes** — doctype JSON changed (`ignore_user_permissions` on 3 `cost_center`
  fields); `after_migrate` now runs the create-only role seed (no-op where roles exist) and re-asserts
  `ignore_user_permissions=1` on the planted `Fleet Vehicle.custom_cost_center` (already 1 on SP prod
  since the 08-31 manual fix — idempotent). **No build** (no SPA change). **No config.**
- **GATES: none.** Independent of every other release; either order; can share any window.
- **Deploy-bound manual steps: NONE, either direction.**
- **Rollback target:** re-deploy tag `v1.10.3` (`f45b542`). Rolling back re-adds `fixtures/role.json` —
  the roles already exist, so the next migrate's fixture import delete+reinserts them once (grants
  survive, as they have all along). Not harmful, just the old behaviour.

**What it is:**
1. **Roles → create-only seed** (`install._seed_roles`, called first in `after_install`/`after_migrate`);
   `fixtures/role.json` deleted; `Role` removed from `hooks.fixtures`. Same 3 roles, same values.
   An operator's edit to a role doc now survives any app's deploy.
2. **`ignore_user_permissions: 1`** on `cost_center` in OCR Import / OCR Import Item / OCR Service Mapping
   (matching OCR Fleet Slip) and on the planted `Fleet Vehicle.custom_cost_center` (so a FRESH install —
   the v16 test site — matches prod's manually-fixed state). `company` deliberately untouched.
3. v16-un-wizarded-install safe: no install-time write references a wizard record.

**Review:** two-pass — Sonnet builder; Terra (GPT-5.6) 9/10 PASS + 1 docs FAIL (fixed on the branch);
Grok 4.5 SHIP. 916 → **926 tests**, ruff clean. CI on `b9a7f13`: see status line below.

**Steps:**
1. Deploy `v1.10.4` on both sites (get-app at tag; **migrate**).
2. **Architect verification (read-only):** `get_versions` → 1.10.4; `Role` rows for the 3 OCR roles show
   `creation` UNCHANGED by this migrate and by the *next* sibling deploy (that is the fix, observable);
   `getdoctype OCR Import` → `cost_center.ignore_user_permissions == 1`.
3. **Smoke, 1 min, as Danell:** OCR Import list loads; open any import; nothing else should differ.
4. Completion echo welcome; the architect probes regardless.

**Rollback:** re-deploy tag `v1.10.3`.

---
**Status:** DEPLOYED — both sites 2026-09-14 21:18 SAST (DEPLOY-LIST-2026-09-14 rev3, item 5). Coordinator probe-verified 2026-09-15; architect re-probed both sites 2026-09-16 (authorised read-only): get_versions 1.10.4, OCR roles' `creation` unchanged through the migrate, `cost_center` iup=1 live on OCR Import + Item.
