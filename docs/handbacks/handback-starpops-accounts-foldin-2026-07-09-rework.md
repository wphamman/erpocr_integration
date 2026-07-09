# Re-handback (rework round 2) — starpops_accounts fold-in — 2026-07-09

> Follows the architect's **CHANGES REQUESTED** close. Original handback: `handback-starpops-accounts-foldin-2026-07-09.md`. Rework items 1–3 done; the live authenticated walk that was missing now PASSES. Still builder-side only — **not merged/tagged/deployed.**

---

## 1. Rework — all three items done (`7edb30a`, pushed)

Root cause (both bugs): absolute-path navigation ignoring the `BrowserRouter` basename (`/accounts` in prod; dev basename is `/`, which is why dev couldn't catch them).

1. **`QueueList.tsx` status pills** — raw `<a href={`/q/…`}>` → react-router `<Link to={`/q/…`}>` (basename-aware; mirrors `QueueCard.tsx`). Previously hit site-root `/q/…` → `website_route_rules` (`/accounts/<path>` only) 404s.
2. **`Login.tsx` post-login redirect** — `window.location.assign("/")` → `assign(ROUTER_BASENAME)` (`/accounts` in prod). Full reload kept (SWR-cache reasoning is sound; only the target was wrong).
3. **`ROUTER_BASENAME` lifted** into `frontend/src/lib/router.ts`, shared by `App.tsx` + `Login.tsx` — avoids a circular import (App imports Login) that a direct export would create.

**Left untouched (as instructed):** the two `/app/…` Desk links — `Login.tsx` forgot-password and `QueueList.tsx` row `Open ↗` — verified still absolute `<a href target="_blank">` (they correctly leave the SPA for Frappe Desk).

Rebuilt committed dist (JS hash `index-B-pKkQfU` → `index-Cf3QiPiq`; `www/accounts.html` updated). Static confirmation in the shipped bundle: `"/accounts"` baked in, zero `assign("/")`.

## 2. Test / lint / build

- **764 py tests pass**, ruff clean (Python untouched this round).
- `npm run build` success (`tsc --noEmit` clean, 49 modules).

## 3. Live authenticated walk — the gap that caused the bounce — NOW PASSES

Driven in a real browser **at the prod basename** (`http://localhost:8094/accounts`, driver-dev.local — the site where erpocr is installed) against the redeployed fixed fold-in. Authenticated as a **mock System User** (`ocr-verify@example.test`, role OCR Manager, created for the walk with a throwaway password and **deleted afterward** — no real credential, POPIA-safe):

- **(a) Fresh logged-out login → lands on `/accounts`** (not the Desk root). Confirmed: after Sign in, URL = `…/accounts`, the **Outstanding Work** Overview rendered with live counts across all three doctypes (OCR Import / Delivery Note / Fleet Slip). ✅ *(bug 2)*
- **(b) Open a queue list → click a status-filter pill → navigates, no 404.** Confirmed: Overview → OCR Import "Needs Review" (`…/accounts/q/ocr-import/Needs%20Review`), then clicked the **Matched** pill → navigated client-side to `…/accounts/q/ocr-import/Matched`, rendered the Matched list, **zero console errors**. ✅ *(bug 1)*
- **(c) Overview drill-in + row Open↗.** Drill-in confirmed (Overview cell → QueueList). Row `Open ↗` href = `…/app/ocr-import/<name>` (Desk, new tab) — correct and untouched. ✅

Also incidentally verified: the 3-doctype union gate + per-user reads (the mock non-admin OCR-Manager user saw all three doctypes' counts).

*(No row-level data reproduced here — data hygiene. Counts are dev-bench test data.)*

## 4. Branch / merge state

- **Branch `feature/starpops-accounts-foldin` @ `7edb30a`, pushed.** 6 commits; 5 ahead / 1 behind master.
- The 1-behind is master's `1b6ff5e` (ADR-0011 docs) — touches only `docs/architecture/{DECISIONS,OPEN-QUESTIONS}.md`, which this branch does **not** touch → **clean merge, no conflict**.
- Still gated to you: merge + tag v1.7.0 + the CROSS_APP_SURFACE re-baseline. I did not self-merge/tag/deploy.

## 5. Bench / housekeeping

- Bench was **down again** (host OOM'd a 2nd time) when I picked this up. Per your call I brought it up, redeployed the fix, ran the walk, then **restored**: erpocr back on master/v1.6.0, assets symlink restored, `/accounts` → 404 (original), **mock user deleted** (`MOCK_EXISTS False`), containers healthy. Bench left running.
- Rehearsal-infra flag (recurring OOM under load) is yours per the close — noting it recurred.

**Rework complete — re-handing back for merge.**
