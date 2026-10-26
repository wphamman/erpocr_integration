# Builder kickoff: erpocr_integration v1.14.0

**From:** erpocr_integration architect · **Date:** 2026-10-26 · **Ruled by Willie:** v1.14.0 YES, all three (A, B, C) on his desk
2026-10-26. Item D waits on Willie's A/B choice. Build it as its own commit; the architect drops it if he picks B.

## 1. Where you work
- Worktree: `/home/willie/dev/erpocr_integration/.worktrees/v1.14.0`, branch `feature/v1.14.0`, from master `3676a69`.
  **Work only there.** Don't touch the main checkout, other repos, `~/dev` or any site.
- Tests: `/home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q` (1,062 pass today).
  Lint: same venv, `-m ruff check erpocr_integration` and `-m ruff format --check erpocr_integration`.
- **No prod, no erp-test, no network writes.** The dev bench (`starpops-test-backend-1`, site `driver-dev.local`, frappe 15.95)
  is available for real-framework checks. Its erpocr app is a bind mount of the MAIN checkout, not your worktree. To test there,
  copy files in temporarily, or run snippets that import from your worktree path, and tell the architect what you did.
- Upstream source for framework questions: shallow-clone `frappe` at `v15.120.0` and `v16.51.0` into the session scratchpad
  (not into `~/dev`).
- Read first: `CLAUDE.md`, `docs/architecture.md`, `docs/implementation-patterns.md`, `docs/architecture/OPEN-QUESTIONS.md`
  (Q22, Q23), `~/.claude/frappe-app-learnings.md` (the 2026-10-09 v16 entry; the fixtures/seed entries).
- Commit on `feature/v1.14.0` only, one commit per item. **Do not merge, tag or push.** The architect does that.

## 2. Items

### A. Gemini billing-failure alert (Q23, ruled yes: "and also email Danell")
**Why:** on 2026-10-23 Star Pops' Gemini key returned **HTTP 402 Payment Required** for 3 days. Nothing alerted, and every Drive scan
in the window used up its 3 tries (`MAX_DRIVE_RETRIES`) and stopped. Google billing accounts are now PRE-PAID, so this will
recur, and small companies (Newel) will share Star Pops' key.

Build:
1. **Detect** a billing failure in `tasks/gemini_extract.py::_call_gemini_api`:
   - HTTP **402**; or
   - HTTP 429 whose body says `RESOURCE_EXHAUSTED` together with prepay/credits/billing wording. An ordinary 429 rate limit must
     stay a retryable rate limit. Base the test on the response body; see `docs/architecture.md` "Prepay credit depletion".
   - **Don't retry** a billing failure in-job; it never fixes itself.
   - Raise a distinct exception class (e.g. `GeminiBillingError`) so callers can tell it apart.
   - Every Gemini call site goes through this (invoice, DN, fleet, statement, classifier).
2. **Alert:** email System Managers (enabled users with the role, excluding Administrator and Guest) **plus** a new OCR Settings field
   `billing_alert_recipients` (Table MultiSelect of User, or Small Text of emails; your call, explain it). Danell is added by the
   operator, not hard-coded.
   - At most **once per 6 hours per site**: a persisted timestamp (OCR Settings hidden field, or `frappe.cache` with expiry).
     Explain which and why.
   - Plain-language subject and body (wording rules, below): what stopped, since when, what to do (top up / check the Google
     billing account for the project behind the key), and that failed documents need re-running.
   - Use `frappe.sendmail` with `now=False`.
   - **Never put the API key in the email, a log or a stored field.** Same discipline as the Jev trial in CLAUDE.md: don't bind
     the key in a frame that can reach a logged traceback.
3. **Banner:** a red message on the **OCR Import list** (and ideally the OCR Fleet Slip list) while billing is failing: "OCR is
   stopped: Google refused payment for the AI service since <time>. Ask Willie to top up the billing account. New uploads will fail
   until then."
   - Clear it on the next successful Gemini call.
   - Use a cheap whitelisted GET that any OCR user can call, returning only `{down: bool, since: datetime}`. Never return
     settings, or anything secret.
4. **Drive self-heal:** a billing failure must **not** count toward `drive_retry_count` / `MAX_DRIVE_RETRIES`
   (`tasks/drive_integration.py`, `_record_drive_scan_failure` and the four `_process_*` paths). After billing is fixed, the next
   15-minute poll retries those files on its own.
   - Keep the existing dedup safety: still one Error placeholder per file, no duplicates.
   - Add tests for "billing failure ×10 then success": the file is extracted exactly once, with no leftover Error rows.
5. Tests for detection (402; 429 billing; 429 plain rate limit stays retryable), the throttle, recipients, the banner endpoint
   and the Drive behaviour. Mock `requests` responses realistically (status code + JSON body).

### B. v16 app-owned Sidebar (Q22: the design was agreed with erp_ux, but is OPEN on one point)
**Goal on v16 (frappe 16.51):**
- (1) a **standard** `Sidebar` named exactly `ERPNext OCR`, `app = erpocr_integration`, items **Workspace + DocType only, no URL
  item**: Home → workspace ERPNext OCR; OCR Import, OCR Delivery Note, OCR Fleet Slip, OCR Statement, OCR Supplier Alias, OCR
  Item Alias, OCR Service Mapping, OCR Settings.
- (2) neutralise the converted custom Sidebar `ERPNext OCR (Custom)` (`standard = 0`, no app);
- (3) delete the ghost `Module Def` `ERPNext OCR (Custom)` **only if** `custom = 1` and no Workspace, DocType, Report or Page has
  that module.

On v15, everything is a no-op. **Never a patch** (a patch that ran on v15 is marked done and never runs on v16). Guard everything on
`frappe.db.exists("DocType", "Sidebar")`.

**The open point you must settle FROM SOURCE before building:**
- frappe 16.51 puts `Sidebar` in `ORPHANABLE_ENTITIES` filtered `standard=True`. A standard Sidebar with no JSON file in the app
  is **deleted by the migrate orphan sweep**.
- Find out (a) where the sweep runs relative to `after_migrate` hooks in `frappe/migrate.py` at v16.51, and (b) whether shipping
  a Sidebar JSON in our module tree (`erpnext_ocr/sidebar/erpnext_ocr/erpnext_ocr.json`, or whatever path v16 expects) would
  **break or be ignored by a v15 migrate** (frappe v15.120 `sync_for` / `import_file_by_path`). Master is the v15 branch (ADR-032)
  and is what deploys to prod.
- Pick the design that survives a second migrate on v16 and is inert on v15. **Report the evidence with file:line** from both
  versions. If neither works cleanly, stop and report rather than improvise.
- Also read `get_module_shell` (~L720 in v16.51 `sidebar.py`) to confirm the row must be named exactly after the module.
- Write it idempotently: create if missing; if it exists and is ours, leave operator edits alone except restoring `app` /
  `standard`. Explain your choice.

### C. Three wording fixes (erp_ux wording rules)
1. **One name for the dashboard.** Today it's "ERPNext OCR Integration" (`hooks.app_title`, shown on the v16 Desktop icon), "OCR
   Accounts" (`add_to_apps_screen` title) and "Star Pops · Accounts" (`<title>` in `frontend/index.html` and
   `www/accounts.html`). **Use "OCR Accounts"** in all three.
   - The SPA build is committed (`public/accounts/`). If you change `frontend/index.html`, rebuild with the frontend's build
     script and commit the dist. Check that the dist diff is only the title change.
   - The desk workspace/module "ERPNext OCR" is a different object; don't rename it.
2. **"Approved By" shows the person's name, not a login.** Purchase Invoice custom fields from v1.13.0
   (`custom_duplicate_bill_approved_by` …). Stamp the approver's full name into a read-only Data field next to it, or make the
   displayed value the full name. Keep the login stored for audit; the duplicate-check rules must not change.
3. **Money in the duplicate-number conflict message:** `45747.0` → `R 45,747.00`. Use `frappe.utils.fmt_money(amount,
   currency=<that PI's currency>)` in `duplicate_bill._conflict_message`. Test it.
4. (small) In v16, ungrouped custom buttons move into the "Actions" menu. **Make "Upload File" on a new OCR Import a primary
   toolbar button** (`.addClass('btn-primary')`, as "Mark Recorded" already is) so staff see it. Check that the guides'
   wording ("click Upload File") is then true; fix any guide line that says otherwise.

### D. Unlink & Reset for clerks without Delete (WAITING on Willie's A/B; separate commit)
`OCRImport.unlink_document` (`ocr_import.py` ~L1650) requires **delete** permission on the linked PI/PR/JE. The invoice clerk's
roles don't have Delete on Purchase Invoice, and Frappe's Delete would also let her delete **cancelled** invoices, which we don't
want.
- **Change:** when the linked document is a **draft (docstatus 0)** AND it was created from **this** OCR record (its back-link,
  e.g. `custom_ocr_import` on PI/PR, or the JE equivalent, equals `self.name`), require **write** permission on that draft instead
  of delete, then delete it with `ignore_permissions=True`.
- **Cancelled (docstatus 2) still requires real delete permission**, exactly as today. Submitted still throws.
- If the back-link doesn't match, behave exactly as today.
- Same change in `OCRDeliveryNote` / `OCRFleetSlip` if they have the same method shape; list what you changed.
- Tests: clerk without delete + own draft → deleted; clerk + cancelled → refused; clerk + someone else's draft (back-link
  mismatch) → refused; submitted → refused.

## 3. Rules that bind you
- **Wording rules (erp_ux ADR-004)** for every new string a user sees: no codes or field names, people by name, errors say what to
  do next, plain words.
- **R8:** every new `get_all` / `get_list` has an explicit plain-field `order_by`. **No SQL expressions in `order_by` / `fields` /
  `group_by`** (v16 rejects them; we just shipped a fix for exactly that).
- Seed operator-facing records create-only (CLAUDE.md fixtures gotcha). No new fixtures.
- New Settings fields go in the OCR Settings doctype JSON, default off/blank, with plain help text.
- Don't add `ignore_permissions` anywhere except item D's narrowly-scoped delete.
- Version: bump `__version__` to `1.14.0`. Add a CHANGELOG entry and update CLAUDE.md "Currently v1.14.0". Add a CLAUDE.md gotcha
  bullet only for a genuinely new invariant (the billing-failure path likely is one).

## 4. Hand back (to the architect, in your final message)
- Per item: what you built, files touched, and the tests added (count before → after).
- For B: the source evidence (file:line, both versions) and the design you chose, with why.
- Anything you couldn't do, or decided differently, and why.
- Exact commands run and their results (tests, ruff). Branch name and the commit SHAs.
- Any dev-bench check you ran, and how you got the code onto it.
