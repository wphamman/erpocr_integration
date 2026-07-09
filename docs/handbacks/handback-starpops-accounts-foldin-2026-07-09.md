# Handback from Claude Code — fold `starpops_accounts` into `erpocr_integration` (ADR-0010 / Q5) — 2026-07-09

> **For the operator (Willie):** paste into the erpocr_integration architect session. Builder work is complete and pushed; **not** merged/tagged/deployed (your boundary). The architect runs the two-pass review's Codex pass + merges + tags.

---

## 1. Branch and commits

- **Branch:** `feature/starpops-accounts-foldin`
- **Base:** `master` @ `237aa41` (master advanced +2 mid-session — the architect's Q3-resolved docs commits landed while I worked; the refold picked them up automatically).
- **Commits this session (3, ahead of master):**
  - `50fa1fb` feat: fold starpops_accounts read-only dashboard into erpocr_integration (ADR-0010)
  - `92802f7` docs(surface): record v1.7.0 SPA fold-in — no new whitelisted surface (§3a)
  - `e5c0dbe` fix(dashboard): drop inert has_app_permission hook + correct route-gating docs (review)
- **Push status:** pushed to `origin/feature/starpops-accounts-foldin` @ `e5c0dbe`. **No PR opened** (architect merges).
- **Working tree:** clean (worktree `/home/willie/dev/erpocr-foldin` on the branch; main checkout `/home/willie/dev/erpocr_integration` restored to `master`).
- **Backup branch:** `backup/foldin-wip-pre-refold` @ `9ed7305` (local only — snapshot of the discarded stale WIP). **Keep until the fold-in is merged + verified, then prune.**

---

## 2. Files changed

`git diff --stat master..HEAD`: **28 files, +3935 / −3** (the large insert count is the committed built JS/CSS). By group:

- `frontend/` — **17 files** — the Vite+React SPA moved to repo root. `src/**` is **byte-identical to the canonical `~/dev/starpops_accounts`** (UAT-passed). Only build-wiring differs: `vite.config.ts` (`outDir`→`../erpocr_integration/public/accounts`, `sourcemap:false`), `package.json` (`name`, `--base=/assets/erpocr_integration/accounts/`), `scripts/copy-html-entry.mjs` (erpocr paths).
- `erpocr_integration/dashboard/` — **2 files** — `__init__.py` (empty) + `permission.py` (the `has_app_permission` tile gate, 3-doctype read union).
- `erpocr_integration/hooks.py` — **+25** — `website_route_rules` (`/accounts/<path>`→`accounts`), `add_to_apps_screen` (OCR Accounts tile), tile `has_permission` wiring.
- `erpocr_integration/public/accounts/` — **3 files** (committed built dist: `index.html`, `assets/index-*.js` 365 KB, `assets/index-*.css` 14 KB; **no `.map`**).
- `erpocr_integration/www/accounts.html` — SPA shell (committed, built).
- `erpocr_integration/public/images/ocr-logo.svg` — new tile icon (tile previously referenced a missing logo).
- `erpocr_integration/__init__.py` — `1.6.0`→`1.7.0`.
- `CHANGELOG.md` (+`[1.7.0]` entry), `CROSS_APP_SURFACE.md` (§3a + header note).

---

## 3. Test / lint / build status

- **Python tests:** **764 pass, 0 fail** via the fleet venv (`/home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q`). Baseline at session start was also 764 pass — the fold-in adds no Python logic tests and perturbs nothing. No SPA unit tests exist (none in the standalone; out of scope).
- **Lint:** `ruff check` + `ruff format --check` **clean** on the changed Python.
- **Frontend build:** `npm ci && npm run build` **success** — `tsc --noEmit` clean, 48 modules, emits `public/accounts/` + `www/accounts.html` under base `/assets/erpocr_integration/accounts/`, no source map.
- **Read-only invariant:** grep-confirmed — data access is only `useFrappeGetDocCount`/`useFrappeGetDocList` (over `frappe.client` get_count/get_list); `useFrappeAuth` is auth-only. **No write path, no new whitelisted method.**

---

## 4. Decisions made during implementation

- **Rebase-vs-refold → REFOLD FRESH (surfaced, my call).** The stale branch was **0-ahead / 52-behind** master (its history a strict ancestor; all value in uncommitted WIP). I reset it to master HEAD and re-applied the fold-in from the canonical SPA source. **This was the correct call and the completeness gate proved it:** the stale WIP's `hooks.py` carried *52-commit-old* master content — `after_install` commented out, **no `after_migrate`**, **no `OCR Fleet Driver`** role fixture, and it still had the **known-bad `Custom Field` fixture line** master deliberately removed. A naive rebase/carry would have **regressed all of these**. Stale WIP snapshotted to `backup/foldin-wip-pre-refold` first (you approved the discard).
- **Packaging → COMMIT the built dist (Option A), NOT build-at-deploy.** I surfaced the choice; you confirmed **commit-the-dist**. This **matches the architect's already-ratified Option A** (per memory `project_architect_register_bootstrap` Q5) and how `fleet_management` ships its dashboard to the same Starktail host. **Consequence: this supersedes ADR-0010's "Starktail needs a Node build step" flag** — that flag was the *stale* framing inherited from the standalone-app era. The app now installs + serves the SPA with **zero Node at deploy**. `sourcemap:false` avoids committing a 1.76 MB `.map` per release.
- **Serving model → preserved the standalone's website-route (`/accounts` www page), NOT switched to a Desk page.** The `frappe-react-spa` skill's default is a Desk page, but the standalone (UAT-passed) uses the also-verified www-route fork. Scope is frozen — I preserved it rather than redesign.
- **Placeholder logo added.** The tile referenced `ocr-logo.svg` which never existed (same gap in the standalone). Added a small self-contained SVG so the tile renders. Swap for a real brand asset anytime.
- **Review fix (`e5c0dbe`) — removed inert `has_app_permission` hook dict + corrected overstated comments.** See §7.

---

## 5. Open questions for the architecture chat

1. **ADR-0011 — formalize commit-the-dist.** Memory Q5 says "formalize as ADR-0011 at merge review." The dist is committed; **ADR-0010's Node-step consequence is now reversed**. Please write ADR-0011 and update ADR-0010's consequence line at merge. **Caveat you flagged: wire the SPA rebuild into the release step so the committed dist never drifts from `frontend/src`** — recommend a pre-tag gate that fails if `npm run build` produces a diff (candidate for `frappe-pre-deploy-gates`).
2. **CROSS_APP_SURFACE baseline SHA.** I added §3a and a header note but left the Baselined-at SHA at `ba21ec1` (a merged commit). **Re-baseline it at the merge commit** (noted inline in the doc). No whitelisted surface was added, so §2's count stays 32.
3. **Route gating posture (informational, not a blocker).** The `/accounts` www page is a **public shell**; access rests entirely on per-API read perms (correct + sufficient for read-only). The tile is gated; the route is not. If you ever want server-side route gating, that's a `www/accounts.py` `get_context` — a deliberate design change, not something I did. Flagging so it's a conscious choice.
4. **Retire the standalone `~/dev/starpops_accounts`.** Post-merge, uninstall the standalone app from any site that has it and archive the repo. The canonical source still lives there (unchanged, still carries the 2 Codex fixes uncommitted) — I sourced from it but didn't modify it.

---

## 6. Memory delta (durable code-side facts)

- The accounts dashboard SPA source lives at **`frontend/`** (repo root); build emits to **`erpocr_integration/public/accounts/`** + **`erpocr_integration/www/accounts.html`**, both **committed** (base `/assets/erpocr_integration/accounts/`, `sourcemap:false`).
- Rebuild command: **`cd frontend && npm ci && npm run build`** (Node 20+/npm; stack is React 19 + Vite 6 + Tailwind 4 + frappe-react-sdk — floats ahead of the `frappe-react-spa` skill's pinned Mint stack; that's the standalone's pre-existing choice).
- SPA route: **`/accounts`** (website route, NOT a Desk page). Tile gate: **`erpocr_integration.dashboard.permission.has_app_permission`**, wired only via `add_to_apps_screen[].has_permission` (Frappe has **no** top-level `has_app_permission` hook).
- The bench serves erpocr on **driver-dev.local (frontend :8094)**, NOT starpops-dev.local — that's where erpocr is installed on `starpops-test`. (Memory `project_starpops_accounts_mvp` + MEMORY.md updated this session.)
- Managed-host asset-serving footgun confirmed live: `sites/assets/erpocr_integration` is a symlink into the app's `public/`; the per-site frontend containers don't mount `apps/`, so serving `/accounts` assets needs the dist as **real files** in the shared assets volume (`bench build` recreates the symlink; committed dist + real-files copy is the pattern).

---

## 7. Known issues / risks

- **Review finding (fixed in `e5c0dbe`, LOW).** First-pass in-session review (verified against live Frappe 15.95) found the top-level `has_app_permission = {...}` hook dict was **dead config** — Frappe reads only the tile's `has_permission` (`frappe/apps.py::get_apps`; cf. sibling `driver_ui_shell`). My comments/docstring also **overstated** it as "gating the SPA route." Removed the dict; corrected the comment/docstring/CHANGELOG/surface-doc to state the truth (route public, data API-gated). No behaviour change; docs now truthful.
- **`starpops-test` stack OOM-crashed mid-session, independent of my work** (`Exited 137` across all containers; the `starpops-v16` stack was already flapping — host memory pressure on WSL, 15 GiB). My only host change at the time was a git detach, which can't stop containers. I reverted it, then **brought the stack back up** (your instruction, to run the live verify) and it's **left running**. If you want it down, `docker compose -p starpops-test ... down`. Watch host memory if running multiple stacks + Chrome.
- **Deploy note (not a bug):** the `/accounts/<subpath>` catch-all route rule needs a **`clear-cache` after the code loads** (a fresh www page + hook isn't picked up until then). `bench migrate` does a clear-cache, so a normal deploy covers it — flagging because I hit a false 404 until I cleared cache post-restart.
- **No SPA unit tests.** The standalone shipped none; the SPA is UAT-covered. Not introduced by this session, but worth noting for future iteration.

---

## 8. How to test/verify locally

Serving wiring was **verified live this session** on `starpops-test` (driver-dev.local, :8094): `/accounts` → 200 (SPA shell), all `/assets/erpocr_integration/accounts/` JS+CSS+logo → 200, React boots → Login screen, deep-link `/accounts/q/...` resolves under `basename="/accounts"` (200, react-router handles it), no SPA console errors. Bench restored to master/v1.6.0 afterward. To re-run:

```
# 1. point the bench's erpocr at the fold-in + serve its assets as real files
cd /home/willie/dev/erpocr_integration && git checkout 92802f7      # (or the merge commit)
docker exec starpops-test-backend-1 bash -lc '
  cd /home/frappe/frappe-bench
  rm -f sites/assets/erpocr_integration
  cp -r apps/erpocr_integration/erpocr_integration/public sites/assets/erpocr_integration'
docker restart starpops-test-backend-1        # loads the www page + hooks
# wait for ping, then:
docker exec starpops-test-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site driver-dev.local clear-cache'

# 2. verify (guest — no creds needed)
curl -s -o /dev/null -w "%{http_code}\n" -H 'Host: driver-dev.local' http://localhost:8094/accounts                                   # -> 200
curl -s -o /dev/null -w "%{http_code}\n" -H 'Host: driver-dev.local' http://localhost:8094/assets/erpocr_integration/accounts/assets/index-B-pKkQfU.js   # -> 200
# browser: http://localhost:8094/accounts  -> SPA mounts, Login screen renders

# 3. RESTORE
cd /home/willie/dev/erpocr_integration && git checkout master
docker exec starpops-test-backend-1 bash -lc '
  cd /home/frappe/frappe-bench && rm -rf sites/assets/erpocr_integration &&
  ln -sfn /home/frappe/frappe-bench/apps/erpocr_integration/erpocr_integration/public sites/assets/erpocr_integration'
docker restart starpops-test-backend-1
```

Not re-driven (deliberately — unchanged, UAT-covered): the **authenticated** Overview→drill-down with real data. To exercise it, log into `/accounts` as a user with OCR read perm and confirm the 12 count cells render and a drill-down opens.

**Python:** `cd /home/willie/dev/erpocr-foldin && /home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q` → 764 pass.

---

## 9. Workflow notes

- **Two-pass review split:** I ran the **first pass** (in-session general-purpose finder, verified against live Frappe) → 1 LOW finding, fixed. Per the kickoff, the **Codex second pass is the architect's job** — I did not run it. The diff is small and mostly a code move; the genuinely-new hand-written surface is ~45 lines (`permission.py` + the hooks block).
- **Kickoff accuracy:** two minor drifts, both benign — (a) the kickoff located the perm gate at `api/permission.py`; the WIP had already chosen `dashboard/permission.py` (I kept that). (b) The kickoff's "Starktail Node-step CRITICAL FLAG" was already superseded by the architect's ratified Option A in memory — my surfacing + Willie's confirmation re-landed on it. Worth syncing the kickoff template's standing "Starktail needs a Node step" assumption to the committed-dist reality for future SPA fold-ins.
- **The completeness gate you asked for earned its keep** — it's what proved the stale WIP would have regressed master's `after_migrate`/role-fixture, turning "which branch strategy?" from a judgment call into an evidenced one.

---

**End of handback.**
