# Architect close — ERP OCR Pass-2 runtime GO and v1.10.0 release — 2026-07-13

## Verdict and release selection

The sole serialized combined runtime is accepted: **GO, C/H/M/L 0/0/0/0**. ERP-P2-1, ERP-P2-2,
ERP-P2-3, and the materially affected original Pass-2 seams passed on a fresh isolated Frappe 15.95 /
ERPNext 15.94 site. The 172-entry evidence checksum manifest independently verifies.

The correct next version is **v1.10.0**. The train is backward-compatible but adds a user-visible fourth
Accounts work queue (`OCR Statement`), so Semantic Versioning requires a minor rather than patch bump.
The release also contains the secret-safe Drive connection-test repair merged before the three bounded
Pass-2 units.

## Exact lineage

- previous frozen tag: `v1.9.0`, peeled `45f696a2b481a2773b994ffabcd82c4f14849204`;
- secret-safe implementation: `c777f1a552b2decfcb69fa9c4dd651e3e9dcfb6c`, accepted tip `e8b889d`,
  integration merge `b69f91c`;
- ERP-P2-1 reviewed tip `a9e7b5c` (implementation `463c3fe`), merge `138aed6`;
- ERP-P2-2 reviewed tip/implementation `91f8bbd`, merge `f6fbd88`;
- ERP-P2-3 reviewed tip `7cdd314` (implementation `03f564b`), merge/product candidate `39b9562`;
- runtime-tested architect-doc successor: `a3c41b9ce621ed6fc30b2a907a3fd0f1c38069a3`; its only delta
  from `39b9562` is the source Pass-R close and open-question transition.

The release-metadata successor changes version and documentation only. It carries the accepted runtime
because it does not change executable product, built assets, schema, hooks, patches, or dependencies.

## Accepted evidence

- Fresh independent exact-tip source Pass R: every bounded unit PASS, C/H/M/L 0/0/0/0.
- Combined source: focused Python 94, full Python 866, Statement source assertions 3, Ruff/format/
  compile/diff clean, Vite build clean, committed dist byte-identical.
- Combined runtime handback and checksums:
  `/home/willie/dev/_runtime/erpocr-p2-combined-runtime-20260713T050431Z/evidence/HANDBACK.md` and
  adjacent `SHA256SUMS` (172/172 verified).
- Separate secret-safe runtime:
  `/home/willie/dev/_runtime/erpocr-p2-secret-safe-20260713-0011/HANDBACK.md`.

Runtime limitations remain explicit: physical phone/camera, offline 3G, real Gemini success, real
Drive/Gmail ingestion, and production tenant behavior were **NOT ASSESSED**. Those limitations do not
invalidate the tested release seams. OAuth was not separately exercised; the validated API-token path
was exercised and the source uses the same Frappe `set_user` stamp for the exemption.

## Release boundary

- No new patch, schema, hook, role, fixture, required app, or mandatory setting.
- Accounts dist is committed; deploy requires no npm/Vite step.
- Cross-app provider delta: the existing `upload_fleet_slip` cookie-CSRF invariant. The provider flag
  was emitted and the real Driver Shell consumer passed the combined runtime.
- ADR-0019 remains accepted-for-release and Q14 remains open; production audit is unchanged at 2 High +
  3 Moderate with no observed socket request from Accounts.
- No deployment was performed. Willie and Starktail retain deployment authority.
