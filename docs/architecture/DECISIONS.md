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
- **Status:** Accepted · **shipped v1.7.0** (merge `762301d`, 2026-07-09)
- **Decision:** The `starpops_accounts` React SPA (read-only accounting-ops dashboard, Mint-pattern)
  will be folded **into** this app: `frontend/` at repo root, SPA-serving Python (`website_route_rules`
  + `add_to_apps_screen` + permission gating) into the OCR namespace, Vite base
  `/assets/erpocr_integration/accounts/`. One `bench get-app`, one version, one CHANGELOG.
- **Why:** It reads OCR data exclusively and shares this app's users/permissions; a separate app means
  a second install, version, and deploy for a UI that is a view onto this app's data. Portfolio
  persona-shell / canonical-datamodel-home rules favor folding a pure consumer view into its data owner.
- **Rejected:** Keep `starpops_accounts` a standalone Frappe app — extra install/version/deploy surface
  for no isolation benefit (it holds no datamodel of its own).
- **Consequence:** originally implied a Starktail image-build **Node step** — **superseded by ADR-0011** (commit the built dist; no deploy-time build), which removes that cross-team dependency.
- **Pointer:** worktree `../erpocr-foldin` (branch `feature/starpops-accounts-foldin`). See [[project_starpops_accounts_mvp]]. **Execution/rebase state → OPEN-QUESTIONS Q5.**

## ADR-0011 — Commit the SPA's built dist; no deploy-time build step
- **Status:** Accepted 2026-07-09 · **shipped v1.7.0** (merge `762301d`); dist committed under `public/accounts/`, verified fresh at merge
- **Decision:** The folded-in `starpops_accounts` SPA ships its **built assets committed to git** (`public/accounts/*.js+*.css` + `www/accounts.html`, excluding the ~1.76MB sourcemap; ~380KB hashed assets per release). The app installs + serves the SPA with **zero Node at deploy** — no `npm run build` step in Starktail's image.
- **Why:** (a) matches how `fleet_management` already ships its dashboard to the **same Starktail host**; (b) preserves the app's stated goal of installing on **self-hosted AND Frappe Cloud** — Frappe Cloud won't run a custom Vite build, so a deploy-time build would break `/accounts` there; (c) eliminates a cross-team dependency (a Starktail image-build change) and its `/accounts`-404 failure mode.
- **Rejected:** gitignore the dist + add `cd frontend && npm ci && npm run build` to Starktail's image (the fold-in kickoff's original assumption, inherited from the standalone-app era) — more operational surface, a new failure mode, and breaks Frappe-Cloud installability. **Supersedes ADR-0010's Node-step consequence.**
- **Caveat (the one footgun of committing build output):** the release process must rebuild + re-commit the dist so the committed assets never drift from `frontend/src`. Enforce in the release checklist / a build script.
- **Pointer:** OPEN-QUESTIONS Q5; builder handback (pending). Ratified by Willie 2026-07-09. See [[project_starpops_accounts_mvp]].

## ADR-0012 — Fleet Card auto-record: opt-in, confidence-gated, plus a bulk manual action
- **Status:** Accepted (Willie's ruling 2026-07-09) · shipped v1.8.0 (merge `acdddc9`)
- **Decision:** Two levers for the Mark-Recorded backlog (34+ slips at the 2026-07-06 review):
  (1) **`OCR Settings.enable_fleet_auto_record`** (default OFF, ADR-0002 philosophy) — a Fleet Card
  slip that lands/updates to `Matched` with **high confidence** auto-completes via the existing
  `mark_recorded()` path, triggered from `OCRFleetSlip.on_update` only. High confidence =
  confirmed vehicle (`Auto Matched`/`Confirmed`, never fuzzy `Suggested`) + recon payload present
  (Fuel: litres+total; Toll: total); slip_type `Other` NEVER auto-records (Willie, in-build).
  Audit: `auto_recorded` + `auto_record_skipped_reason` (skip-reason group-by = the diagnostic).
  The completing call is savepointed — a post-save failure rolls back the status write, so the
  audit trail can never contradict the persisted status. (2) **`fleet_api.bulk_mark_recorded`** —
  a list action (≤200 rows, synchronous) that server-revalidates EVERY row (strict `Matched`,
  Fleet Card, per-doc perms via `mark_recorded()`) with per-row savepoints.
- **Why:** the per-slip click was pure toil on verified slips, and the monthly fleet-card invoice
  reconciliation in `fleet_management` remains the downstream safety net; a slip is a control
  record, not a financial document (ADR-0003).
- **Rejected:** always-on auto-record (violates ADR-0002's opt-in automation posture); a new
  terminal status (ADR-0003's reuse rule); background-queued bulk (accepted synchronous at ≤200 —
  revisit trigger: a prod timeout on a bulk selection).
- **Invariant preserved:** `purchase_invoice` stays NULL on every auto/bulk path; Direct Expense
  slips are untouched; with the setting off, behavior is regression-tested identical to v1.7.0.
- **Pointer:** `tasks/auto_record.py`; OPEN-QUESTIONS Q8 (resolved); CHANGELOG 1.8.0.

## ADR-0013 — Item aliases: supplier-scoped tier over the global tier; hash-named rows
- **Status:** Accepted (Willie ruled Q7c IN, 2026-07-09) · shipped v1.8.0 (merge `acdddc9`)
- **Decision:** `OCR Item Alias` gains an optional `supplier` Link; matching order becomes
  **Item Supplier lookup → supplier-scoped alias → global alias → exact → service mapping →
  fuzzy → default_item**. Learning writes the supplier-scoped row when the parent supplier is
  known; all pre-v1.8.0 rows stay global (blank supplier) and keep working as the fallback tier —
  no destructive migration. Enabler: autoname `field:ocr_text` → `hash` and the unique index
  dropped (same text may exist per supplier), so **every read/write is filter-based with
  `order_by="modified desc, name asc"`** — rows are never addressed by document name.
- **Why:** globally-keyed aliases let one supplier's confirmation silently rewrite the mapping
  every other supplier relies on (cross-supplier description collisions). Supplier scoping makes
  corrections local; the DN pipeline joined the same semantics (its old name-based existence
  check would have inserted unbounded duplicates under hash naming).
- **Rejected:** keeping the unique index with a composite key (breaks legacy rows / needs
  migration); waiting for a collision to bite (Willie ruled include-now, 2026-07-09).
- **Accepted costs:** duplicate rows possible under a benign check-then-insert race (reads and
  corrections deterministically target the most-recently-modified row; periodic cleanup is a
  future nit); up to two queries per line on a scoped miss.
- **Pointer:** `tasks/matching.py`, `ocr_item_alias.json`; OPEN-QUESTIONS Q7 (resolved); CLAUDE.md gotcha.

## ADR-0014 — Auto-draft gates on totals reconciliation (tax-inclusivity-aware)
- **Status:** Accepted 2026-07-10 · shipped v1.9.0 (merge `ec4910a`)
- **Decision:** Auto-draft (never manual creation) skips when **Σ(qty×rate) across extracted
  lines** deviates from the invoice's stated amount beyond **max(1%, R1.00)** (module constants,
  not a setting), bidirectionally. **The reference is tax-inclusivity-aware** — decided by the
  existing `_detect_tax_inclusive_rates`: exclusive rates reconcile against `subtotal` (falling
  back to `total − tax` when subtotal is 0/absent); **inclusive rates reconcile against
  `total_amount`**. Degenerate/unverifiable cases (no positive line sum, no usable reference)
  **fail open** — the other confidence gates and human review still stand.
- **Why:** the first organic auto-draft (Q4 probe, 2026-07-10: `OCR-IMP-01918` → `ACC-PINV-2026-00446`,
  Cactus) **overdrafted R132.76** — a 5% invoice discount lived in the extracted subtotal but not
  the line rates, and PI amounts build from qty×rate; the Gemini schema has no discount field, so
  every globally-discounted invoice would systematically overdraft. The gate converts the whole
  class into "parks for review with an amount-naming skip reason."
- **The load-bearing subtlety (pin this):** comparing line sums against the tax-exclusive
  subtotal **unconditionally** false-fails every legitimately tax-INCLUSIVE invoice by the full
  tax amount — silently disabling auto-draft for that entire class while the mocked suite stays
  green (the first cut had exactly this bug; three /code-review finders converged on it, fixed
  pre-handback). The gate's arithmetic must also mirror the PI builder's defaults exactly
  (`qty or 1`, `rate or 0`) — verified both sides at the merge gate.
- **Rejected:** extracting discounts via a Gemini schema change (bigger design; the gate makes
  the class safe first — revisit if discounted-invoice volume makes the skip pile annoying);
  a tolerance setting in OCR Settings (no evidence a knob is needed); fail-closed on
  unverifiable data (would silently disable auto-draft for sparse extractions).
- **Pointer:** `tasks/auto_draft.py` `_totals_reconcile`; OPEN-QUESTIONS Q11 (resolved), Q4 (evidence); CHANGELOG 1.9.0.

## ADR-0015 — Pass 2 is NO-GO; repair three bounded release blockers before re-freeze
- **Status:** Accepted 2026-07-13 · repair train completed and shipped v1.10.0 after runtime GO
- **Decision:** The full-flow Pass-2 handback against integrated candidate `b69f91c` is accepted as
  **NO-GO**: ERP-P2-1 High (Delivery Note → Purchase Order cannot create a draft), ERP-P2-2 Medium
  (first Website User upload can bypass CSRF comparison), and ERP-P2-3 Medium (supplier statements
  are absent from the advertised complete Accounts queue) are all FIX-NOW. They are isolated into
  separate backend-business, backend-security, and frontend-discovery units. No version bump, tag,
  re-freeze, or deploy may occur until each unit passes independent GPT review and the combined
  candidate passes serialized runtime/Pass R.
- **Why:** The review proved most flows at E4/E5 but found one core path dead, one write without an
  explicit anti-CSRF invariant, and one accounting queue reachable only by URL/Desk knowledge.
  Portfolio ADR-017 explicitly favors complete, discoverable workflows with no URL typing.
- **Rejected:** releasing with the High; fixing only the High while accepting hidden statement work;
  combining all three into one opaque implementation/review delta.
- **Pointer:** `docs/handbacks/ARCHITECT-CLOSE-ERP-P2-PASS2-2026-07-13.md`; OPEN-QUESTIONS Q12-Q14.

## ADR-0016 — OCR Delivery Note PO schedule uses the reviewed delivery date, else today
- **Status:** Accepted 2026-07-13 under portfolio ADR-017 · shipped v1.10.0 (`463c3fe`, merge `138aed6`)
- **Decision:** `OCR Delivery Note.create_purchase_order()` must set both Purchase Order header and
  every included item `schedule_date` to `OCR Delivery Note.delivery_date` when present. If the
  reviewed source has no delivery date, use site `today()` and leave the draft for operator review.
  Keep the existing PO `transaction_date` rule aligned to the same value and preserve all duplicate,
  status, document-type, permission, matched-item, and linkage guards.
- **Why:** `delivery_date` already exists in the app-owned schema, is extracted from the source,
  rendered for review, and already supplies the PO transaction date. Reusing it is the narrowest
  reversible rule that preserves document intent and satisfies ERPNext v15's required-by invariant.
  The fallback is explicit, deterministic, and non-financial: the output remains a zero-rate draft
  requiring review. It does not promise payment, submit stock, or create a binding supplier order.
- **Rejected:** a new setting/lead-time policy (larger and unsupported); leaving the action broken;
  `ignore_mandatory` as a substitute for a valid schedule date; silently inventing a future lead time.
- **Assumption for Willie to review:** when a historical delivery date is the only source date, it is
  better provenance than an invented future date for this retrospective draft-PO workflow. Willie may
  later replace the fallback with a configured procurement lead time without schema migration.
- **Pointer:** ERP-P2-1; `ocr_delivery_note.py:create_purchase_order`.

## ADR-0017 — Fleet-slip cookie writes fail closed unless the session CSRF token matches
- **Status:** Accepted 2026-07-13 · shipped v1.10.0 (`91f8bbd`, merge `f6fbd88`); consumer runtime PASS
- **Decision:** `upload_fleet_slip` must enforce an initialized, matching Frappe CSRF token before any
  permission, idempotency, file, or database work for cookie-authenticated requests. A missing server
  token, missing header, or mismatch is denied with the normal CSRF error class. Do not mint a token
  inside the mutation and then accept the same headerless request. Preserve POST-only, Driver/create
  authorization, multipart shape, idempotency, ownership, and recon-only semantics.
- **Why:** Pass 2 proved Frappe v15 can accept the first Website User mutation when its session token
  has not yet been initialized. The driver shell's shared capture queue already sends
  `X-Frappe-CSRF-Token` from Desk boot state when available, so fail-closed enforcement matches the
  intended System-User shell path. This clarifies a consumed write contract and requires a routed flag
  plus a real newly-logged-in negative/positive HTTP regression.
- **Rejected:** relying only on SameSite/file-input friction; weakening cookie policy; accepting the
  first mutation to initialize state; changing to API-key auth inside this bounded repair.
- **Pointer:** ERP-P2-2; `fleet_api.upload_fleet_slip`; OPEN-QUESTIONS Q12.

## ADR-0018 — Supplier statements are first-class `/accounts` work queues
- **Status:** Accepted 2026-07-13 under portfolio ADR-017 · shipped v1.10.0 (`03f564b`, merge `39b9562`)
- **Decision:** Add `OCR Statement` to `/accounts` with its own actionable states, counts, list fields,
  error/empty/loading behavior, and direct Desk drill-through. Statement actionable states are
  `Pending`, `Extracting`, `Reconciled`, and `Error`; `Reviewed` is terminal and excluded from
  outstanding work. Refactor the SPA's current global status array into per-doctype configuration so
  statement semantics do not distort Import/DN/Fleet queues. Rebuild and commit the dist.
- **Why:** The landing page claims the accounting pipeline and is the daily work surface. Pass 2 proved
  statement reconciliation itself works, but operators needed direct URL knowledge to find it. A
  callable backend plus hidden Desk route is incomplete under portfolio ADR-017.
- **Rejected:** changing the page copy to disclaim statements; a bare link without counts/list/error
  states; treating `Reviewed` as outstanding.
- **Pointer:** ERP-P2-3; `frontend/src/lib/doctypeMeta.tsx`; OPEN-QUESTIONS Q13.

## ADR-0019 — Dependency advisories accepted for this release with explicit invalidation signals
- **Status:** Accepted-for-release 2026-07-13 · carried into v1.10.0; non-blocking follow-up Q14
- **Decision:** Do not fold dependency churn into the three Pass-2 repairs. The 2026-07-13 production
  audit reports 2 High and 3 Moderate transitive advisories. The latest published
  `frappe-react-sdk` 1.17.0 still pins vulnerable `socket.io-client` 4.7.1; npm's automatic proposal
  is a regressive SDK change to 1.3.11, while overrides would replace pinned transitive networking
  packages. The shipped Accounts page explicitly disables Socket.IO and Pass 2 observed no socket
  request from it. Track a dedicated dependency unit with rebuild, byte-diff, auth/data/browser, and
  disabled-socket regression coverage.
- **Invalidation signals:** make this a release blocker if a Critical advisory appears; an advisory is
  demonstrated reachable in the shipped browser/server path; the Accounts SPA enables Socket.IO; a
  supported SDK release resolves the chain without regression; or policy requires a clean production
  audit. The `form-data` chain must also be re-audited because it is Node-oriented transitive code and
  may be removable/upgradable independently.
- **Rejected:** npm's SDK downgrade; unreviewed `overrides`; broad dependency upgrades inside a
  release-blocker repair train; claiming the advisories are harmless rather than currently unexploited.
- **Pointer:** ERP-P2-4; OPEN-QUESTIONS Q14.
