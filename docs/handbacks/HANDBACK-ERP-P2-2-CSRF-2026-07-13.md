# Handback — ERP-P2-2 fleet upload CSRF fail-closed — 2026-07-13

## Outcome

ERP-P2-2 is implemented on the isolated builder branch and is ready for independent source review.
Cookie-authenticated `upload_fleet_slip` calls now fail closed unless the server session already has a
CSRF token and the standard Frappe request header matches it. The guard is the first executable line in
the endpoint, before permission, idempotency, file, document, transaction, or enqueue work.

This is source-level and mocked-test evidence only. I did not run or claim the architect-owned real HTTP
runtime gate.

## Identity and boundary

- Repository: `/home/willie/dev/_worktrees/erpocr-p2-2-csrf`
- Branch: `codex/erp-p2-2-csrf`
- Exact base: `a675018bb2d816401dda855a37b05e8ea1d143c8`
- Governing decision: ADR-0017
- Kickoff: `docs/handbacks/CODE-KICKOFF-ERP-P2-2-CSRF-2026-07-13.md`
- No Docker, full-stack harness, localhost, consumer/sibling/portfolio edit, bus flag, merge, tag,
  deploy, version/changelog, schema, frontend, dependency, or business-policy change.

## Authoritative Frappe v15 verification

Verified against the official `frappe/frappe` `version-15` branch at
`105b17938839f4e5c6cdff817d42afc40c3bcc32` before implementation:

- `frappe/auth.py::HTTPRequest.validate_csrf_token` reads
  `frappe.session.data.csrf_token`, reads `X-Frappe-CSRF-Token`, sets
  `frappe.flags.disable_traceback`, and raises `frappe.CSRFTokenError` with `Invalid Request`.
- That framework guard returns early when the saved session token is absent. This is the first Website
  User mutation gap closed here.
- `frappe/sessions.py::get_csrf_token` generates a token when absent. The mutation deliberately does not
  call it: an uninitialized session must be denied, not initialized and accepted inside the write.
- `frappe/__init__.py::set_user` sets `local.session.sid = username` and clears session data. Frappe's
  validated API-key/OAuth paths use `set_user`, so the endpoint exempts a request only when it has both
  an Authorization header and the verified `session.sid == session.user` stamp. A bogus Authorization
  header on an ordinary cookie session cannot bypass CSRF.

Official source:

- <https://github.com/frappe/frappe/blob/105b17938839f4e5c6cdff817d42afc40c3bcc32/frappe/auth.py>
- <https://github.com/frappe/frappe/blob/105b17938839f4e5c6cdff817d42afc40c3bcc32/frappe/sessions.py>
- <https://github.com/frappe/frappe/blob/105b17938839f4e5c6cdff817d42afc40c3bcc32/frappe/__init__.py>

## Implementation

### `erpocr_integration/fleet_api.py`

- Added `_enforce_upload_csrf()` and invoked it at the start of `upload_fleet_slip`.
- Cookie auth requires both an initialized `frappe.session.data.csrf_token` and matching
  `X-Frappe-CSRF-Token`.
- Matching uses `hmac.compare_digest`.
- Denial uses Frappe's normal `CSRFTokenError`, `Invalid Request`, and traceback-suppression convention.
- Validated Authorization clients retain their no-CSRF-header behavior.
- POST-only decoration, arguments, permissions, file rules, response shape, idempotency, owner scope,
  atomic insert/File/job unit, and recon-only behavior are unchanged.

### Tests

`erpocr_integration/tests/test_fleet_upload_contract.py` adds explicit coverage for:

1. missing server session token;
2. missing request header;
3. mismatched request header;
4. matching token and constant-time comparator use;
5. validated token-auth compatibility without cookie CSRF state;
6. bogus Authorization header cannot bypass a cookie session guard.

Every denied path asserts zero calls at the permission, role, settings, vehicle lookup, document build,
rollback, commit, enqueue, file seek/tell, and file read seams.

`erpocr_integration/tests/conftest.py` now models the real Frappe CSRF exception and supplies an explicit
cookie-session token/header default so all pre-existing upload-contract tests continue through their
original paths.

### Provider surface

`CROSS_APP_SURFACE.md` records the new ADR-0017 invariant on the existing §2c write. No endpoint was
added, renamed, or removed; the whitelisted method count remains 33 and `allow_guest` remains zero.
The architect still owes the provider flag because the directly consuming `driver_ui_shell` is outside
this builder's authority.

## Verification

All valid test runs used `TMPDIR=/tmp TMP=/tmp TEMP=/tmp` to avoid the known WSL temporary-directory
failure mode.

```text
Focused:
  pytest -q erpocr_integration/tests/test_fleet_upload_contract.py
  49 passed in 0.22s

Full mocked suite:
  pytest -q erpocr_integration/tests/
  864 passed in 2.21s

Static:
  ruff check .
  All checks passed!

  ruff format --check .
  81 files already formatted

  python3 -m compileall -q erpocr_integration
  exit 0

Whitelist/source audit:
  33 whitelisted methods; 0 allow_guest
  upload_fleet_slip methods=['POST']

Diff hygiene:
  git diff --check
  exit 0
```

One initial `pytest` attempt used an unavailable bare command and ran nothing; a second attempt inherited
an invalid temporary capture path and also ran nothing. Neither is counted as evidence. The two runs
reported above used the known-good review virtualenv and explicit Unix temporary directories.

## Self-review

- The guard is endpoint-specific, not a global Frappe behavior change.
- It does not accept form-dict `csrf_token`; ADR-0017 requires the consumed shell's explicit standard
  header, and the provider contract now says so.
- Authorization-header presence alone is insufficient for exemption; the Frappe `set_user` session
  stamp is also required.
- No token is generated, read from a consumer, logged, persisted, or returned.
- The deny branch performs only request-header/session reads, sets the standard traceback flag, and
  throws the standard exception.
- No existing upload test was weakened or removed; the full mocked suite grew and remains green.

## Open gates (architect/reviewer owned)

1. Independent GPT source Pass R on the committed builder ref.
2. Serialized real HTTP proof on an isolated harness:
   - newly logged-in Website User, first headerless POST denied before mutation;
   - wrong token denied;
   - initialized matching token accepted;
   - normal System User shell flow accepted;
   - API-token client behavior preserved;
   - no slip, File, duplicate envelope, rollback artifact, or queued job on denials.
3. Emit and route the provider flag to `driver_ui_shell`; verify its shared capture queue sends the
   normal Desk token path.
4. Combined candidate review/re-freeze remains architect-owned. No merge, tag, or deploy is authorized
   by this handback.
