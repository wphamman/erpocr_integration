# Deploy-request — erpocr_integration v1.11.0 · **routine**

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-09-19
**Shape:** as v1.10.4.

**Why:** Willie reported auto-draft "not landing" (2026-09-19). A read-only probe of both sites (Q18) showed it
fires but lands on only 17% (SP) / 11% (Cactus) of imports since 2026-08-01. Two defects are fixed here:
accepting a pre-filled suggestion never taught the matcher (41 + 24 skipped auto-drafts), and a haulier's
R0 return-leg line blocked every one of its invoices (17). Willie asked for the fix. **Routine** — no
data change on deploy; the effect shows as more auto-drafts over the following weeks.

- **Ref:** tag `v1.11.0` — tag object `bff5398`, **peeled `affdce0`**.
- **Branch-reachability:** `origin/master` HEAD == `affdce0` at tagging (a docs-only commit follows it — this file).
- **Hop:** **v1.10.4 → v1.11.0**, both sites. Baseline: `get_versions` → 1.10.4 on both (architect probe 2026-09-16).
- **Delta class:** Python only (`api.py`, `ocr_import.py`, `tasks/auto_draft.py`) + tests + docs. **No doctype
  JSON, no hooks, no patches, no fixtures, no install change → MIGRATE: not required** (Press will run one
  anyway; harmless). **No build** (no SPA change). **No config.**
- **GATES: none.** Independent of every other release.
- **Deploy-bound manual steps: NONE, either direction.**
- **Rollback target:** re-deploy tag `v1.10.4` (`b9a7f13`). Aliases/mappings learned in between stay (they
  are ordinary learned rows, same as any operator confirm would write).

**What it is:**
1. **Learning at submit.** When a PI/PR created from an OCR Import is submitted, the supplier alias and the
   item/service mappings are learned from the submitted document, for matches the operator accepted without
   changing. Never for auto-drafted records. Runs inside a savepoint; a learning failure is logged and can
   never fail or roll back the submit.
2. **Zero-value lines** (rate 0 and amount 0, non-stock, no PO/PR link) no longer block readiness or
   auto-draft, and are left off the PI — what operators already did by hand. Free stock lines are kept.
3. **Learning text over 140 characters** no longer aborts learning (was also latent on the confirm path).

**Review:** Sonnet builder; Terra (GPT-5.6) + Grok 4.5 on the branch (core PASS; savepoint isolation,
auto-draft skip and missing-Item guard sent back and fixed); then a **real-bench smoke** on the dev site in a
rolled-back transaction caught two bugs the mocked suite could not see — a savepoint name with hyphens that
would have failed **every** OCR-linked PI submit with a MariaDB syntax error, and the 140-char overflow.
Both fixed with regression tests; Terra reviewed that delta. 926 → **968 tests**, ruff clean, CI green on `affdce0`.
Final smoke: 15/15 checks PASS, incl. 17 service mappings learned from 5 real records with zero errors.

**Steps:**
1. Deploy `v1.11.0` on both sites (get-app at tag).
2. **Smoke, 2 min, as Danell (the one that matters):** open a Draft Created OCR-linked Purchase Invoice and
   **submit it** — it must submit normally. Then open its OCR Import: status Completed.
3. **Architect verification (read-only):** `get_versions` → 1.11.0; no `OCR Submit Learning Failed` Error Log
   rows; over the next week, OCR Supplier Alias rows appear for suppliers that used to repeat as 'Suggested'.

**Rollback:** re-deploy tag `v1.10.4`.

---
**Status:** REQUESTED 2026-09-19.
