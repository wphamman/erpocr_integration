# Code kickoff — ERP-P2-3 statement queue discovery — 2026-07-13

## Unit and boundary

- Finding: ERP-P2-3 Medium, FIX-NOW. Governing decision: ADR-0018.
- Frontend-only discovery unit. Do not alter backend doctypes/APIs/permissions, DN/PO, fleet upload,
  dependencies, version/changelog, siblings/portfolio, Docker/full-stack harness, merge, tag, or deploy.

## Required implementation

Make `OCR Statement` a first-class `/accounts` card and queue with Desk drill-through. Replace the global
actionable-status assumption with per-doctype statuses: existing Import/DN/Fleet buckets remain unchanged;
Statement uses `Pending`, `Extracting`, `Reconciled`, `Error`, while `Reviewed` is terminal/excluded. Add
statement fields/columns sufficient to work the queue (name, supplier/fallback OCR supplier, statement date,
period, closing balance/currency, reconciliation mismatch/missing indicators, age), using only existing
generic `frappe.client` reads. Preserve permission-aware errors, empty/loading states, refresh, invalid-route
bounce, and direct form links. Rebuild and commit the exact dist under `public/accounts`/`www/accounts.html`.

## Evidence and handback

- Add focused frontend tests or deterministic source assertions for doctype routing, per-doctype statuses,
  Reviewed exclusion, statement fields, and desk URL; do not claim rendered E5 from source assertions.
- Run TypeScript check/build, committed-dist freshness/diff, full Python suite (shared provider-surface
  regression), Ruff/format, and `npm audit --omit=dev` as an unchanged informational gate.
- Runtime remains architect-owned: operator reaches a Reconciled statement from `/accounts` without URL
  typing; counts/list/drill-through and error/empty states are browser-observed.
- Write `docs/handbacks/HANDBACK-ERP-P2-3-STATEMENT-QUEUE-2026-07-13.md`, commit and push only the builder
  branch with exact base/implementation/tip and scope attestation.
