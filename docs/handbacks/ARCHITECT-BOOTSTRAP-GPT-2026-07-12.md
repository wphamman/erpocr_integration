# Architect bootstrap — permanent GPT architect baseline — 2026-07-12

## Purpose and role boundary

This is the durable handover from the unavailable Claude architect to the standing GPT architect
for `erpocr_integration`. It records the baseline reconstructed from repository, git, bus, and
portfolio evidence on 2026-07-12. On-disk evidence wins over earlier handback prose or injected
memory.

The standing architect owns plans, ADRs, open questions, builder kickoffs, handback adjudication,
merge/tag decisions, provider-surface reconciliation, routed flags, and deploy requests. The
architect does not implement substantive feature code and never deploys. Substantive builds go to
separate Grok terminal sessions; a fresh GPT reviewer independently verifies Grok-built changes.
Business-process and cross-app decisions remain Willie's routing/ratification boundary.

There is no `AGENTS.md` in this repository. The applicable repository guidance is `CLAUDE.md`, its
always-load files `docs/architecture.md` and `docs/implementation-patterns.md`, and the cross-app
Frappe invariants in `/home/willie/.claude/frappe-app-learnings.md`. The repository does not contain
a separate explicit git-authority statement. Durable prior closes show the app architect owns
architectural docs, merge, tag, release, and push decisions, while deployment remains Starktail/
Willie territory. This bootstrap/docs-only commit is additionally authorized by the initiating
task.

## Frozen release identity

- App/version source: `erpocr_integration/__init__.py` = `1.9.0`.
- Frozen ship tag: annotated `v1.9.0`.
- Tag object: `3eb24e48d2d34d0cf999641ec18d0f49a12dad38`.
- Peeled ship commit: `45f696a2b481a2773b994ffabcd82c4f14849204`.
- At bootstrap start, `HEAD`, local `master`, `origin/master`, and `v1.9.0^{commit}` all resolved to
  that peeled commit. The architect worktree was clean and detached; the primary checkout
  `/home/willie/dev/erpocr_integration` was clean on `master`.
- Rollback tag: lightweight `v1.6.0`, commit
  `ba21ec18e6d98bad69c5cb108a5a67e4698e1abd`.
- The current production baseline is recorded by the portfolio as v1.6.0. No live probe was made
  during this bootstrap. The intended change is therefore a real v1.6.0 -> v1.9.0 upgrade, with
  v1.6.0 as rollback.

The archived v1.9.0 code-close says the tag was on `b410672`; that sentence is stale. Git and the
portfolio manifest agree on the later peeled commit `45f696a`. The merge commit `ec4910a` remains
the v1.9.0 code/surface merge baseline, not the frozen ship commit. This bootstrap is post-tag,
architect-owned documentation; it does not move or replace the frozen `v1.9.0` ship ref.

## Verified release delta and repository health

The v1.6.0 -> v1.9.0 range is 40 commits and a real cumulative upgrade:

- v1.7.0 folds the read-only `/accounts` SPA into this app. Its built dist is committed; deploy
  needs no Node/npm step for this app.
- v1.8.0 adds the opt-in Fleet Card auto-record/bulk action, supplier-scoped aliases, and related
  hardening. It changes the semantics of `OCR Fleet Slip.expense_account` for Fleet Card slips to
  blank; the fleet flag was emitted, acknowledged as no-impact, and archived.
- v1.9.0 closes Q9/Q10/Q11: explicit tax-builder inputs, supplier-chain confidence capping, and a
  tax-inclusivity-aware totals-reconciliation gate for auto-draft.
- `patches.txt` is byte-identical at v1.6.0 and v1.9.0. Its two entries predate v1.6.0, so the
  upgrade adds no patch. Schema settlement still requires `bench migrate`.
- The hooks delta is the `/accounts` catch-all website route and `/apps` tile only; there is no
  doc-event, scheduler, or override delta in this release range.
- The upgrade changes no DocPerm array and no install permission code. The accepted Pass 1 clean-
  install evidence records three shipped roles, standard DocPerm grants, and zero Custom DocPerm.
- No credential file is tracked; `.env` is ignored.

Checks rerun in this worktree at the peeled ship commit:

```text
pytest erpocr_integration/tests/ -q                    855 passed
ruff check erpocr_integration/                         passed
ruff format --check erpocr_integration/                81 files already formatted
npm --prefix frontend ci && npm --prefix frontend run build
                                                        passed; committed dist remained clean
git diff --check                                       passed
```

The first pytest attempt failed before collection because inherited `TMP` pointed at a Windows
path inside WSL. Re-running with `TMPDIR=/tmp TMP=/tmp TEMP=/tmp` produced the valid 855-test run.
The npm audit reported three moderate and three high dependency advisories; no dependency update
was attempted because this task is a baseline reconstruction, not a feature/dependency build.

The whitelisted-source audit finds 33 methods and zero `allow_guest`. It also found one old
documentation mismatch: `test_drive_connection` has always been POST-only in code but was labeled
GET in `CROSS_APP_SURFACE.md`. This bootstrap commit corrects the table to POST; it is not a
runtime or cross-app contract change and needs no routed flag.

## Architecture records and handbacks

- `docs/architecture/DECISIONS.md` contains ADR-0001 through ADR-0014. All are Accepted/live or
  shipped; ADR-0001 through ADR-0010 are explicitly reconstructed historical records.
- `docs/architecture/OPEN-QUESTIONS.md` has one genuinely open item: Q4. Auto-draft firing is
  proven and Q11 closes the discovered discount-overdraft class, but a correct future
  high-confidence organic draft and the first post-configuration Cargo Compass customs case still
  need observation. Owner: Architect + Willie. Q1-Q3 and Q5-Q11 are resolved.
- Every repository handback has a corresponding accepted or bounced-then-accepted architect close
  in `/home/willie/dev/_bus-archive/`. The latest, v1.9.0 invoice-path handback, was independently
  accepted with 855 tests and a clean external GPT/Codex pass. There is no unreviewed app handback
  in `docs/handbacks/` at bootstrap time.
- The provider surface is current through v1.9.0 at merge baseline `ec4910a`; v1.9.0 itself has no
  external surface delta. The earlier v1.8.0 Fleet Card `expense_account` semantics flag is
  acknowledged and archived. No surface flag is owed now.

## Bus, deploy, and portfolio state

The `~/dev` bus scan found no unarchived app-specific inbound flag, kickoff, or handback for
`erpocr_integration`. Relevant archived items are the v1.7/v1.8/v1.9 code closes, the v1.9.0
go-live ref-freeze response, and the acknowledged fleet semantics flag. The unarchived broadcast
freeze/runbook request has already been actioned for this app via the archived response and the
go-live pack.

There is no active deploy authorization:

- `/home/willie/dev/deploy-request-portfolio-golive-2026-07-11.md` is explicitly HALTED/VOID and
  says to run no phase until Willie reissues it.
- `/home/willie/dev/golive-pack-erpocr_integration-2026-07-13.md` is an operator/runbook input,
  not authority to deploy.
- The app remains async-tolerant in the portfolio: production can continue daily use on v1.6.0
  while v1.9.0 review/deploy gates trail.

Current portfolio records, read without editing the sibling repository:

- `review/release-manifest-2026-07-11.md` row 4 freezes `v1.9.0` at peeled commit
  `45f696a2b481a2773b994ffabcd82c4f14849204`, install-order position 10, upgrade from v1.6.0, class
  `full`, and async-tolerant ship-set status.
- `review/coverage-ledger.md` records Pass 1 CLOSED/PASS at E3, with the two Info items accepted
  for release. Pass 2 is `not started`; Pass 3 is also outstanding. The authored Pass 2 unit is
  `/home/willie/dev/starpops_portfolio/review/unit-erpocr_integration-pass2.md`.
- Own Pass 2 must be executed by a separate fresh reviewer, not by this standing architect. It
  must drive the real accounts/OCR flows, `/accounts` E5 render, runtime socket-origin behavior,
  and the third-role seam. Later Pass 3 scope follows the portfolio review protocol.

## Standing baseline and next ownership

Resulting app baseline: frozen v1.9.0 / peeled `45f696a`; no queued builder work; no unreviewed
handback; no unacknowledged surface flag; no active deploy request. The app is not portfolio-GO:
Pass 1 is closed, while own Pass 2 and subsequent scoped review remain gates.

Next actions:

1. Portfolio coordinator/Willie dispatches `unit-erpocr_integration-pass2.md` to a separate fresh
   GPT reviewer when the review batch reaches this app.
2. The standing app architect holds for routed review findings, adjudicates them against code, and
   writes Grok builder kickoffs only if fixes are accepted.
3. Fresh GPT reviewers verify any Grok-built changes before the architect merges/re-freezes.
4. Willie/Starktail own any future reissued deploy authorization and execution. The architect may
   write or update the deploy request but will not deploy.
5. Architect + Willie observe Q4 when suitable organic volume or a Cargo Compass case appears.

Until one of those inputs arrives, hold the frozen release and do not implement feature code,
deploy, edit sibling repositories, run own Pass 2, or close portfolio findings.
