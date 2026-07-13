# Handback — ERP-P2-3 supplier statement queue discovery — 2026-07-13

## Identity and refs

- **Unit:** ERP-P2-3 (Medium, FIX-NOW) — supplier statements absent from `/accounts`
- **Governing decision:** ADR-0018
- **Builder:** contained Codex builder fallback
- **Branch:** `codex/erp-p2-3-statement-queue`
- **Exact frozen base:** `a675018bb2d816401dda855a37b05e8ea1d143c8`
- **Exact implementation commit:** `03f564b0d22ed71ee0824bb820928e10afff5da0`
- **Final branch tip:** resolve with `git rev-parse origin/codex/erp-p2-3-statement-queue` after the
  handback-doc commit; it must be a direct descendant of the implementation commit.

```bash
git merge-base --is-ancestor a675018bb2d816401dda855a37b05e8ea1d143c8 \
  03f564b0d22ed71ee0824bb820928e10afff5da0
git show --stat 03f564b0d22ed71ee0824bb820928e10afff5da0
git rev-parse origin/codex/erp-p2-3-statement-queue
```

## Implementation

`OCR Statement` is now a fourth first-class card and list route in the existing read-only Accounts
SPA. The implementation:

- adds `/accounts/q/ocr-statement/<status>` routing and direct Frappe v15 form links at
  `/app/ocr-statement/<name>`;
- replaces the former global status assumption with per-doctype queue configuration;
- preserves the three existing Import/DN/Fleet buckets exactly as `Needs Review`, `Matched`,
  `Draft Created`, and `Error`;
- gives Statement its ADR-0018 buckets: `Pending`, `Extracting`, `Reconciled`, and `Error`;
- excludes terminal `Reviewed` from counts, list routing, and status navigation;
- reads only existing fields through the existing generic `useFrappeGetDocCount` and
  `useFrappeGetDocList` hooks: record name, matched supplier with OCR-name fallback, statement date,
  period, closing balance/currency, mismatch/missing/not-listed counts, status, and creation age;
- preserves existing count/list loading, permission-error, empty, refresh, invalid-route bounce, and
  direct Desk drill-through behavior; and
- rebuilds the exact committed dist under `erpocr_integration/public/accounts/` and
  `erpocr_integration/www/accounts.html`.

No new whitelisted method or write path was added.

## Files changed

1. `frontend/src/lib/doctypeMeta.tsx` — statement doctype/slug, per-doctype statuses, fields, columns,
   balance/issue rendering.
2. `frontend/src/components/QueueCard.tsx` — count cards now iterate the selected doctype's statuses.
3. `frontend/src/pages/QueueList.tsx` — per-doctype route validation and status navigation.
4. `frontend/tests/statement-queue-source.test.mjs` — deterministic source assertions for route,
   status split, Reviewed exclusion, fields, and Desk URL.
5. `frontend/package.json` — adds only `test:source`; dependency and version declarations unchanged.
6. `erpocr_integration/public/accounts/` — fresh content-addressed production JS/CSS and entry.
7. `erpocr_integration/www/accounts.html` — fresh matching SPA entry.

Implementation diff from frozen base: 11 files, 259 insertions, 99 deletions. The apparent bulk
delete/add is Vite's content-addressed JS/CSS replacement.

## Verification evidence

### Focused frontend assertions

```bash
cd frontend
npm run test:source
```

Result: **3 passed, 0 failed**. Assertions cover:

- `OCR Statement` slug/config and generic direct Desk form URL;
- exact Statement statuses, `Reviewed` exclusion, three unchanged legacy status assignments, and
  per-doctype route/navigation use; and
- required Statement list fields.

These are deterministic source assertions only. They are not rendered-browser or E5 evidence.

### TypeScript, production build, and dist freshness

```bash
cd frontend
npm ci
npm run build
```

Result: **PASS** — `tsc --noEmit` clean; Vite 6.4.2 transformed 49 modules and emitted:

- `assets/index-BRMuQcv1.css` — 14,758 bytes
- `assets/index-DzAhEJBq.js` — 367,560 bytes

A second clean `npm run build` produced byte-identical dist hashes:

- CSS: `2d0523087188959a14c66cc6467ec931abf2585814a40e7ab6f156a886fc6680`
- JS: `ede1cfd459824db80ac7d11decebcf032e5800f1c52daec6158f29105b65d270`
- both HTML entries: `4556d8de8f93e7fc1d07f4951ef0573bf4a8ded0a91adf59e33d1c998a39b60d`

### Full mocked provider-surface regression

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
  /home/willie/dev/fleet_management/.venv/bin/python -m pytest erpocr_integration/tests/ -q
```

Result: **858 passed in 2.29s**.

The first invocation used the venv's inherited temporary-directory configuration and failed in
pytest capture cleanup before collection (`FileNotFoundError`; 0 tests ran). The explicit Unix temp
environment above is the known WSL-safe invocation and passed the complete suite. No failure was
suppressed or excluded.

### Lint, format, diff, and dependencies

```bash
ruff check .
ruff format --check .
git diff --check a675018bb2d816401dda855a37b05e8ea1d143c8..03f564b0d22ed71ee0824bb820928e10afff5da0
cd frontend && npm audit --omit=dev
```

- Ruff: **all checks passed**.
- Format: **81 files already formatted**.
- Diff whitespace: **clean**.
- Production audit (informational under ADR-0019): **2 High, 3 Moderate**, unchanged expected chain
  (`form-data`; `ws` via `engine.io-client` / `socket.io-client` / `frappe-react-sdk`). The forced
  proposal would install regressive `frappe-react-sdk@1.3.11`; no audit fix was run.
- `frontend/package-lock.json`: **unchanged**; no dependency or dependency-version drift.

## Self-review and scope attestation

- Verified every requested Statement field against the existing `OCR Statement` schema.
- Verified the status vocabulary against the schema and ADR-0018; `Reviewed` occurs only in the
  explanatory terminal-state comment/test description, never in a queue array.
- Verified invalid routes are checked against the selected doctype, so Statement statuses cannot be
  used for Import/DN/Fleet and vice versa.
- Verified the existing generic count/list hooks remain the only data access and all row links use the
  Frappe v15 `/app/<slug>/<name>` form route.
- Verified existing Import/DN/Fleet field/column definitions and status values were not changed.
- Verified there is no backend, API, permission, DocType, hook, DN/PO, fleet upload, CSRF, dependency,
  lockfile, version, changelog, cross-app surface, sibling, or portfolio change.
- Did not use Docker, a bench, localhost, the shared full-stack harness, or production.
- Did not merge, tag, deploy, re-freeze, or make a business-policy decision.

## Open gates

- **Independent frontend Pass R:** architect/reviewer-owned; not claimed here.
- **Serialized runtime/browser gate:** still open. From `/accounts`, an authorized operator must reach
  a pending or reconciled Statement without typing a URL and browser-observe counts, list fields,
  permission error, empty/loading behavior, refresh, invalid-route bounce, and Desk drill-through.
- **Release integration:** merge/re-freeze/version/tag/deploy remain architect/operator-owned and are
  prohibited from this builder cycle.
- **ADR-0019 dependency follow-up:** unchanged and remains separate from ERP-P2-3.
