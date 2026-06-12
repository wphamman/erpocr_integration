# Handback from Claude Code — P4 Fleet-Slip Upload Contract (erpocr side) — 2026-06-11

> Paste into the .ai/architecture chat. Frame as "Handback from Code session — P4 upload contract."
> **Status: code COMPLETE + PUSHED; P-1 INSTALLED + LIVE-VERIFIED on driver-dev (data-safe).**
> The contract is proven end-to-end live (idempotency, source_type, owner-stamp, captured_at,
> private attachment, fail-safe, recon-only, async pipeline) AND the driver role is verified live
> (create + if_owner read scoping). **One required prod-deploy step surfaced + cleared on the mirror:
> Restore Original Permissions on the OCR Fleet Slip Custom DocPerm shadow** — see §14d–14f.
> **Codex review run: 8 PASS / 2 FAIL; both FAILs fixed in `0aeb256` (§15) — re-review warranted.**

---

## 1. Branch and commits

- **Branch:** `feature/p4-slip-upload-contract` (off master `5d52627`, the v1.3.0 release).
- **Commits this session (erpocr repo):**
  - `e0245d3` feat(driver): idempotent upload_fleet_slip contract (shell P4)
  - `2fdaebb` test(driver): upload_fleet_slip contract — idempotency, role, fail-safe, coexistence
  - `298d44f` docs(cross-app): commit upload_fleet_slip contract + source_type vocabulary
- **NOT pushed, NOT merged.** Codex review gates it (kickoff). Working tree clean otherwise.

## 2. Files changed (`git diff --stat 5d52627..HEAD` — 8 files, +~470)

- `erpocr_integration/fleet_api.py` — `upload_fleet_slip` (POST) + `_shape_upload_response`,
  source-type constants (`SOURCE_TYPE_DRIVE/SHELL`), `MAX_FLEET_UPLOAD_SIZE`, and `fail_safe`
  threaded through `_apply_vehicle_config`/`_match_vehicle`/`_run_fleet_matching` +
  `fleet_gemini_process` (Confirmed-skip + shell fail-safe).
- `erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.json` — new fields
  `client_request_id` (Data, unique, no_copy, hidden, read_only, print_hide) + `captured_at`
  (Datetime, read_only); new `OCR Fleet Driver` permission row (create + if_owner read only).
- `erpocr_integration/fixtures/role.json` — `OCR Fleet Driver` role (desk_access 0).
- `erpocr_integration/hooks.py` — role added to the Role fixtures filter.
- `erpocr_integration/tests/test_fleet_upload_contract.py` — 33 tests (NEW).
- `erpocr_integration/tests/test_fleet_driver_role.py` — 6 tests (NEW).
- `erpocr_integration/tests/conftest.py` — real `UniqueValidationError`/`DuplicateEntryError`/
  `DoesNotExistError`/`PermissionError` classes, `get_datetime`, `db.rollback`,
  `clear_messages`, and an `enqueue.side_effect` reset.
- `CROSS_APP_SURFACE.md` — §2c contract, §5 role, §2a row + count, §6 stability,
  source_type vocabulary, photo-contract forward notes.

## 3. Test / lint status

- **Tests:** **716 pass, 0 fail** (`pytest erpocr_integration/tests/`, frappe fully mocked).
  Baseline 677 → +39 (33 upload-contract + 6 driver-role). Run via the fleet venv
  (`/home/willie/dev/fleet_management/.venv/bin/python -m pytest`) since erpocr has no venv;
  frappe is mocked so any pytest works.
- **Lint:** `ruff check` + `ruff format --check` clean on all touched files (erpocr's own
  pyproject: tab indent, line-length 110, double quotes).
- **Migrate / live:** NOT run yet (P-1 held). The `client_request_id` UNIQUE index is created by
  `bench migrate` once installed — verification step is in §8.

## 4. T1 verdict — PASS (recon is source-agnostic)

`fleet_management` consumes OCR Fleet Slips **by record, never by provenance**:
- `monthly_summary.py:254` filters on `fleet_vehicle` (canonical Link) + `transaction_date` +
  `status ∈ {Completed, Draft Created, Matched, Needs Review}`; reads only
  `slip_type, total_amount, litres`.
- Fuel Efficiency Tracker only checks `frappe.db.exists("DocType","OCR Fleet Slip")`.
- **Zero** references to `source_type`, `drive_file_id`, `drive_link`, or `uploaded_by` anywhere
  in `fleet_management`'s non-test code (grep-verified).
- **Implication for the build:** an API slip is consumed identically to a Drive slip as long as
  it lands with `fleet_vehicle` + `transaction_date` + a consumed status. The driver-supplied
  vehicle makes this *more* reliable than the Drive OCR-guess path (which left 20/39 prod slips
  with blank posting_mode). No gap — no STOP needed.
- **Out-of-scope observation (not fixed):** `fleet_management/scripts/health_audit.py:207`
  queries `OCR Fleet Slip` with a `slip_date` filter, but the field is `transaction_date` — that
  diagnostic script would error. It's a manual script, not the recon path; flagged, left alone.

## 5. Contract as built

```
erpocr_integration.fleet_api.upload_fleet_slip(
    client_request_id,            # REQUIRED idempotency key (client UUID, reused on retry)
    fleet_vehicle=None,           # optional Fleet Vehicle name (driver pick) → Confirmed
    vehicle_registration=None,    # optional fallback plate string → async match
    captured_at=None,             # optional ISO device timestamp (stored distinctly)
)  @frappe.whitelist(methods=["POST"])   # multipart/form-data, binary `file` field

Returns (same shape fresh + replay):
  {"ocr_fleet_slip": "<OCR-FS-…>", "status": "<str>", "client_request_id": "<uuid>", "duplicate": bool}
```

- Permission: Guest denied; `has_permission("OCR Fleet Slip","create")`; **never** OCR Import.
- File: multipart binary, **≤2MB** (own boundary), magic-byte validated, private `File`
  (office-visible via OCR Manager read; not publicly exposed).
- Idempotency: R-B verbatim — nullable-unique `client_request_id`, insert-and-catch +
  **full** `frappe.db.rollback()`, duplicate returns existing + `duplicate:true`, no 2nd enqueue.
- Fail-safe fork: provider-less vehicle → blank `posting_mode`/supplier → Needs Review; PI guard
  (`posting_mode != "Direct Expense"`) blocks invoice. NEVER silently toward the invoice path.
- Confirmed (driver-picked) vehicle is not re-matched by async OCR.
- `source_type = "Gemini Shell Upload"` constant (never client input). Async extraction enqueued
  on `long`; returns immediately.

## 6. OCR Fleet Driver role

- `fixtures/role.json`: `OCR Fleet Driver`, `desk_access: 0`, `disabled: 0` (drivers use the
  shell SPA + the whitelisted API, never the Desk).
- OCR Fleet Slip perm row: `create:1, read:1, if_owner:1` — everything else 0
  (no write/delete/submit/export/email/share, permlevel 0).
- `hooks.py` fixtures filter includes it (ships on install).
- **Deployment note (Willie's call):** assign drivers `OCR Fleet Driver` IN ADDITION to fleet's
  `Driver` role. The existing `OCR Fleet Slip Reader` grants broad read+write on *all* slips — if
  a driver must be strictly own-slips-only, give `OCR Fleet Driver` and NOT Reader.

## 7. Decisions made during implementation (deviations from kickoff text, flagged)

1. **`source_type`: REUSED the existing Data field** (set to `"Gemini Shell Upload"`) instead of
   adding the kickoff's literal "new Select(Drive/API) field". The SHELL_INTEGRATION_REPORT
   §3/§5 found the field already exists and ruled to reuse it; T1 confirmed no consumer reads it;
   reuse avoids migrating existing "Gemini Drive Scan" rows and a dual source-of-truth.
   **Confirmed by Willie** (answer A). Vocabulary documented in CROSS_APP_SURFACE.md §2c/§6.
2. **Fail-safe = blank posting_mode + blank supplier** (not a new `needs_review` checkbox). Uses
   the existing status mechanism: no supplier → `_update_status()` lands the slip in Needs
   Review, and the PI guard blocks the invoice path. The kickoff allowed "a needs_review flag or
   equivalent existing mechanism" — this is the equivalent existing mechanism, no schema bloat.
3. **Fail-safe applies to ALL shell-sourced slips**, not just driver-picked ones: the async
   matcher also fail-safes when `source_type` starts with "Gemini Shell" (so a registration-only
   slip that OCR-matches a provider-less vehicle still can't reach Direct Expense). Drive path
   keeps the original Direct-Expense fallback (`fail_safe=False`).
4. **2MB server cap** per the kickoff (tighter than the report's "reuse 10MB checks") — the
   contract enforces its own boundary.
5. **No per-user pending cap.** upload_pdf has one (20); I omitted it here so an offline-queue
   drain of many slips at reconnect isn't throttled. Idempotency + role scoping are the
   protections. The client contract is "one `client_request_id` per capture, reused on retry" —
   a client that regenerates the key per attempt could flood; documented, not server-capped.
   Forward note: rate-limiting is a shared-photo-contract concern.
6. **`desk_access: 0`** on the driver role (tighter than Reader's 1) — drivers never load Desk.

No deviations from the kickoff's *intent* — these are mechanism choices within the rulings.

## 8. P-1 live install — HELD (runbook + the scheduler crash-loop)

**Held** at Willie's instruction (the extraction session owned the bench window). Riders: diagnose
the crash-looping scheduler first (done, below); coordinate a quiet recreate window.

### 8a. Scheduler crash-loop — DIAGNOSED (pre-existing, unrelated to P4)
- `starpops-test-scheduler-1`: `ModuleNotFoundError: No module named 'starpops_assets'`,
  RestartCount 27, looping since **2026-06-11T11:59:04Z** (this morning's last recreate). So
  **scheduled jobs have not run on the prod-mirror since ~12:00 today.**
- Root cause: the scheduler runs `setup_module_map(include_all_apps=True)` (no request/job
  context) → eager-imports every app in apps.txt incl. `starpops_assets`, which is **not** in the
  override's bind-mount + self-heal guard (list is production/maintenance/fleet/driver/hrms). The
  recreate wiped its writable-layer install. The backend survives only because gunicorn imports
  apps lazily per-request.
- **This also blocks P-1:** a recreated backend would lose `starpops_assets` too, and
  `bench install-app`/`migrate` eager-import → would fail. So fixing it is a prerequisite, folded
  into the same override edit (rider: "fix it as part of the recreate").

### 8b. Proposed `compose.override.yaml` change (REVIEWED with Willie; not yet applied)
At `/home/willie/dev/erpnext-docker/starpops-test/frappe_docker/compose.override.yaml` — add
`starpops_assets` AND `erpocr_integration` to the bind-mount + self-heal guard of the 4 python
services (backend, scheduler, queue-short, queue-long): extend the backend `for` loop and add two
`if`-guards each to scheduler/queue-short/queue-long, plus two volume lines per service. (Exact
unified diff was shown in-session; it is mechanical and matches the established pattern.)

### 8c. Install runbook (run when Willie gives the word + the window is quiet)
```bash
cd /home/willie/dev/erpnext-docker/starpops-test/frappe_docker
# 1. apply the override edit (8b), then recreate the 4 python services:
sg docker -c "docker compose -p starpops-test up -d backend scheduler queue-short queue-long"
# 2. confirm scheduler no longer restarts + apps import:
sg docker -c "docker compose -p starpops-test ps"
sg docker -c "docker exec starpops-test-backend-1 /home/frappe/frappe-bench/env/bin/python -c 'import starpops_assets, erpocr_integration; print(\"ok\")'"
# 3. install on the site (watch for conflicts with mirror prod data — STOP + surface if any):
sg docker -c "docker exec starpops-test-backend-1 bench --site driver-dev.local install-app erpocr_integration"
# 4. migrate (creates client_request_id UNIQUE index + captured_at):
sg docker -c "docker exec starpops-test-backend-1 bench --site driver-dev.local migrate"
# 5. VERIFY the nullable-unique index:
sg docker -c "docker exec starpops-test-backend-1 bench --site driver-dev.local mariadb -e \"SHOW INDEX FROM \\\`tabOCR Fleet Slip\\\` WHERE Column_name='client_request_id'\""
#    expect Non_unique=0, Null=YES, Default=NULL
# 6. build Desk assets (doctype_js): bench build --app erpocr_integration
# 7. real-image async test (synthetic): create a user with OCR Fleet Driver role, call
#    upload_fleet_slip with a real JPEG → assert one slip Pending → extraction → Matched/Needs
#    Review; retry same client_request_id → duplicate:true, one row. (P-2: mirror data may lag
#    prod — custom_fleet_card_provider set after the last restore; use synthetic provider
#    vehicles, or run scripts/restore-prod-to-dev.sh first.)
```
Acceptance items still open until this runs: "app installed and healthy on driver-dev",
"extraction completes async on a real test image", "migrate creates the unique index".

## 9. Codex review prompt (run before merge — focus per kickoff)

```
Review the branch feature/p4-slip-upload-contract in this repo (erpocr_integration, a Frappe v15
custom app). Base is master 5d52627 (v1.3.0); the relevant commits are e0245d3, 2fdaebb, 298d44f.

This adds erpocr_integration.fleet_api.upload_fleet_slip — a whitelisted POST that lands a
phone-captured fleet slip as an OCR Fleet Slip recon record (image attached, Gemini extraction
queued async, NO Purchase Invoice — the v1.2.0 fleet invariant). Read fleet_api.py (the
upload_fleet_slip block + _shape_upload_response + the fail_safe changes in _apply_vehicle_config /
_match_vehicle / _run_fleet_matching / fleet_gemini_process), the new client_request_id +
captured_at fields and the OCR Fleet Driver permission row in
erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.json, fixtures/role.json,
hooks.py (Role fixtures filter), and CROSS_APP_SURFACE.md §2c/§5/§6. The OCR Fleet Slip controller
is erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py. The site runs MariaDB
at REPEATABLE READ.

Intentional design (do NOT flag as bugs): the contract gates on OCR Fleet Slip create only (never
OCR Import); inserts with ignore_permissions=True after the role gate (the method is the perm
boundary); owner/uploaded_by are server-stamped from the session user; idempotency via a
nullable-unique client_request_id with insert-and-catch (not check-then-insert) + a FULL
frappe.db.rollback() on collision; source_type is a server-set constant; the fail-safe leaves
posting_mode/supplier BLANK (Needs Review) for a provider-less vehicle rather than Direct Expense.

Focus on CORRECTNESS, especially:
1. The idempotency race. Duplicate path does FULL rollback then re-fetches by client_request_id.
   Race-safe across SEPARATE HTTP requests under REPEATABLE READ? Any window for two slips on one
   key, or a wrong/failed re-fetch? Is guarding ONLY insert() correct (could any other unique key
   fire there)? Is the commit-after-insert sequencing right vs the rollback path?
2. The fail-safe provider fork. Can ANY path (driver-picked vehicle, async OCR match of a
   registration-only slip, retry, Desk edit) route a shell/API slip toward Direct Expense / a
   Purchase Invoice when the vehicle has no custom_fleet_card_provider? Confirm the Confirmed-skip
   in fleet_gemini_process can't be bypassed so OCR re-matching clobbers a driver's pick. Does the
   Drive path remain unchanged (fail_safe=False → Direct-Expense fallback intact)?
3. Role scoping. OCR Fleet Driver = create + if_owner read only. Can a driver read other drivers'
   slips, write/delete any slip, or reach OCR Import / the PI surface? Is if_owner sufficient given
   the contract inserts with ignore_permissions (owner == session user)? Guest denial order.
4. File handling: 2MB cap + magic-byte validation order, private-File office visibility (no public
   URL leak), and that a malformed captured_at can't block the upload.
5. source_type coexistence: an API slip indistinguishable from a Drive slip to fleet_management
   except source_type; no validation added that could reject a Drive/Desk slip (NULL
   client_request_id) — confirm nullable-unique coexistence.

Report a numbered list, each PASS / FAIL / CAUTION / NIT with file:line and a one-line rationale.
Do not edit anything — just review.
```

## 10. Known issues / risks

- **Idempotency race verified by reasoning + mocked tests, not a real two-process collision**
  (pytest is mock-based; the DB unique index is the true enforcement point and only exists after
  migrate). Same caveat as P3. The live two-request test is in §8c step 7.
- **No per-user flood cap** (§7.5) — relies on the per-capture-key client contract.
- **`captured_at` parse is lenient** — a malformed value is logged and dropped, never blocks the
  upload (deliberate; a bad device clock shouldn't lose a slip).
- **Mirror data may lag prod (P-2):** `custom_fleet_card_provider` set on prod after the last
  restore — provider-dependent live tests need synthetic vehicles or a fresh restore.

## 11. Photo / upload contract — forward requirements (named here, designed once separately)

This contract NAMES but does not implement (shared with P3, its own post-P3 sitting):
- Client-side compression to **≤1.5MB** (long edge ~1280–1600px, JPEG q~0.7) before upload.
- **Offline IndexedDB queue** keyed on `client_request_id` (compressed blob + key + captured_at),
  draining with backoff on reconnect — the idempotency key is what makes the drain safe.
- One image in memory at a time (Android 9 / ~2GB RAM).
- Out of P4 scope entirely: PODs (P6), Wesbank-Import changes, the shared photo-contract *design*.

## 12. Memory delta (durable code-side facts)

- Contract `erpocr_integration.fleet_api.upload_fleet_slip(client_request_id, fleet_vehicle=None,
  vehicle_registration=None, captured_at=None)` → `{ocr_fleet_slip, status, client_request_id,
  duplicate}`. Recon-only (no PI), R-B idempotency, fail-safe provider fork, source_type
  "Gemini Shell Upload". The house write-contract template (P3 R-B) now has a 2nd instance.
- Field: `OCR Fleet Slip.client_request_id` (Data, nullable-unique, no_copy, hidden) +
  `captured_at` (Datetime). Role: `OCR Fleet Driver` (create + if_owner read, desk_access 0).
- source_type vocabulary is the cross-app source discriminator (constant, never client input);
  no consumer reads it as of P4 T1.
- Test runner for erpocr: fleet venv python (erpocr has no venv; frappe mocked).

## 13. Open questions for the architecture chat

1. **Driver role assignment** (§6): confirm drivers get `OCR Fleet Driver` and whether to drop
   `OCR Fleet Slip Reader` from driver users (Reader is broad read+write on all slips).
2. **Flood protection** (§7.5): accept the key-per-capture client contract as the only guard, or
   add a per-user pending cap / rate limit in a later pass?
3. **P-1 window**: when is driver-dev quiet enough to recreate (the extraction session had it)?

---

## 14. P-1 EXECUTED on driver-dev.local (2026-06-12) — evidence + the DocPerm-shadow finding

**Outcome: install-app + migrate are data-safe; the contract is proven end-to-end live; one
prod-deploy gotcha (the Custom DocPerm shadow) blocks the OCR Fleet Driver role until reset.**

### 14a. Environment work
- Override edited (`starpops-test/frappe_docker/compose.override.yaml`): added `starpops_assets`
  + `erpocr_integration` to the bind-mount + self-heal of the 4 python services. **Uncommitted in
  that repo** (works on disk; commit if you want it tracked). Also fixed the pre-existing
  `starpops_assets` scheduler crash-loop (it had self-resolved via a 12:06 recreate, but the edit
  makes it durable). NB: the startup self-heal lost a race (4 containers editable-installing the
  same bind-mount at once) — a one-time manual `pip install -e` per container settled it; worth a
  small stagger/lock if this recurs.
- `sites/apps.txt`: appended `erpocr_integration` (install-app requires it). The file had no
  trailing newline → first append corrupted the last line; rewritten cleanly (9 apps, one per line).
- A Docker Desktop engine crash mid-window (host-side) took the stack down between recreate and
  install; restarted by Willie, stack auto-recovered, writable-layer editable installs survived.

### 14b. install-app + migrate — BEFORE/AFTER evidence (the prod-window record)
| | BEFORE | AFTER |
|---|---|---|
| `installed_apps` has erpocr | no | **yes** |
| OCR Import / Fleet Slip / Service Mapping / Statement | 834 / 39 / 452 / 1 | **834 / 39 / 452 / 1 (identical)** |
| `tabOCR Fleet Slip` columns | 48 (no `client_request_id`/`captured_at`) | **50** (+`client_request_id` varchar(140) NULL, +`captured_at` datetime(6) NULL) |
| `client_request_id` index | — | **Non_unique=0, Null=YES** (nullable-unique) |
| 39 legacy rows non-NULL on new cols | — | **0 / 0** |
| `OCR Fleet Driver` role | absent | installed (disabled=0, desk_access=0) |

"Register app over existing restored tables" = additive schema sync, **zero row loss**. This is
exactly the prod deploy-choreography install step; the evidence above is its dry-run record.

### 14c. Contract live verification (as a create-capable user, under the shadow)
`upload_fleet_slip` driven against the real DB + real enqueue with a synthetic JPEG:
- Two calls, same `client_request_id` → **one slip** (`count_for_key=1`), 2nd returns
  `duplicate:true`, same name → **idempotency live on the real DB unique index**.
- `source_type="Gemini Shell Upload"` (constant), `owner`/`uploaded_by` server-stamped,
  `captured_at` stored, **private** File attached, `posting_mode=""` (fail-safe), `purchase_invoice=None`
  (recon-only), returned `Pending` immediately.
- **Async extraction pipeline proven**: the job enqueued → queue-long worker ran `fleet_gemini_process`
  → reached the Gemini call → failed at **`Failed to decrypt key … gemini_api_key — Encryption key
  is invalid`** (a post-restore artifact: the key is encrypted with prod's `encryption_key`, not the
  mirror's) → the error path correctly set status=Error + logged "Fleet Extraction Error". A
  *successful* Gemini extraction is blocked only by the mirror's key mismatch (works on prod, or
  re-enter the key in OCR Settings on the mirror). All test data cleaned up (count back to 39).

### 14d. ⚠️ Prod-deploy finding — the OCR Fleet Slip Custom DocPerm shadow
The driver-role create gate could **not** be verified live: as the synthetic driver
(`OCR Fleet Driver` role assigned), `has_permission("OCR Fleet Slip","create")` returned **False**.
Root cause: `OCR Fleet Slip` has a **Custom DocPerm shadow** (3 rows: Reader/Manager/System Manager,
on prod and the mirror) — and when any Custom DocPerm rows exist, Frappe **ignores the standard JSON
DocPerms entirely**, so the new `OCR Fleet Driver` row (shipped via doctype JSON → `tabDocPerm`) is
invisible. This is the same shadow that blocks `raw_payload` (permlevel-1) reads (known issue).
- **The role design is CORRECT** (verified by reading Frappe `permissions.py`: `create` is
  explicitly excluded from the if_owner downgrade — line `and ptype != "create"` — so a single
  `{create:1, read:1, if_owner:1}` row grants create + owner-scoped read once unshadowed). No code
  change needed.
- **Required prod (and mirror) deploy step:** Customize Form → **Restore Original Permissions** on
  OCR Fleet Slip (deletes the Custom DocPerm shadow so the standard perms — incl. OCR Fleet Driver
  AND the permlevel-1 System Manager row that fixes `raw_payload` — take effect). Programmatic:
  `frappe.permissions.reset_perms("OCR Fleet Slip")` + `clear-cache`.
**Verbatim Custom DocPerm snapshot (OCR Fleet Slip, before restore — the record of what prod's
restore step discards).** All 3 rows dated `2026-05-06`, permlevel 0, if_owner 0:

| role | read | write | create | delete | report | export | print | email | share |
|---|---|---|---|---|---|---|---|---|---|
| OCR Fleet Slip Reader | 1 | 1 | 0 | 0 | 0 | **1** | 0 | 0 | 0 |
| OCR Manager | 1 | 1 | 1 | 0 | 0 | 1 | 0 | 0 | 0 |
| System Manager | 1 | 1 | 1 | 1 | 0 | 1 | 0 | 0 | 0 |

**Assessment: all 3 are STALE, none deliberate** — each is a subset of the shipped JSON captured
before later hardening (missing `report`/`print`/`email`/`share`; missing the System Manager
permlevel-1 row = the `raw_payload` blocker; missing the OCR Fleet Driver row). The only
existing-role perm that *changes* on restore is **Reader `export` 1→0**, which is the intended
hardening (the shipped JSON sets it 0; `test_fleet_slip_reader_role.py` asserts it). **Nothing to
re-apply on prod.**

### 14e. Restore DONE + driver role verified live (2026-06-12, Willie-approved)
`frappe.permissions.reset_perms("OCR Fleet Slip")` + `clear-cache` → Custom DocPerm count 0;
standard JSON now governs (OCR Fleet Driver create+read+if_owner present; System Manager permlevel-1
back → `raw_payload` readable). Driver-role verified **live** as a synthetic `OCR Fleet Driver`:
- `has_permission(create)` = **True**; uploaded a slip via the contract → owner = the driver (stamped).
- `read` own slip = **True**; `read` another user's slip (`OCR-FS-00039`, owner Administrator) =
  **False**; `get_list` as the driver returned **only its own slip (1, not 39)** → if_owner scoping holds.
- Test data cleaned (count back to 39).

### 14f. REQUIRED erpocr deploy-request step (carry into the eventual prod deploy)
The shadow exists on **prod** too. The deploy request MUST include, for `OCR Fleet Slip`:
1. **Snapshot** the existing Custom DocPerm rows (role/perms/if_owner) — record before discarding.
2. **Customize Form → Restore Original Permissions** (or `frappe.permissions.reset_perms` + clear-cache).
3. **Verify** both: a driver can create (contract works) AND System Manager can read `raw_payload`.
4. **Re-apply** any *deliberate* customizations from step 1 (on driver-dev there were none — all stale;
   re-audit prod's set at deploy time in case it diverged).
Context: the `restore-prod-to-dev.sh` three-place strip removes erpocr from `installed_apps` but the
prod dump still carries `tabOCR*` — so post-restore the OCR tables are orphaned; reconcile with
`install-app erpocr_integration` (additive). RUNBOOK updated with this + the encrypted-credential
(Gemini-key) restore quirk.

### 14g. Infra commits
`erpnext-docker/starpops-test` (LOCAL, not pushed): `5322f08` — compose.override.yaml self-heal for
erpocr + starpops_assets (+ the scheduler-loop cause) and RUNBOOK restore quirks. `sites/apps.txt`
(erpocr appended) lives in the docker `sites` volume, not the repo — persists in the volume, nothing
to commit.

---

## 15. Codex review — RUN, both FAILs fixed (`0aeb256`)

Codex reviewed `e18857a` (read-only): **8 PASS, 2 FAIL**. Both FAILs were real and are fixed in
`0aeb256` (pushed); the 8 PASS items confirm the core design (idempotency race, nullable-unique
coexistence, Confirmed-skip, fail_safe threading, role scoping + guest gate, file validation order).

- **FAIL #1 — non-atomic upload (`fleet_api.py`).** The slip committed *before* the File insert +
  enqueue, so a crash/File/enqueue failure after that commit stranded a keyed slip that idempotent
  retries returned un-repaired (no image, no job). **Fix:** slip + private File + extraction job now
  land on a SINGLE commit (the last step), with `enqueue_after_commit=True` tying the job to the
  commit — any pre-commit failure rolls the whole unit back (a retry rebuilds a complete slip), and
  the worker can never race the commit.
- **FAIL #2 — fail-safe undone on re-link (`ocr_fleet_slip.py`).** `_apply_vehicle_config_from_link`
  sent provider-less vehicles to Direct Expense; `on_update` fires it on BOTH a Desk re-link AND the
  shell upload's own insert (`has_value_changed(fleet_vehicle)` on a Confirmed slip) — silently
  undoing the upload-time fail-safe and satisfying the PI guard. **Fix:** it now fail-safes for
  shell-sourced slips (provider-less → blank `posting_mode` → stays Needs Review, invoice blocked);
  Drive keeps the Direct-Expense fallback; the operator can still set Direct Expense explicitly.
- Tests +3 (719 pass, ruff clean): atomic single-commit + no-partial-on-failure; shell fail-safe
  holds on re-link and with a provider. **A re-review of `0aeb256` is warranted** (2 focused fixes).
