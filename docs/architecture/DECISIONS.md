# Architecture Decision Register — erpocr_integration

> **What this is:** append-only ADRs — *why* we chose X over Y, the record nothing else keeps.
> `CLAUDE.md` is *what the app is now*; `CHANGELOG.md` is *what shipped in each release*; an ADR
> is *why we chose this design over the alternative*, which outlives any single release.
> The operating model (federation, flag discipline, review gates) lives in the `architect-session`
> skill — not restated here. Sibling contract surface is in [../../CROSS_APP_SURFACE.md](../../CROSS_APP_SURFACE.md).
>
> **Format per entry:** decision · why · rejected · status · pointer.
> **Bootstrap note (2026-07-09):** this register was reconstructed on the first architect session
> from `CHANGELOG.md`, `CLAUDE.md`, the handbacks, and per-session memory. Every entry below is
> marked **[Reconstructed]** — dates are the originating release/decision, not the authoring date.
> DRAFT until ratified.

---

## ADR-0001 — Build as a Frappe custom app, not standalone middleware  **[Reconstructed]**
- **Status:** Accepted · live (foundational)
- **Decision:** Ship as a single `bench get-app`-installable Frappe v15 custom app with all config
  stored in the **OCR Settings** single DocType, not as a separate FastAPI/Flask service beside ERPNext.
- **Why:** One install, UI-configurable, works on self-hosted **and** Frappe Cloud; no second
  service to deploy, auth, or secure; documents are created through ERPNext's own ORM/permissions.
- **Rejected:** A standalone OCR middleware service calling ERPNext over REST — more infra, a
  second deploy target, duplicated auth, and no native DocType/permission integration.
- **Pointer:** [../architecture.md](../architecture.md) → *Architecture › Frappe Custom App*.

## ADR-0002 — Review-first: no auto-creation by default; auto-draft is opt-in  **[Reconstructed]**
- **Status:** Accepted · live
- **Decision:** Documents (PI/PR/JE/PO) are created only by **explicit user action**. High-confidence
  auto-drafting exists but is gated behind `OCR Settings.enable_auto_draft`, **off by default**.
- **Why:** The system's value is *reducing data entry while keeping a human in the loop* — extract
  and suggest, user reviews and commits. Silent auto-creation would post wrong drafts from OCR
  misreads into the ledger.
- **Rejected:** Auto-create every high-confidence extraction — rejected as too much trust in Gemini
  output against real accounting records; kept as an explicit opt-in for users who want it.
- **Pointer:** [../implementation-patterns.md](../implementation-patterns.md) → *Auto-Draft*; `tasks/auto_draft.py`.

## ADR-0003 — Fleet Card slips are NULL-PI control records (`Completed ≠ purchase_invoice IS NOT NULL`)  **[Reconstructed]**
- **Status:** Accepted · live (v1.2.0)
- **Decision:** A slip with `posting_mode = "Fleet Card"` closes as a **control record** with
  `purchase_invoice` NULL — it captures litres/odometer/vehicle/date for cross-check, and reaches
  `Completed` via the operator-clicked **Mark Recorded** button. The provider's *monthly* fleet-card
  invoice (booked in `fleet_management`) carries the cost. Only `posting_mode = "Direct Expense"`
  slips ever produce a PI. Reuses the existing `Completed`/`Matched` status enum rather than adding
  a new terminal status.
- **Why:** Booking a PI per slip **and** the monthly card invoice would double-count the expense.
  Overloading the accepted status enum saved a cross-app deploy for consumers that read the field.
- **Rejected:** (a) Always create a PI per fleet slip — double-books. (b) Add a new terminal status
  for card slips — forces every consumer (`fleet_management`, Fleet Dashboard) to redeploy.
- **Consequence (load-bearing):** any code querying fleet slips for downstream linkage must check
  **both** `posting_mode` and `purchase_invoice`, not status alone. Server-side PI guard
  (`posting_mode != "Direct Expense"` raises) enforces the invariant against API/stale-client bypass.
- **Pointer:** [../architecture.md](../architecture.md) → *OCR Fleet Slip Workflow*; CLAUDE.md gotcha. See [[feedback_cross_app_reuse_status_over_new]].

## ADR-0004 — Optional-app coupling via conditional Custom Field, never a doctype-JSON Link  **[Reconstructed]**
- **Status:** Accepted · live (foundational cross-app rule)
- **Decision:** Any `Link → <doctype owned by an optional sibling app>` (e.g. `OCR Import.fleet_vehicle`
  → Fleet Vehicle) is installed as a **conditional Custom Field** via `install.setup_optional_custom_fields()`,
  gated on the target doctype existing — never declared in the doctype's own JSON, never shipped as a
  Custom Field *fixture* parented on an optional-app doctype.
- **Why:** A JSON Link (or an unconditional fixture) to a doctype from an app that isn't installed
  **breaks meta resolution** on standalone sites — the app fails to install/migrate. Feature-detected
  install keeps `erpocr_integration` working standalone and install-order-independent (no `required_apps`).
- **Rejected:** Declaring the Link in doctype JSON (simpler authoring) — breaks every site without
  `fleet_management`. Shipping via `fixtures/custom_field.json` — same failure (fixed in v1.5.0, review O1).
- **Pointer:** [../../CROSS_APP_SURFACE.md](../../CROSS_APP_SURFACE.md) §4; `install.py`. See [[feedback_optional_app_link_as_custom_field]].

## ADR-0005 — Driver-shell fleet-slip upload contract: recon-only, fail-safe, idempotent (P4)  **[Reconstructed]**
- **Status:** Accepted · live (v1.4.0, 2026-06-12)
- **Decision:** A new POST endpoint `fleet_api.upload_fleet_slip` lands a phone-captured slip as an
  **OCR Fleet Slip recon record** (image attached, Gemini queued async). It is: **recon-only**
  (structurally incapable of creating a PI or OCR Import — `purchase_invoice` stays NULL);
  **fail-safe** (a vehicle with no `custom_fleet_card_provider` lands in Needs Review with blank
  `posting_mode`/supplier — never silently routed to the invoice path); **idempotent** (client UUID
  `client_request_id` under a DB nullable-unique constraint, insert-and-catch + full rollback → 3G
  retry returns the original with `duplicate: true`). Reuses the existing `source_type` Data field
  (`"Gemini Shell Upload"` constant) as the Drive-vs-shell discriminator — no new field.
- **Why:** Drivers capture slips on a phone with flaky signal; the recon record model already existed
  (ADR-0003) but ingestion was Drive-folder-only. Idempotency makes an offline-queue drain safe; the
  fail-safe fork must not depend on a data field (`custom_fleet_card_provider`) being perfectly maintained.
- **Rejected:** (a) Reuse `api.upload_pdf` — it makes invoices, wrong shape. (b) Check-then-insert
  idempotency — races under REPEATABLE-READ. (c) A new `source_type`-style field — the existing Data
  field already discriminates. Mirrors `fleet_management.submit_vehicle_inspection` (P3) verbatim.
- **Pointer:** [../../CROSS_APP_SURFACE.md](../../CROSS_APP_SURFACE.md) §2c; `docs/SHELL_INTEGRATION_REPORT.md`. See [[project_p4_fleet_slip_shell_contract]].

## ADR-0006 — `upload_fleet_slip` permission is endpoint-scoped (accepts plain `Driver`), not a doctype-perm row (D0)  **[Reconstructed]**
- **Status:** Accepted · live (v1.6.0; decision D0 2026-07-06, released 2026-07-08)
- **Decision:** The endpoint's gate passes on **`OCR Fleet Slip` create OR `"Driver" in frappe.get_roles()`**
  — an in-code, possession-based check, mirroring `fleet_management.submit_vehicle_inspection`. The
  purpose-built `OCR Fleet Driver` role is demoted to a belt-and-braces runbook grant.
- **Why:** Real drivers hold only the **portfolio-wide `Driver`** persona role, so every shell slip
  403'd unless `OCR Fleet Driver` was granted per-user (hit live twice). Keeping the check
  **endpoint-scoped rather than a doctype-perm row** means: Desk posture is unchanged (a Driver
  System User still can't create slips in Desk), **no migrate**, and — critically — the known prod
  **Custom-DocPerm shadow** on OCR Fleet Slip (see OPEN-QUESTIONS Q2) **cannot render it inert**.
- **Rejected:** (a) Grant `OCR Fleet Driver` per driver at deploy — brittle, easy to forget, the twice-hit
  failure. (b) Add a `Driver` doctype-perm row — would be masked by the prod DocPerm shadow and change
  Desk posture. **Do NOT "fix" driver 403s by granting roles or adding a Driver DocPerm row** — the
  in-code check is the contract.
- **Pointer:** `fleet_api.py:501`; [../../CROSS_APP_SURFACE.md](../../CROSS_APP_SURFACE.md) §2c/§5; CLAUDE.md gotcha. See [[project_p4_fleet_slip_shell_contract]].

## ADR-0007 — Idempotent replay is owner-scoped  **[Reconstructed]**
- **Status:** Accepted · live (v1.6.0, 2026-07-08)
- **Decision:** The `client_request_id` replay path returns the duplicate envelope (slip name +
  status) **only to the slip's owner**; any other authenticated caller presenting the key gets a
  `PermissionError`.
- **Why:** A legitimate replay is always same-user — the shell generates the UUID per capture on one
  device. Returning another user's slip name/status to any caller who guesses/replays the key is an
  info leak on the widened (plain-`Driver`) surface.
- **Rejected:** Unscoped replay (the pre-v1.6.0 behavior) — leaks across users once the surface accepts
  any `Driver`. **Cross-app note:** the same gap existed in `fleet_management.submit_vehicle_inspection`
  — flagged and **fixed there** (fleet `791c211`/`32d5721`, released v0.18.0). No erpocr-side action owed.
- **Pointer:** `CHANGELOG.md` v1.6.0 › Security; [../../CROSS_APP_SURFACE.md](../../CROSS_APP_SURFACE.md) §2c.

## ADR-0008 — Customs/import VAT via ratio-test template selection + Actual-row injection  **[Reconstructed]**
- **Status:** Accepted · live but **inert until configured** (v1.5.0, 2026-07-06)
- **Decision:** New `OCR Settings.import_tax_template`. When set, template *selection* runs a ratio
  test — extracted VAT far from the standard percentage of subtotal (customs brokers bill import VAT
  as a fixed amount) selects the Actual-type import template over the percentage default; then
  `_build_taxes_from_template` **injects the extracted `tax_amount` into the template's first Actual row**.
  Unset → behavior unchanged.
- **Why:** Customs/freight invoices (e.g. Cargo Compass on the Cactus instance) carry import VAT as an
  Actual amount 7.5×–111% of subtotal; the old percentage template posted 15%×net (e.g. R216 instead
  of R10,912), re-keyed by hand on every invoice. A generic ratio-test detector avoids a hard-coded
  per-supplier list.
- **Rejected:** A per-supplier "customs-broker profile" (the first-cut design) — rejected in favor of
  the generic ratio-test fallback so it isn't limited to a configured supplier list. Injecting into
  mixed (non-pure-Actual) templates — caused a double-tax; injection scoped to pure-Actual templates.
- **Pointer:** `docs/reviews/REVIEW-LIVE-erpocr_integration-2026-07-06.md` (V1); `_select_tax_template`, `_build_taxes_from_template`. See [[project_vat_customs_broker_fix]]. **Config dependency → OPEN-QUESTIONS Q3.**

## ADR-0009 — Verify framework functions against a real bench; the wholesale `frappe` mock cannot  **[Reconstructed]**
- **Status:** Accepted · standing testing/verification posture (lessons v1.4.1, v1.5.1)
- **Decision:** Unfamiliar framework functions are verified on a **real bench** before use; ERPNext
  functions are imported from `erpnext.*` (e.g. `get_fiscal_year` from `erpnext.accounts.utils`, **not**
  `frappe.utils`) behind a **separate `ImportError` path that fails open**, and the relevant module is
  registered in `conftest` so tests hit the real import location. Tests must feed **real** values (e.g.
  a genuine tz-aware datetime), not echo/naive mocks.
- **Why:** The test suite mocks `frappe` wholesale. A `MagicMock` attribute **never raises**, so a call
  to a **nonexistent** function passes every test and `AttributeError`s only on prod — and inside a
  blanket `except` it masquerades as a domain failure. This shipped twice: v1.4.1 (tz-aware `captured_at`
  rejected by MariaDB at `insert()`) and v1.5.1 (`frappe.utils.get_fiscal_year` doesn't exist →
  **every** auto-draft silently skipped as "outside any active Fiscal Year").
- **Rejected:** Trusting the mocked suite as sufficient coverage for framework-boundary calls — it
  structurally cannot catch missing/misplaced framework functions.
- **Pointer:** CLAUDE.md *Must-know gotchas*; [../implementation-patterns.md](../implementation-patterns.md) → *Auto-Draft*. See [[project_erpocr_phase78_verification]].

## ADR-0010 — Fold `starpops_accounts` into `erpocr_integration` as one app/install  **[Reconstructed]**
- **Status:** Accepted (decision made) · **NOT yet executed** — gated on Danell UAT
- **Decision:** The `starpops_accounts` React SPA (read-only accounting-ops dashboard, Mint-pattern)
  will be folded **into** this app: `frontend/` at repo root, SPA-serving Python (`website_route_rules`
  + `add_to_apps_screen` + permission gating) into the OCR namespace, Vite base
  `/assets/erpocr_integration/accounts/`. One `bench get-app`, one version, one CHANGELOG.
- **Why:** It reads OCR data exclusively and shares this app's users/permissions; a separate app means
  a second install, version, and deploy for a UI that is a view onto this app's data. Portfolio
  persona-shell / canonical-datamodel-home rules favor folding a pure consumer view into its data owner.
- **Rejected:** Keep `starpops_accounts` a standalone Frappe app — extra install/version/deploy surface
  for no isolation benefit (it holds no datamodel of its own).
- **Consequence:** Starktail's image build needs a **Node step** added — must be flagged in the fold-in PR.
- **Pointer:** worktree `../erpocr-foldin` (branch `feature/starpops-accounts-foldin`). See [[project_starpops_accounts_mvp]]. **Execution/rebase state → OPEN-QUESTIONS Q5.**
