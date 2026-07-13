# Code kickoff — ERP-P2-2 fleet upload CSRF fail-closed — 2026-07-13

## Unit and boundary

- Finding: ERP-P2-2 Medium, FIX-NOW. Governing decision: ADR-0017.
- Security-only provider unit. Do not edit the consumer shell/sibling, DN/PO, Accounts SPA, dependencies,
  version/changelog, merge/tag/deploy, or use a full-stack harness.

## Required implementation

Add a small testable guard at the very start of `upload_fleet_slip` that verifies an initialized server
session CSRF token and a constant-time-equivalent matching `X-Frappe-CSRF-Token` request header for
cookie-authenticated requests, using Frappe v15's real token/header conventions and normal
`CSRFTokenError`. Missing server token, missing header, or mismatch must fail before permissions,
idempotency lookups, file reads, document creation, rollback, or enqueue. Do not mint/accept a token inside
the mutation. Preserve POST-only, existing roles/create-permission, multipart signature, response shape,
idempotency and owner scope. Update this app's provider `CROSS_APP_SURFACE.md` with the explicit invariant;
do not emit/edit the sibling flag from the builder.

## Evidence and handback

- Verify the real Frappe v15 implementation path/API before coding; do not rely on the wholesale mock for
  framework function existence.
- Focused tests must cover missing session token, missing header, wrong header, correct header, and prove
  every denied path has zero permission/file/document/enqueue side effects. Preserve existing upload tests.
- Run focused tests, full mocked suite, Ruff, format, compile, whitelist/surface audit, and diff check.
- Runtime remains architect-owned: newly logged-in Website User first POST denied; initialized matching
  token succeeds; System User remains correct.
- Write `docs/handbacks/HANDBACK-ERP-P2-2-CSRF-2026-07-13.md`, commit and push only the builder branch.
