# Deploy-request — erpocr_integration v1.12.0 · **routine** · next hop after v1.11.0

**To:** portfolio coordinator (ADR-029). **From:** `erpocr_integration` architect · 2026-09-19
**Sequencing:** v1.11.0 is already on `DEPLOY-LIST-2026-09-19` (Python only). Let it land as listed; this is the NEXT hop. Do not swap it into today's list.

- **Ref:** tag `v1.12.0` — tag object `f631b30`, **peeled `43ef66a`**. `origin/master` == `43ef66a` at tagging. CI green.
- **Hop:** **v1.11.0 → v1.12.0**, both sites. **GATE: v1.11.0 landed and verified** (its Danell submit smoke passed). If a list ever carries both, v1.10.4 → v1.12.0 in one migrate is also fine — v1.12.0 contains all of v1.11.0.
- **MIGRATE: yes** — new fields on `OCR Import` (a hidden, read-only Jev section) and `OCR Settings` (Jev settings, off by default). Additive only; no patches. **No build. No config at deploy.**
- **Rollback:** re-deploy `v1.11.0` (`affdce0`); the new columns stay, unused.
- **erp-test (v16):** `version-16` tip now carries 1.12.0 (pin-only delta). Pulling the tip instead of `3cb683a` is fine; it just adds the hidden Jev fields.

## What it is
**v1.12.0 — Jev supplier-matching shadow trial (Q17, Willie's go 2026-09-19).** After each extraction a background job
asks TypeSafe Jev to pick the supplier and stores its pick beside our matcher's. **It changes nothing anyone sees:**
fields hidden on the form, never used for matching, status or auto-draft. **Off by default** — nothing is sent to
TypeSafe until Willie enables it per site. Sends only the printed supplier name, VAT number and candidate supplier
names/aliases. Soft monthly spend cap (default US$2). Real-bench smoke: two live calls correct at p ≥ 0.98, ~US$0.00004
each; the API key absent from every stored field and error log.

Review: Sonnet builders; Terra (GPT-5.6) + Grok 4.5 on each release, findings fixed; real-bench smokes on the dev site
(rolled back). 968 → **1,014 tests**, ruff clean.

## Steps
1. Deploy `v1.12.0` on both sites (get-app at tag; **migrate**).
2. **Smoke:** open any OCR Import as Danell — the form looks exactly as before (no Jev section).
3. **Architect verification (read-only):** `get_versions` → 1.12.0; `enable_jev_shadow` = 0 on both sites until Willie turns it on; no `Jev Shadow Failed` logs.

## Post-deploy, Willie only (NOT a Starktail step)
To start the trial on a site: create a **dedicated TypeSafe API key for erpocr** (not jev_lab's key), then in
OCR Settings → *Jev shadow trial*: paste the key, tick *Enable*. The trial is scored by the architect after 4–6 weeks.

---
**Status:** REQUESTED 2026-09-19.
