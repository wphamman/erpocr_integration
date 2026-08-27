# Deploy-request — erpocr_integration v1.10.3 · **floor-relevant, not EXPEDITE**

**To:** portfolio coordinator (ADR-029 routing). **From:** `erpocr_integration` architect · 2026-08-27
**Shape:** `maintenance_ui_shell/docs/deploy-requests/v0.2.1.md`.

**Why floor-relevant:** the factory tablets run Hexnode kiosk mode. Measured read-only on prod
2026-08-27 (Willie-authorised): **three enabled floor drivers — `clever@`, `isaac@`, `thabang@` —
hold `OCR Fleet Slip Reader` / `OCR Fleet Driver` and therefore see the OCR Accounts tile.** On the
live build that tile has no route home except Sign out. Not a brick (logout recovers, unlike
production's v0.3.1 case), which is why this is *floor-relevant* and not EXPEDITE. Ride the next list.

- **Ref:** tag `v1.10.3` — tag object `6e125ff`, **peeled `f45b542`**.
- **Branch-reachability:** `origin/master` HEAD == `f45b542` — **tag-equal**, verified. A Press
  branch-HEAD deploy ships the tagged bytes.
- **Hop:** **v1.10.2 → v1.10.3**. Prod baseline probed 2026-08-27: `get_versions` →
  `erpocr_integration 1.10.2` on `erp.starpops.co.za` (also 1.10.2 on `erp.cactuscraft.co.za`).
- **Delta class:** Python (2 hook handlers, 5 query `order_by` pins) + React committed dist
  (ADR-0011) + docs. **No migrate needed** (`bench migrate` is harmless — no patches, no schema,
  no DocType JSON, no hooks.py change), **no config, no build** — dist is committed; CI's
  `dist-freshness` gate rebuilt and matched it. `get-app` at the tag + standard site update.
- **GATES: none.** Independent of every other release; can share any window. Both sites.
- **Deploy-bound manual steps: NONE, either direction.** Nothing to flip on deploy, nothing to
  restore on rollback. *(Standing kiosk line applies as to every floor-facing item: after the deploy,
  anyone with the OCR Accounts dashboard open on a tablet should fully close and reopen it.)*
- **Rollback target:** re-deploy tag `v1.10.2` (`7d1fa7e`). Patch-free in both directions.

**What it is (3 items, all from portfolio broadcasts, none from a user report):**
1. **`Apps` back-link** in the accounts dashboard's shared `TopNav` — plain `<a href="/apps">`
   (full page load; `/apps` is outside the SPA's `/accounts` basename), not `history.back()`
   (empty on a fresh kiosk launch). Every route renders `TopNav`, so coverage is total.
2. **v16 R11:** removed `frappe.db.commit()` from the two Purchase Invoice `doc_events` handlers for
   fleet slips. They run inside the PI's own submit transaction; committing there was wrong on v15
   too (slip status could persist past a rolled-back submit). Identical behaviour on success.
3. **v16 R8:** `order_by` pinned on 5 order-dependent `limit=1` queries (4 scan-attachment
   lookups, 1 service-mapping lookup). No behaviour change on v15.

**Steps:**
1. Deploy `v1.10.3` on both sites (get-app at tag; standard flow). Order vs other apps: irrelevant.
2. **Architect verification (read-only, no login):** `get_versions` → `erpocr_integration 1.10.3`;
   HTTP fetch of `/assets/erpocr_integration/accounts/assets/index-BzWdz_5o.js` → 200 and contains
   `"/apps"` (byte-identity vs committed dist).
3. **Smoke, 1 min, any tablet as `isaac@`:** `/apps` → OCR Accounts tile → header shows an **Apps**
   button → tap → lands on `/apps`. Previously: no such button.
4. **Smoke, accounts, 2 min:** submit any OCR-created Purchase Invoice → linked OCR Import /
   Fleet Slip flips to Completed exactly as before (item 2 must be invisible on success).
5. Completion echo welcome; the architect probes regardless.

**Rollback:** re-deploy tag `v1.10.2`.

---
**Status:** REQUESTED 2026-08-27. Not yet on the deploy list.
