# Deploy-request — erpocr_integration v1.12.0 (cumulative: includes v1.11.0) · **routine**

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-09-19
**Supersedes:** `DEPLOY-REQUEST-ERP-OCR-v1.11.0-2026-09-19.md` — deploy v1.12.0 directly; it contains all of v1.11.0.

- **Ref:** tag `v1.12.0` — tag object `f631b30`, **peeled `43ef66a`**. `origin/master` == `43ef66a` at tagging. CI green.
- **Hop:** **v1.10.4 → v1.12.0**, both sites (baseline 1.10.4, architect probe 2026-09-16).
- **MIGRATE: yes** — new fields on `OCR Import` (a hidden, read-only Jev section) and `OCR Settings` (Jev settings, off by default). Additive only; no patches. **No build. No config at deploy.**
- **GATES: none.** **Rollback:** re-deploy `v1.10.4` (`b9a7f13`); the new columns stay, unused.

## What it is
**v1.11.0 — auto-draft learning fixes (Q18, Willie's "auto-draft is not landing").** See the v1.11.0 request for detail:
learning from the submitted invoice, zero-value lines no longer block, long descriptions no longer break learning.
A real-bench smoke caught and fixed a bug that would have failed every OCR-linked PI submit.

**v1.12.0 — Jev supplier-matching shadow trial (Q17, Willie's go 2026-09-19).** After each extraction a background job
asks TypeSafe Jev to pick the supplier and stores its pick beside our matcher's. **It changes nothing anyone sees:**
fields hidden on the form, never used for matching, status or auto-draft. **Off by default** — nothing is sent to
TypeSafe until Willie enables it per site. Sends only the printed supplier name, VAT number and candidate supplier
names/aliases. Soft monthly spend cap (default US$2). Real-bench smoke: two live calls correct at p ≥ 0.98, ~US$0.00004
each; the API key absent from every stored field and error log.

Review: Sonnet builders; Terra (GPT-5.6) + Grok 4.5 on each release, findings fixed; real-bench smokes on the dev site
(rolled back). 926 → **1,014 tests**, ruff clean.

## Steps
1. Deploy `v1.12.0` on both sites (get-app at tag; **migrate**).
2. **Smoke, 2 min, as Danell:** submit one OCR-linked Purchase Invoice — it must submit normally; its OCR Import shows Completed.
3. **Architect verification (read-only):** `get_versions` → 1.12.0; no `OCR Submit Learning Failed` / `Jev Shadow Failed` logs.

## Post-deploy, Willie only (NOT a Starktail step)
To start the trial on a site: create a **dedicated TypeSafe API key for erpocr** (not jev_lab's key), then in
OCR Settings → *Jev shadow trial*: paste the key, tick *Enable*. The trial is scored by the architect after 4–6 weeks.

---
**Status:** REQUESTED 2026-09-19.
