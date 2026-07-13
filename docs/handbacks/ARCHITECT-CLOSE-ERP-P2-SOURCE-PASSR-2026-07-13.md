# Architect close — ERP OCR Pass-2 remediation source Pass R — 2026-07-13

## Outcome

All three bounded FIX-NOW units from ADR-0015 passed fresh independent exact-tip source review with
no findings. Their reviewed tips are integrated, with preserved ancestry, in the **untagged** combined
candidate `39b9562`. This is a source-gate close only: the Pass-2 release verdict remains **NO-GO** until
the sole serialized combined real-Frappe/browser runtime passes.

| Unit | Reviewed branch tip | Merge commit | Independent findings | Disposition |
|---|---|---|---|---|
| ERP-P2-1 DN → PO schedule date | `a9e7b5c` (implementation `463c3fe`) | `138aed6` | C/H/M/L 0/0/0/0 | Source PASS |
| ERP-P2-2 upload CSRF fail-closed | `91f8bbd` | `f6fbd88` | C/H/M/L 0/0/0/0 | Source PASS |
| ERP-P2-3 Statement `/accounts` queue | `7cdd314` (implementation `03f564b`) | `39b9562` | C/H/M/L 0/0/0/0 | Source/build PASS |

The three accepted branches changed disjoint path sets. The independent reviewer found no mechanical
combination conflict, scope/tool-boundary breach, weakened test, or unsupported runtime claim.

## Combined source evidence at `39b9562`

- focused DN + fleet contract tests: **94 passed**;
- full mocked Python suite: **866 passed**;
- Statement source assertions: **3 passed**;
- Ruff, format, compileall, and diff checks: clean;
- TypeScript/Vite production build: **49 modules transformed**;
- rebuilt Accounts dist: no tracked diff from the reviewed committed artifacts.

The unchanged ADR-0019 production audit remains accepted-for-release at 2 High + 3 Moderate; no
dependency, lockfile, version, changelog, tag, re-freeze, manifest, sibling-repository, or deploy change
is part of this candidate.

## Sole remaining serialized runtime

Run one isolated exact-candidate Frappe/ERPNext v15 harness only after portfolio allocation. It must:

1. prove reviewed and fallback DN dates reach one linked draft PO header and every included item,
   unmatched rows remain excluded, zero rates remain for operator review, and duplicate creation is
   denied;
2. prove the complete cookie/token CSRF HTTP matrix, including zero mutation/file/job side effects on
   denials and the existing driver-shell queue's normal Desk-token behavior;
3. browser-prove Statement discovery, counts, lists, statuses, fields, error/empty/loading/refresh,
   invalid-route bounce, and Desk drill-through without typing a direct URL; and
4. rerun the original Pass-2 regression seams, capture exact ref/install/migrate/worker identity, and
   clean up only the isolated records and harness created by the run.

No tag, re-freeze, release recommendation, or deployment is authorized until that runtime is accepted.
