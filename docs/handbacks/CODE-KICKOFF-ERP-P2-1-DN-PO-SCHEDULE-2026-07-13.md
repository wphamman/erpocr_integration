# Code kickoff — ERP-P2-1 DN → PO required-by date — 2026-07-13

## Unit and boundary

- Finding: ERP-P2-1 High, FIX-NOW. Governing decision: ADR-0016.
- Work only in the assigned isolated branch/worktree. Do not touch CSRF, frontend, dependencies, version,
  changelog, surface docs, sibling/portfolio repos, Docker/full-stack harness, merge, tag, or deploy.

## Required implementation

In `OCRDeliveryNote.create_purchase_order`, derive one `schedule_date` as reviewed
`self.delivery_date` or site `frappe.utils.today()` when absent. Use it for Purchase Order header
`schedule_date`, every included PO item `schedule_date`, and the existing `transaction_date`. Preserve
all permission, status, document-type, row-lock, duplicate, matched-item, zero-rate draft, description,
warehouse, linkage, and skipped-item behavior. Do not use `ignore_mandatory` as the date mechanism.

## Evidence and handback

- Focused mocked tests: delivery date propagates to header and all included items; absent date uses a
  deterministic mocked today; unmatched items remain excluded; guards/linkage remain intact.
- Add real-Frappe test coverage if repository conventions permit without a full stack; otherwise specify
  the exact serialized runtime step and do not claim E4/E5.
- Run focused tests, full mocked suite, Ruff, format, compile, and diff check.
- Write `docs/handbacks/HANDBACK-ERP-P2-1-DN-PO-SCHEDULE-2026-07-13.md`, commit and push only the builder
  branch, with exact base/implementation/tip, commands/counts, scope attestation, and open Pass-R gate.
