# Architect close — ERP OCR Pass 2 adjudication — 2026-07-13

## Identity and verdict

- Reviewed candidate: `b69f91c6863f4f5c3d1f9399eae6e8a9d42c8510`.
- Governing Pass-2 handback: external portfolio artifact
  `erpocr_integration-pass2-handback-2026-07-12.md`; accepted after checking its cited runtime evidence.
- Verdict: **NO-GO** — Critical 0, High 1, Medium 2, Low 1.
- This close records adjudication, not release closure. No version, tag, re-freeze, manifest, portfolio,
  deployment, or sibling-repository mutation occurred.

## Finding dispositions

| Finding | Severity | Disposition | Durable decision / unit |
|---|---:|---|---|
| ERP-P2-1 DN → PO lacks required-by date | High | **FIX-NOW** | ADR-0016; isolated backend business-flow unit |
| ERP-P2-2 first Website User write can bypass CSRF comparison | Medium | **FIX-NOW** | ADR-0017; isolated backend security unit + consumer flag |
| ERP-P2-3 statements absent from `/accounts` | Medium | **FIX-NOW** | ADR-0018; isolated frontend discovery unit |
| ERP-P2-4 dependency audit not green | Low | **ACCEPT-FOR-RELEASE** | ADR-0019; Q14 with explicit invalidation signals |

## Evidence-based rulings

- `OCR Delivery Note.delivery_date` is an existing reviewed Date field, extracted from the source and
  already used as PO `transaction_date`; it is the provenance source for PO/item schedule dates.
- The shell's pinned image-capture queue already sends `X-Frappe-CSRF-Token` when Desk boot exposes it.
  The provider must now reject cookie-authenticated uploads without an initialized matching token.
- `OCR Statement` has distinct statuses (`Pending`, `Extracting`, `Reconciled`, `Reviewed`, `Error`),
  requiring per-doctype SPA status configuration rather than adding it to the old global buckets.
- The dependency chain has no safe automatic fix today: latest SDK still pins the affected socket
  client, npm proposes a regressive SDK downgrade, and the shipped page has sockets disabled.

## Frozen work and remaining gates

Three code kickoffs in this directory are frozen from the architect-doc successor to `b69f91c`.
Grok is unavailable because xAI authentication is down; separate contained Codex builders are the
authorized fallback. Builders may run source-local tests/builds only and must not use full-stack Docker,
merge, tag, deploy, edit siblings, or close findings.

Every builder result requires fresh independent GPT review. Then a combined candidate requires serialized
Pass R on a disposable harness: exact ref/install/migrate/worker identity; DN → one linked draft PO with
header/item dates; first-request CSRF negative/mismatch/positive matrix with zero denied side effects;
statement discovery/count/list/drill-through without URL typing; full original Pass-2 regression; cleanup.
Only after those gates may the architect consider version/changelog, annotated patch tag, re-freeze flag,
and release recommendation. Deployment remains Willie/Starktail-owned.
