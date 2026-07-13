# Deploy request — erpocr_integration v1.10.0

**To:** Willie + Starktail

**From:** erpocr_integration architect

**Authority:** deploy request only; this document does not authorize or perform deployment

## Target and preflight

- Deploy annotated tag `v1.10.0`; verify it resolves to the peeled commit supplied by the owning
  architect's routed re-freeze flag. Do not deploy an untagged branch or infer the ref from patch state.
- Record the current app ref and
  `GET /api/method/frappe.utils.change_log.get_versions` response for each target site before changing
  anything. Star Pops was previously expected at v1.6.0; live verification wins.
- Take the hosting platform's normal site/database/files backup and verify the rollback ref is locally
  fetchable before the window.
- Preserve all `OCR Settings` values. Its Gemini and Drive credentials are encrypted Password fields;
  do not copy them between sites without the matching site `encryption_key`.

## Install/build/migrate/restart

1. Fetch the repository and check out exact tag `v1.10.0`; verify `git rev-parse v1.10.0^{commit}`
   equals the re-freeze flag's peeled commit.
2. Run the normal Python dependency/install step used by the Starktail image. This release adds no
   Python or Node dependency.
3. Run `bench --site <site> migrate` after all portfolio apps are present. There are no new erpocr
   patches or schema fields, but migrate remains required to settle hooks/assets and the optional
   feature-detected Fleet Vehicle custom fields.
4. Run the normal `bench build --app erpocr_integration`/portfolio build. The `/accounts` Vite dist is
   committed; do **not** run `npm install`, `npm audit fix`, or a deploy-time Vite build.
5. Restart/recreate the normal Frappe web, workers, scheduler, and websocket processes using Starktail's
   standard deployment choreography. Confirm all containers/processes are healthy with zero restart loop.

## Post-deploy verification

1. Version probe reports `erpocr_integration 1.10.0`; record the checked-out commit too.
2. `bench --site <site> migrate` completed without an unexpected erpocr patch or lost OCR Settings.
3. An authorized accounts user loads `/accounts`, sees the `OCR Statement` Pending/Extracting/
   Reconciled/Error card, opens a Statement queue, and drills into Desk.
4. A permitted operator creates a draft PO from a synthetic/reviewed OCR Delivery Note and verifies the
   header/item required-by dates; remove the synthetic records afterwards.
5. The deployed Driver Shell uploads one synthetic slip with its normal CSRF header and reaches Received;
   verify exactly one private File/slip and no duplicate retry artifact, then clean it up.
6. Existing OCR Import PI/PR/JE creation, scheduler state, and long-worker Retry remain healthy. Keep
   `enable_fleet_auto_record` OFF and do not change `enable_auto_draft` during the window.

## Rollback

- Roll back to the exact per-site pre-deploy ref recorded in preflight. If the target had already reached
  the prior freeze, that ref is `v1.9.0` (`45f696a2b481a2773b994ffabcd82c4f14849204`); if Star Pops is
  still at v1.6.0, use its verified pre-deploy commit instead of assuming v1.9.0.
- Re-run `bench --site <site> migrate`, normal build, and process restart at the rollback ref. The
  v1.9.0→v1.10.0 delta has no patch/schema/dependency change, so no destructive data reversal is needed;
  new OCR records are ordinary app documents and remain valid.
- If only `/accounts` fails, operators can use the existing Desk DocType lists while Willie/Starktail
  decide whether to roll back. If extraction fails, stop duplicate OCR creation and use the documented
  manual-accounting fallback until the ref is restored.

## Completion echo

Return to the app and portfolio architects: site, previous ref, deployed tag object and peeled commit,
version probe, migrate/build/restart result, post-deploy checks, cleanup, and any rollback performed.
