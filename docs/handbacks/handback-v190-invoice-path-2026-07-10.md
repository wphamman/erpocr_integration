# Handback — erpocr v1.9.0 invoice-path (Q9 + Q10 + Q11)

> BUILDER → ARCHITECT. Session built from `code-kickoff-erpocr-v190-invoice-path-2026-07-10.md`.
> Branch pushed; **NOT merged/tagged/deployed** — architect runs the two-pass review + merge + tag.

## 1. Branch and commits

- **Branch:** `feature/v190-invoice-path` (pushed, tracks `origin/`, HEAD `dc21b27`).
- **Base:** `cecf959` (master tip == the architect's Q11-opening docs commit, 1 ahead of tag `v1.8.0`).
- **Working tree:** clean. Remote has the branch at `dc21b27`.
- **Commits (4):**
  - `854f378` feat(auto-draft,matching): Q11 totals gate + Q10 chained-confidence cap
  - `d2f698e` refactor(tax): Q9 — `_build_taxes_from_template` explicit args, kill fleet proxy
  - `7946eae` fix(auto-draft): Q11 gate must reconcile tax-inclusive rates against total *(code-review fix)*
  - `dc21b27` docs: v1.9.0 — version, CHANGELOG, CLAUDE.md, implementation-patterns

Build order was **A → B → C** as specified (Q11 first, Q9 last with freshest attention).

## 2. Files changed (`git diff --stat cecf959..HEAD` — 13 files, +743/−43)

Production:
- `erpocr_integration/tasks/auto_draft.py` (+85) — Q11 `_totals_reconcile` + gate wiring.
- `erpocr_integration/tasks/matching.py` (+44) — Q10 `_cap_to_supplier` + `supplier_status` params.
- `erpocr_integration/api.py` (+12) — thread `supplier_match_status` into invoice item matching.
- `erpocr_integration/dn_api.py` (+12) — same for the DN path.
- `.../doctype/ocr_import/ocr_import.py` (+59) — Q9 `_build_taxes_from_template` signature; PI/PR callers.
- `.../doctype/ocr_fleet_slip/ocr_fleet_slip.py` (+15) — Q9 fleet caller; `SimpleNamespace` proxy deleted.
- `erpocr_integration/__init__.py` — version → 1.9.0.

Tests: `test_auto_draft.py` (+194), `test_matching.py` (+219), `test_ocr_import.py` (+119).
Docs: `CHANGELOG.md`, `CLAUDE.md`, `docs/implementation-patterns.md`.

## 3. Test / lint / build status

- **Tests: 824 → 855 pass, 0 fail** (venv: `fleet_management/.venv`; frappe fully mocked). +31 = 14 Q11 + 10 Q10 + 7 Q9.
- **Ruff:** `check` clean, `format --check` clean (81 files).
- **Bench smoke (driver-dev, ADR-0009 — /verify):** brought up the OOM-stopped `starpops-test` stack, ran, stopped it again. All create paths built correct taxes through the refactored builder, **all rolled back** (`frappe.db.rollback()` — nothing persists):
  - Percentage-template PI → net **1000**, grand **1150** (15% VAT).
  - Actual-template PI (temp template, rolled back) → extracted VAT **150** injected into the Actual row.
  - Fleet Direct-Expense PI → net **500**, grand **575**.
  - Plus 5 builder-level real-framework assertions (passthrough, inclusive-flag, injection, fleet-caller shape, company-mismatch throw) — all PASS.
  - Note: `gemini_api_key` undecryptable on that bench (documented env quirk) — irrelevant, smoke builds staging docs directly.

## 4. Decisions / deviations

- **Q11 tolerance (asked Willie): `max(1%, R1.00)`** — module constants `_TOTALS_TOLERANCE_PCT=0.01`, `_TOTALS_TOLERANCE_ABS=1.00`, not a setting (per spec — no evidence a knob is needed).
- **Q11 degenerate handling:** line sum ≤ 0 → pass (unverifiable). Subtotal 0/absent → fall back to `total_amount − tax_amount`; if that's ≤ 0 → pass. Gate is **bidirectional** (catches under- as well as over-draft — cheap and strictly safer).
- **Q11 tax-inclusivity (see §7 — the one real find):** the gate reuses `_detect_tax_inclusive_rates` and reconciles inclusive rates against `total_amount`, exclusive against `subtotal`. This was NOT in the first cut — added after `/code-review` caught that comparing an inclusive line sum against the subtotal false-fails every inclusive invoice. The discount specimen (`OCR-IMP-01918`) has *exclusive* rates (detector returns False), so it's still caught against the subtotal.
- **Q10:** implemented exactly as ruled (cap, don't skip). `_cap_to_supplier(status, supplier_status)` downgrades only `Auto Matched`→`Suggested`, and only under a `Suggested` supplier. Only the two supplier-keyed tiers are capped; global-alias/exact/fuzzy are untouched (they don't depend on the supplier being right). Threaded through both invoice + DN paths.
- **Q9 signature:** `_build_taxes_from_template(tax_template, company, tax_amount, rates_include_tax)`. `_detect_tax_inclusive_rates(ocr_import)` stays at the invoice call site and is passed in; fleet caller passes `rates_include_tax=False`. Verified equivalent to the old proxy: the old `subtotal=0` forced `_detect_tax_inclusive_rates` to return False, and the builder read `total_amount`/`items` *only* via that detector — nothing else lost. JE path (which calls the detector directly) untouched.
- **Out-of-scope held:** did NOT add a Gemini discount-schema field (I agree with the spec — the gate makes the class safe; a schema field is a larger design and only worth it if discount volume proves high. No argument to build it now). No new OCR Settings knob.

## 5. Open questions (for the architect)

- **No new open questions.** Implementation matched the spec + rulings cleanly; the one surprise (tax-inclusive false-fail) was a code-review catch I fixed in-session, not a design question.
- **For the Q11 ADR you'll write:** worth recording the tax-inclusivity dimension explicitly — it's the non-obvious part. The gate's reference is `total_amount` for inclusive rates, `subtotal` (with `total − tax` fallback) for exclusive, keyed off the existing `_detect_tax_inclusive_rates`. That's the coupling a future editor must not break.
- **Tolerance is a guess, not calibrated:** `max(1%, R1.00)` catches the 5% specimen with margin and clears rounding noise, but there's no corpus study behind the exact 1%. If real skip-rate telemetry (group-by `auto_draft_skipped_reason`) shows false skips, the constant is a one-line change.

## 6. Memory / surface delta

- **CROSS_APP_SURFACE.md: NO DELTA.** No whitelisted method added/renamed/removed, no doctype field, no response-shape or contract change. Q9 changes a private `_`-prefixed helper signature (not surface); Q10/Q11 are matching/auto-draft internals. I left the doc's "Current through v1.8.0" baseline marker untouched — **the architect re-baselines it to v1.9.0 (no delta) at merge**, consistent with the v1.8.0 merge-reconcile pattern.
- **New durable facts** (now in CLAUDE.md gotchas + implementation-patterns.md):
  - `tasks/auto_draft.py:_totals_reconcile` — the Q11 gate; tolerance constants `_TOTALS_TOLERANCE_PCT`/`_TOTALS_TOLERANCE_ABS`; tax-inclusivity coupling to `_detect_tax_inclusive_rates`.
  - `matching._cap_to_supplier` — the Q10 min-of-chain cap; callers must thread `supplier_status`.
  - `_build_taxes_from_template` now explicit-args — any new caller passes values, never a doc/proxy.

## 7. Known issues / risks

- **The tax-inclusivity trap (fixed, but note it).** First cut of the Q11 gate compared against subtotal unconditionally → false-failed every tax-inclusive invoice (rates already include VAT) by the tax amount, silently disabling auto-draft for that class. Three independent `/code-review` finders converged on it. **Fixed** in `7946eae` (+2 regression tests: inclusive reconciles; inclusive overstatement still caught). Fail-safe direction throughout (only ever skipped to review — never drafted a wrong number), so no data risk was ever live, but it would have quietly degraded coverage. This is the class of bug the CLAUDE.md "wholesale mock" gotcha warns about — the mocked suite was green; only reasoning about the inclusive path (and the bench) surfaced it.
- **Regression surface of Q9 (the shipped invoice path):** highest-risk item, done last. Actual-row injection (ADR-0008 / Cargo Compass) is proven byte-identical by (a) existing PI/PR/fleet integration tests through the create methods, (b) new direct-unit tests of the refactored signature, (c) the real-bench smoke. I'm confident it's clean.
- **No calibration data behind the Q11 tolerance** (see §5).

## 8. How to test / verify locally

- **Unit:** `cd /home/willie/dev/erpocr_integration && /home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q` → 855 pass. Focused: `-k "TotalsReconcile or ChainedConfidenceCap or BuildTaxesExplicitArgs"`.
- **Lint:** `.../.venv/bin/python -m ruff check erpocr_integration/ && ruff format --check erpocr_integration/`.
- **Bench smoke** (optional — `starpops-test` stack is OOM-stopped; start `starpops-test-{mariadb,redis-cache,redis-queue,redis-socketio,backend}-1`, then from `/home/frappe/frappe-bench/sites` run the builder against real templates on `driver-dev.local`, `frappe.db.rollback()` at the end; **stop the stack after** to relieve host memory pressure). The refactor adds no new framework call, so this is defense-in-depth, not load-bearing.
- **Grep proof (Q9):** `grep -rn "_build_taxes_from_template(" erpocr_integration --include=*.py` → 3 callers, all explicit-args; no `SimpleNamespace` proxy for the tax builder remains (the only `SimpleNamespace` left is `statement_api`'s unrelated one-attr recon proxy).

## 9. Workflow notes

- `/code-review` (high) run on the diff → 1 confirmed finding (tax-inclusivity), fixed + regression-tested in-session, then re-verified. No Codex second pass (kickoff §6 specified `/code-review`, not an external handoff; the multi-finder review covered it).
- `/verify` satisfied by the bench smoke (PI creation is the runtime surface).
- Started the OOM-stopped `starpops-test` stack for the smoke and **stopped it again afterward** to return the host to its prior state (other stacks — `frappe-eos-test`, `starpops-v16` — are running).
- No `.env` or credential touched; no live/prod access; the `OCR-IMP-01918` fixture is synthetic (magnitudes from the real specimen, no personal data).
