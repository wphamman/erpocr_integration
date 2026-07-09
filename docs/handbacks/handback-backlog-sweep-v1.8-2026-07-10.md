# Handback from Claude Code — v1.8.0 backlog sweep (Q6 + Q7a–d + Q8 + ADR-0011 CI gate) — 2026-07-10

> **For the operator (Willie):** paste this into the erpocr_integration architect session.
> Frame as "Handback from Code session — v1.8.0 backlog sweep."

---

## 1. Branch and commits

- **Branch:** `feature/backlog-sweep-v1.8`
- **Base:** `master` at `cbb7ec8` (one docs commit ahead of the kickoff's stated `6a0120e` — the architect's own "Q6-Q8 ruled" register update; expected drift, verified before starting)
- **Commits made this session** (9, oldest first):
  - `795758b` ci: add ADR-0011 dist-freshness gate — committed SPA dist must match a fresh build
  - `66c95e1` fix(fleet): Q6 — stop capturing the vestigial control account on the Fleet Card path
  - `10451ae` fix(reconcile): C(d) — getdate() hardening on statement-recon date compares
  - `ee8459e` feat(fleet): C(a) — Actual-VAT injection on fleet PI creation via the shared tax builder
  - `392f616` feat(ingest): C(b) — decode-verify gate on every image ingest path
  - `ec592a6` feat(fleet): Q8 — opt-in Fleet Card auto-record + bulk Mark Recorded
  - `88775f2` feat(matching): Q7(c) — supplier-scoped item aliases beat global ones
  - `0c1e116` docs: v1.8.0 — version bump, CHANGELOG, knowledge-doc + surface-doc updates
  - `eb7aeba` fix: apply /code-review findings — 6-angle review of the v1.8.0 sweep
- **Push status:** pushed to `origin/feature/backlog-sweep-v1.8` (remote tip `eb7aeba` confirmed via ls-remote). **No merge, no tag, no deploy** — per the kickoff boundary; the architect merges + tags.
- **Working tree:** clean.

---

## 2. Files changed

`git diff --stat master..HEAD`: **32 files, +1854 / −178.** By area:

- `.github/workflows/ci.yml` — +28 — the ADR-0011 dist-freshness job (Node 22, `npm ci && npm run build` in `frontend/`, fails if `public/accounts/` + `www/accounts.html` drift)
- `erpocr_integration/fleet_api.py` — Q6 capture removal, `bulk_mark_recorded` endpoint (per-row savepoint), pipeline auto-record trigger note, `_verify_image_decodable` delegates to the shared gate
- `erpocr_integration/tasks/auto_record.py` — NEW — Q8 auto-record: confidence gate, skip-reason audit (`update_modified=False`, idempotent writes), completes via `mark_recorded()`
- `erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/` — controller (on_update trigger, Q6, Q7a tax-builder delegation, Direct-Expense-flip expense fallback, quiet-msgprint flag), doctype JSON (`auto_recorded` + `auto_record_skipped_reason`), list JS (bulk action + restored indicator map)
- `erpocr_integration/erpnext_ocr/doctype/ocr_item_alias/ocr_item_alias.json` — supplier Link field; autoname `field:ocr_text` → `hash`; `unique: 1` dropped
- `erpocr_integration/tasks/matching.py` — supplier-scoped → global alias tiers in `match_item`; fuzzy alias-pool scoping; explicit `order_by`
- `erpocr_integration/erpnext_ocr/doctype/ocr_import/ocr_import.py` — supplier-scoped `_save_item_alias` (filter-based, `order_by`)
- `erpocr_integration/erpnext_ocr/doctype/ocr_delivery_note/ocr_delivery_note.py` + `dn_api.py` — DN alias learning repaired for hash naming + supplier pass-through into matching
- `erpocr_integration/api.py` — shared `is_image_decodable`, decode gate on `upload_pdf`, `_run_matching` passes supplier into exact + fuzzy tiers
- `erpocr_integration/tasks/drive_integration.py` — shared `_validate_scan_content` gate used by all three Drive pipelines
- `erpocr_integration/tasks/email_monitor.py` — decode gate on image attachments
- `erpocr_integration/tasks/reconcile.py` — getdate() hardening (period parsed once; None posting_date = brought-forward)
- `erpocr_integration/erpnext_ocr/doctype/ocr_settings/ocr_settings.json` — `enable_fleet_auto_record` (default 0)
- Tests: `test_auto_record.py` (NEW, 30), `test_decode_gate.py` (NEW, 12), plus additions in test_matching / test_ocr_import / test_reconcile / test_fleet_controller / test_fleet_workflow; conftest gained real `getdate` semantics + savepoint reset
- Docs: CHANGELOG (1.8.0), CLAUDE.md gotchas, architecture.md, implementation-patterns.md, CROSS_APP_SURFACE.md (§2a), FLEET_DASHBOARD_DATA_SPEC.md, `__init__.py` → 1.8.0

---

## 3. Test / lint / build status

- **Test suite:** **820 pass, 0 fail** (baseline 764 at session start, verified). **56 tests added**, 0 failures introduced.
- **Lint:** `ruff check` clean; `ruff format --check` clean.
- **CI dist gate proven both ways locally:** green on HEAD (fresh `npm ci && npm run build` → byte-identical, empty porcelain via the workflow's own test), red on a deliberate one-character `frontend/src` tweak (hash flipped `index-Cf3QiPiq.js` → `index-WtG3JP74.js`, 3 paths flagged). Tweak reverted, never committed; dist restored byte-identical.
- **Real-bench walk (driver-dev.local): 19/19 PASS** — see §8. Synthetic data only; transaction rolled back, site untouched.

---

## 4. Decisions made during implementation

- **Q8 "Other" slips never auto-record** — Willie ruled in-session (AskUserQuestion, start of session): auto-record applies to Fuel + Toll only; `Other` (unauthorized flag) always parks for human review.
- **Q8 "confirmed vehicle" = `vehicle_match_status ∈ {Auto Matched, Confirmed}`** — exact registration match or driver/operator pick; never fuzzy `Suggested`. Mirrors auto-draft's `_HIGH_CONFIDENCE_STATUSES`.
- **Auto-record trigger is the controller's `on_update` ONLY.** Spec said "lands/updates to Matched"; `Document.save()` fires `on_update` on real Frappe for BOTH the pipeline save in `fleet_gemini_process` and Desk saves, so one hook covers both. An explicit pipeline call (my first cut) double-evaluated the gate on the same save — removed in the review pass. Bench walk proves the pipeline path works through the hook.
- **Bulk action requires status = `Matched` strictly** — tighter than single `mark_recorded()` (which also accepts Needs Review). Rationale: bulk is for the verified backlog; Needs Review rows need eyes. Single-slip behavior unchanged.
- **Q7c naming: `OCR Item Alias` autoname → `hash`, ocr_text unique index dropped.** Required so the same text can exist per-supplier. Consequences handled: all lookups are now filter-based with explicit `order_by="modified desc, name asc"` (R8 — most-recently-curated row wins deterministically on v15 AND v16, and corrections target the same row reads return); existing rows keep their names; no data migration needed (legacy rows have NULL supplier = global tier).
- **DN pipeline joined the Q7c semantics** (not in spec, forced by the review): DN matching now passes the DN's supplier, and DN alias learning is supplier-scoped + filter-based. Without this, (a) the DN's old name-based `exists()` check would insert unbounded duplicate aliases (hash naming broke it), and (b) invoice-learned scoped aliases would be invisible to DN matching — a cross-pipeline learning-loop regression.
- **Q6 follow-through: Direct-Expense-flip fallback.** With the control account no longer captured, a Fleet Card slip flipped to Direct Expense during review would create a PI with a blank expense account. `create_purchase_invoice` now falls back to `OCR Settings.fleet_expense_account`. Also updated FLEET_DASHboard_DATA_SPEC.md: `expense_account` documented blank on Fleet Card slips since v1.8.0.
- **Q7a via an adapter, not a signature change:** fleet PI creation calls the invoice pipeline's `_build_taxes_from_template` through a SimpleNamespace proxy (`tax_amount=vat_amount`, `subtotal=0` short-circuits the inclusive-rate detector). Chose not to refactor the shared helper's signature — that touches the shipped invoice path. Flagged as an altitude question below.
- **First-attempt Drive decode failures record `drive_retry_count=0`** — consistent with the existing accounting (count increments on subsequent polls toward MAX_DRIVE_RETRIES).
- **`/code-review` (6-angle, 10 findings) ran in-session; 8 fixed in `eb7aeba`.** Most serious: my bulk-action list script had **clobbered master's existing `ocr_fleet_slip_list.js`**, deleting the status-indicator colour map — restored. Also: pre-migrate `AttributeError` window on `self.auto_recorded` (now `.get()`), skip-reason writes bumping `modified` inside on_update (operator's open form would throw TimestampMismatch — now `update_modified=False` + idempotent), bulk per-row savepoint (a row failing mid-save could commit Completed while reporting skipped), fuzzy-tier alias-pool scoping.
- **ADR-0009 discipline:** `frappe.db.savepoint(save_point)` / `rollback(save_point=)`, `db.get_value` with `["is", "not set"]`, and `frappe.parse_json` all verified on the real bench before/while being used. conftest gained REAL `getdate` semantics; decode-gate tests feed a real PIL JPEG + a real corrupt body.

---

## 5. Open questions for the architecture chat

- **Bulk Mark Recorded runs synchronously in the request** (≤200 rows, full doc load + save each, `freeze: true`). On a slow prod a max selection could brush the gunicorn timeout (rows are individually savepointed, so partial completion is consistent, but the UX is a dead freeze). Options: keep (backlog is ~34, cap is generous) / background-queue with realtime progress (the app's usual pattern). Recommendation: keep for v1.8.0, queue it if backlogs grow.
- **`tax_proxy` duck-typing (Q7a):** if `_build_taxes_from_template` ever reads another OCR Import attribute, the fleet path AttributeErrors on prod while the invoice path keeps working. Options: keep (comment warns) / refactor the helper to explicit inputs (`tax_template, company, tax_amount, rates_include_tax`) with both callers passing real values. Recommendation: refactor at the next invoice-path release, not in this one.
- **Supplier-keyed tiers accept a `Suggested` (fuzzy-guessed) supplier** at Auto Matched item confidence — a scoped alias for the guessed supplier marks items Auto Matched even though the supplier itself is unconfirmed. This is PRE-EXISTING tier-1 (`Item Supplier` lookup) behavior since v1.1.0 — Q7c made tier 2 consistent with it, not worse — and auto-draft is still blocked by the Suggested supplier. Design question: should supplier-derived tiers require a confirmed supplier? (Would change tier 1 too.)
- **`fleet_management`'s Fleet Dashboard reads `expense_account`** per FLEET_DASHBOARD_DATA_SPEC.md — the spec (this repo) is updated for Q6, but if the dashboard renders that column for Fleet Card slips it will now show blank on new slips. Flag to the fleet architect; no erpocr-side action.

---

## 6. Memory delta (durable code-side facts)

- `tasks/auto_record.py` is Q8's auto-record module; audit fields `auto_recorded` / `auto_record_skipped_reason` on OCR Fleet Slip; setting `enable_fleet_auto_record` (OCR Settings, default 0). The trigger is `OCRFleetSlip.on_update` only.
- `fleet_api.bulk_mark_recorded` is a new §2a whitelisted method (32 → 33) — documented in CROSS_APP_SURFACE.md **with a placeholder baseline note; the architect re-baselines the SHA at merge**.
- `OCR Item Alias` is hash-named with an optional `supplier` field since v1.8.0 — **never look rows up by document name**; filter on `ocr_text` + `supplier` with explicit `order_by` (duplicates are possible).
- The shared image decode gate is `api.is_image_decodable`; Drive pipelines validate via `drive_integration._validate_scan_content`.
- CI now has a `dist-freshness` job — editing `frontend/src` without rebuilding + committing the dist fails the build (rebuild: `cd frontend && npm ci && npm run build`).
- `driver-dev.local` is a SITE inside the `starpops-test` Docker stack (bind-mounts this repo at `apps/erpocr_integration`); bench work without HTTP: `docker exec starpops-test-backend-1 bash -lc 'cd /home/frappe/frappe-bench/sites && ../env/bin/python …'`.
- Test suite baseline on this branch: **820 pass** (was 764 at v1.7.0).

---

## 7. Known issues / risks

- **Migrate required at deploy** (new fields on OCR Settings / OCR Fleet Slip / OCR Item Alias + the dropped unique index). The deploy-to-migrate window is safe: `.get()` guards the new-column reads (review fix).
- **Duplicate alias rows are now possible** (unique index dropped; check-then-insert learning has a benign race). Reads and corrections both target the most-recently-modified row deterministically, so duplicates are cosmetic, but a periodic cleanup query may be worth a future nit.
- **`match_item` costs up to two queries per line** (scoped miss → global lookup) instead of one. A single ifnull-based query would need raw SQL/QB; chose correctness + clarity. Revisit only if extraction profiling ever flags it.
- **Auto-record is opt-in and OFF** — regression-tested byte-identical to v1.7.0 when off. Turning it on at deploy is a Willie/UI step (OCR Settings), not automatic.

---

## 8. How to test/verify locally

```bash
cd /home/willie/dev/erpocr_integration
git checkout feature/backlog-sweep-v1.8

# Suite + lint (fleet venv — erpocr has none)
/home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q   # 820 passed
/home/willie/dev/fleet_management/.venv/bin/python -m ruff check erpocr_integration/         # clean

# CI dist gate, both ways
cd frontend && npm ci && npm run build && cd .. \
  && git status --porcelain -- erpocr_integration/public/accounts erpocr_integration/www/accounts.html  # empty = green
sed -i 's/Outstanding Work/Outstanding Work!/' frontend/src/pages/OutstandingWork.tsx \
  && cd frontend && npm run build && cd .. && git status --porcelain -- erpocr_integration/public/accounts  # non-empty = red
git checkout -- frontend/src erpocr_integration/public/accounts erpocr_integration/www/accounts.html && git clean -f erpocr_integration/public/accounts/ && cd frontend && npm run build && cd ..

# Real-bench walk (already run this session: 19/19 PASS, rolled back).
# The script lived at .verify_v18_walk.py (deleted; recoverable from this handback's session)
# — it migrates nothing, creates synthetic ZZ-VERIFY docs, drives auto-record OFF/ON/fuzzy/no-total,
# bulk_mark_recorded on a mixed 4-row selection, and scoped-vs-global alias matching, then rolls back.
docker exec starpops-test-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site driver-dev.local migrate'
```

Expected: 820 tests pass; dist gate green→red→green; bench walk asserts Matched (off) / Completed with NULL PI (on) / skip reasons (fuzzy, missing payload) / bulk records 2 + skips Direct-Expense + nonexistent rows.

---

## 9. Workflow notes (optional)

- The kickoff's trust-posture rule paid for itself twice: the base-SHA drift was benign (architect register commit), and the "verify, don't assume" grep of fleet's `monthly_summary.py` confirmed the Matched+Completed read before I claimed no consumer impact.
- The 6-angle in-session review caught a genuinely embarrassing miss (clobbered list JS — `Write` to a "new" path then `mv` over an existing file bypassed the overwrite guard). Worth a template line: **check `git ls-files <dir>` before creating any new file next to a doctype.**
- Second-pass Codex review deliberately NOT run builder-side — the kickoff assigns the two-pass review (in-session + Codex) to the architect at merge.

---

**End of handback.**
