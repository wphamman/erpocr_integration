# Handback from Claude Code — `upload_fleet_slip` accepts plain `Driver` (D0 / driver-shell GAP 2) — 2026-07-07

> **For the operator (Willie):** Paste this whole doc into the .ai architecture chat. Frame as
> "Handback from Code session — D0 driver-perm widening."

---

## 1. Branch and commits

- **Branch:** `fix/fleet-slip-driver-perm`
- **Base:** `master` at `52ae278` (clean tree at kickoff, matched Section 2 of the kickoff exactly)
- **Commits made this session:**
  - `6b51afd` fix(fleet_api): upload_fleet_slip accepts plain Driver role (D0, GAP 2)
  - `503cef0` docs(changelog): Driver role is portfolio-wide, not fleet_management-owned (review nit)
  - `9393be9` docs(handback): this doc
  - `7f829aa` fix(fleet_api): owner-scope the idempotent replay (review finding; Willie-requested in-session)
- **Push status:** pushed to `origin/fix/fleet-slip-driver-perm` (remote confirmed via `git ls-remote`). No PR opened, no merge — per kickoff §5.
- **Working tree:** clean.

Session complete — all acceptance criteria met, including live HTTP verification.

## 2. Files changed

`git diff --stat master..HEAD` (before this handback): 8 files, +88 / −14.

- `erpocr_integration/fleet_api.py` — [+17 / −8] — the gate: `has_permission("OCR Fleet Slip", "create") OR "Driver" in frappe.get_roles()`; comment block + docstring updated (now at `fleet_api.py:501`).
- `erpocr_integration/tests/test_fleet_upload_contract.py` — [+44 / −0] — 3 posture tests: Driver-only upload succeeds; Driver-only idempotent replay returns `duplicate: true`; neither-Driver-nor-OCR-role still rejected.
- `erpocr_integration/tests/conftest.py` — [+5 / −0] — `frappe.get_roles` mocked as a **real list** default `["All"]` (build + per-test reset) so `in` does genuine containment and roles can't leak between tests.
- `CROSS_APP_SURFACE.md` — [+19 / −6] — §2a table row, §2c permission-posture bullet (v1.6.0/D0), §5 `OCR Fleet Driver` demoted to belt-and-braces.
- `CHANGELOG.md` — [+6 / −1] — `[1.6.0] — 2026-07-07` entry.
- `erpocr_integration/__init__.py` — `1.5.1` → `1.6.0`.
- `CLAUDE.md` — [+8 / −1] — version line; new gotcha (perm posture is endpoint-scoped — don't "fix" driver 403s with role grants); the 3 standing data/credential hygiene rules propagated per kickoff §4.
- `docs/architecture.md` — [+1 / −1] — fleet ingest bullet: shell upload open to plain `Driver` (D0).

## 3. Test / lint / build status

- **Test suite:** **764 pass, 0 fail** (`pytest erpocr_integration/tests/`, CI-mirror venv: pytest + requests + google-api-python-client + google-auth + Pillow). New tests: 4 (3 posture + 1 cross-user replay rejection). New failures: 0.
- **Baseline at start of session:** 760 pass, 0 fail — nothing pre-existing red.
- **Lint:** `ruff check` + `ruff format --check` clean on all touched Python files.
- **Build:** n/a (no frontend/bundle changes).
- **Live HTTP verification (AC #4): PASS.** Backend container restarted first (standing gotcha). `relief-smoke@driver-dev.local` — confirmed on-bench to hold **exactly `['Driver']`**, no OCR role, and **no role was granted to anyone** — POSTed a synthetic JPEG slip through `:8094`:
  - Fresh: `{"ocr_fleet_slip": "OCR-FS-00040", "status": "Pending", "duplicate": false}`, HTTP 200 (previously this exact call 403'd).
  - Replay (same `client_request_id`): same slip, `"duplicate": true`, HTTP 200.
  - Slip record confirmed: `source_type = "Gemini Shell Upload"`, `uploaded_by = relief-smoke@driver-dev.local`.
  - The slip later moved to `status: Error` — **pre-existing bench condition, not this change**: driver-dev.local was restored from prod without the original encryption key, so `OCR Settings.gemini_api_key` can't be decrypted and the *async extraction* fails post-insert. The upload contract itself is unaffected.

## 4. Decisions made during implementation

- **Route chosen: endpoint-gate (kickoff route 2), not doctype-perm.** Three reasons:
  1. **It's the portfolio posture the kickoff said to match** — `fleet_management.api.submit_vehicle_inspection` gates possession-based driver writes with an in-endpoint role check (`_INSPECTION_SUBMIT_ROLES = ("Driver", "Fleet Manager", "System Manager")`) while doctype perms stay tight.
  2. **The known prod Custom-DocPerm shadow on OCR Fleet Slip** would render a doctype-JSON perm row inert on prod until the still-owed "Restore Original Permissions" step runs. The endpoint gate is immune to DocPerm state — decisive, since this change must be live on prod *before* driver-shell deploy #1.
  3. No migrate needed; no accidental Desk-side create for every Driver System User.
- **The widening is additive** (`create-perm OR Driver`), so `OCR Fleet Driver` / `OCR Manager` / `System Manager` keep working via the doctype perm — the runbook's role-grant line becomes belt-and-braces, exactly as the kickoff scoped.
- **CHANGELOG wording corrected post-review** (`503cef0`): the `Driver` Role record is portfolio-wide (also referenced by `starpops_assets` `bin_inspection.json`), not owned by `fleet_management`. Any future holder of the Driver persona role gains slip upload — that is D0's intent, but the chat should know the blast radius is the persona role, not one app.
- `frappe.get_roles` verified on the real bench before use (CLAUDE.md mock-blind-spot rule); conftest mocks it as a real list so the `in` check is genuinely exercised.

## 5. Open questions for the architecture chat

- **~~Replay owner check~~ — RESOLVED in-session** (Willie asked for the fix): commit `7f829aa` owner-scopes the replay — only the slip's owner receives the duplicate envelope; any other authenticated caller presenting the key gets a PermissionError. Cross-user rejection unit-tested; live same-user replay re-verified green through the new check.
- **`fleet_management` parity follow-up (fleet repo, not here):** `submit_vehicle_inspection`'s replay path has the SAME missing owner check (verified in `fleet_management/api.py:2318-2324`). Decide whether to port the owner-scoped replay there.

## 6. Memory delta (durable code-side facts)

- erpocr_integration is at **v1.6.0** on `fix/fleet-slip-driver-perm` (not yet merged/tagged): `upload_fleet_slip` accepts the plain `Driver` role via an **endpoint-scoped** check at `fleet_api.py:501` — driver-shell GAP 2 is closed at root; NO site-level role provisioning needed for drivers.
- The `OCR Fleet Driver` role is demoted to belt-and-braces (still functional; also the way to give a driver `if_owner` Desk read of own slips).
- The endpoint gate is deliberately immune to the prod Custom-DocPerm shadow on OCR Fleet Slip (that restore step is still owed for *read* posture, but no longer blocks driver uploads).
- Posture tests live in `erpocr_integration/tests/test_fleet_upload_contract.py` (`TestUploadPermissionGuards`); conftest now ships a real-list `frappe.get_roles` mock (default `["All"]`).
- driver-dev.local bench condition: `OCR Settings.gemini_api_key` is undecryptable (encryption-key mismatch from the prod restore), so ALL Gemini extractions on that site fail post-insert with "Failed to decrypt key". Upload/idempotency smokes work; extraction smokes can't, until the key is re-entered on the site.
- CLAUDE.md now carries the 3 standing data/credential hygiene rules (kickoff §4 propagation) and a gotcha forbidding role-grant "fixes" for driver 403s.

## 7. Known issues / risks

- **Driver-role blast radius (accepted by D0):** any user holding the portfolio `Driver` persona role — current holders: 16 genuine Driver users on driver-dev restored from prod, incl. `wphamman@` — can create slips + ≤2MB private Files via this endpoint, with no rate limit, same as they already could via `submit_vehicle_inspection`. Not a new exposure class; noting for completeness.
- **Review process flag:** the 3 CLAUDE.md hygiene rules rode along in the main commit (`6b51afd`) rather than a separate docs commit. Content was kickoff-mandated; bundling was a tidiness miss, left as-is to avoid rewriting pushed history.
- **The Codex second-pass gate did NOT run** — Codex CLI is not installed on WPHSERVER. A ready-to-paste review prompt is embedded in §9; the in-session adversarial first pass (subagent, mutation-tested the gate both directions, bench-verified Frappe semantics) returned all-PASS.

## 8. How to test/verify locally

```bash
cd /home/willie/dev/erpocr_integration && git checkout fix/fleet-slip-driver-perm

# Unit suite (CI-mirror venv)
python3 -m venv /tmp/erpocr-venv && /tmp/erpocr-venv/bin/pip install -q pytest requests google-api-python-client google-auth Pillow
/tmp/erpocr-venv/bin/python -m pytest erpocr_integration/tests/ -q          # expect: 763 passed

# Live contract smoke (bench serves this checkout via bind mount)
docker restart starpops-test-backend-1 && sleep 8                            # stale-gunicorn gotcha
curl -s -c /tmp/ck.txt -X POST http://localhost:8094/api/method/login \
  -d 'usr=relief-smoke@driver-dev.local' --data-urlencode 'pwd=<see driver_ui_shell handback-relief-driver-2026-06-25.md §6>'
curl -s -b /tmp/ck.txt -X POST \
  http://localhost:8094/api/method/erpocr_integration.fleet_api.upload_fleet_slip \
  -F "client_request_id=$(cat /proc/sys/kernel/random/uuid)" \
  -F 'vehicle_registration=SMOKE 1 GP' -F 'file=@<any small .jpg>'
# expect: {"message": {"ocr_fleet_slip": "OCR-FS-…", "status": "Pending", "duplicate": false}}
# repeat with the SAME client_request_id → same slip, "duplicate": true
```

Expected outcome: a Driver-only user gets the success envelope (no 403); replay is idempotent; the slip carries `source_type = "Gemini Shell Upload"` and `uploaded_by` = the caller.

## 9. Workflow notes (for the operator)

- **Cross-app surface delta (kickoff §6.9):** `CROSS_APP_SURFACE.md` updated in the same commit as the code (it carries no Baselined-at SHA — only an "authored against v1.2.0" note, left as-is with the §2c bullet version-stamped v1.6.0). **driver_ui_shell follow-up owed (shell session, not here):** its GAP 2 doc entry → closed-at-root, and its deploy checklist's `OCR Fleet Driver` grant line → demote to belt-and-braces.
- **Prod sequencing reminder:** this must be merged, tagged, and live on prod **before driver-shell deploy #1** (runbook step, out of this session's scope).
- Kickoff was accurate to the line number — zero discrepancies; no gating questions needed.
- **Optional external second pass** (Codex CLI not on this machine) — paste into Codex in the repo root on branch `fix/fleet-slip-driver-perm`:

```text
You are reviewing a security-sensitive permission change in a Frappe v15 custom app (this repo, branch fix/fleet-slip-driver-perm, code commits 6b51afd + 7f829aa, diff range master..HEAD — ignore the docs/handbacks commits).

Change under review: the whitelisted endpoint erpocr_integration/fleet_api.py::upload_fleet_slip previously required frappe.has_permission("OCR Fleet Slip", "create"); it now passes on that OR "Driver" in frappe.get_roles() (gate at fleet_api.py:501). The subsequent insert runs ignore_permissions=True, so this check is the entire gate. Intent (architecture decision D0): possession-based driver writes accept the plain Driver role, matching fleet_management.api.submit_vehicle_inspection; doctype perms deliberately unchanged.

Second change under review (7f829aa): the idempotent-replay path is now owner-scoped — after refetching the existing slip on a unique-key collision, `existing.owner != frappe.session.user` throws PermissionError instead of returning the duplicate envelope (slip name/status). Rationale: a legitimate replay is always same-user (the shell generates the UUID per capture on one device).

Invariants that must NOT have moved: idempotent client_request_id replay for the SAME user (insert-and-catch + full rollback), captured_at tz normalization, recon-only (the endpoint must remain structurally incapable of creating/feeding a Purchase Invoice or OCR Import), vehicle_registration relief fallback, explicit Guest denial.

Please review adversarially:
1. Does the widened gate open ANY surface beyond slip upload for a plain-Driver session (other endpoints, field injection through the insert, information disclosure)?
2. Boolean/ordering correctness of the gate (guest check first; or-precedence; nothing bypasses the file/idempotency validation).
3. The three new tests in erpocr_integration/tests/test_fleet_upload_contract.py (TestUploadPermissionGuards) + the conftest.py get_roles mock — do they genuinely exercise the new branch, and could a mock artifact hide a real-Frappe failure?
4. Docs accuracy: CHANGELOG [1.6.0], CROSS_APP_SURFACE.md §2a/§2c/§5, CLAUDE.md, docs/architecture.md vs the actual code.
Return numbered PASS/FAIL/CAUTION/NIT verdicts with one-line justifications, then any additional findings. Do not fix anything — report only.
```

---

**End of handback.**
