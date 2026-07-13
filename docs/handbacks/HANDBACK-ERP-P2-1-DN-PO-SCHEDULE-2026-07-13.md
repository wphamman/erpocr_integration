# Handback — ERP-P2-1 DN → PO schedule-date repair — 2026-07-13

## Identity

- **Unit:** ERP-P2-1 (High, FIX-NOW)
- **Decision:** ADR-0016
- **Builder:** contained Codex builder fallback
- **Branch:** `codex/erp-p2-1-dn-po-schedule`
- **Frozen base:** `a675018bb2d816401dda855a37b05e8ea1d143c8`
- **Implementation commit:** `463c3fe76b081dab9aae421d661b34d1c5dbae98`
- **Branch tip:** resolve with `git rev-parse HEAD` after this handback commit; it must be a descendant of the implementation commit.

```bash
git merge-base --is-ancestor a675018bb2d816401dda855a37b05e8ea1d143c8 HEAD
git merge-base --is-ancestor 463c3fe76b081dab9aae421d661b34d1c5dbae98 HEAD
git rev-parse HEAD
```

## Files changed

1. `erpocr_integration/erpnext_ocr/doctype/ocr_delivery_note/ocr_delivery_note.py`
   - derives one `schedule_date` from reviewed `delivery_date`, falling back to site `frappe.utils.today()`
   - uses that value for Purchase Order `transaction_date`, header `schedule_date`, and every included item `schedule_date`
2. `erpocr_integration/tests/test_dn_controller.py`
   - proves reviewed-date propagation to the header and all included items
   - proves one deterministic mocked-today fallback when the reviewed date is absent
   - proves unmatched rows stay excluded and included rows remain zero-rate drafts
3. `docs/handbacks/HANDBACK-ERP-P2-1-DN-PO-SCHEDULE-2026-07-13.md`

No schema, hooks, frontend, dependencies, version, changelog, CSRF, or cross-app surface files changed.

## Implementation

`OCRDeliveryNote.create_purchase_order()` now derives the required-by date once, after the existing permission/status/document-type/row-lock/supplier guards and before building included items:

```python
schedule_date = self.delivery_date or frappe.utils.today()
```

That single value is placed on the PO header and each matched item. Existing behavior remains intact: unmatched items are skipped, zero rates are retained, warehouse and descriptions are retained, scan copy and source linkage are unchanged, and the resulting PO remains a draft for operator review. `ignore_mandatory` was not used as the date mechanism; the pre-existing draft flag is unchanged.

## Evidence

Environment: `/home/willie/dev/OCRIntegration/.venv-review`, Python 3.14.6, pytest 9.0.3, Ruff 0.15.12. `TMPDIR=/tmp TMP=/tmp TEMP=/tmp` was set for pytest.

### Focused mocked gate

```bash
python -m pytest -q erpocr_integration/tests/test_dn_controller.py
```

Result: **45 passed**.

### Full mocked suite

```bash
python -m pytest -q erpocr_integration/tests
```

Result: **860 passed**.

### Static gates

```bash
ruff check .
ruff format --check .
python -m compileall -q erpocr_integration
git diff --check
```

Results: Ruff passed; **81 files already formatted**; compileall exited 0; diff check exited 0.

## Guard and scope attestation

- Preserved source-document write and Purchase Order create permission checks.
- Preserved allowed source statuses and exact `Purchase Order` document-type check.
- Preserved the `for_update=True` duplicate/linkage lock across PO and PR outputs.
- Preserved supplier and matched-item requirements.
- Preserved unmatched-item exclusion, zero rates, OCR descriptions, warehouse selection, scan copy, source linkage, status update, and operator message behavior.
- Did not use Docker, localhost, a bench, the serialized full-stack harness, sibling/portfolio repos, or live data.
- Did not merge, tag, deploy, change version/changelog/dependencies/surface/CSRF/frontend, or claim E4/E5 or independent review.

## Open gates

1. **Independent Pass R:** review exact diff `a675018bb2d816401dda855a37b05e8ea1d143c8..463c3fe76b081dab9aae421d661b34d1c5dbae98` plus this handback.
2. **Serialized real-Frappe runtime gate:** on an isolated Frappe/ERPNext v15 site at the accepted candidate, as a permitted non-Administrator operator:
   - create/review an OCR Delivery Note with a known `delivery_date`, two matched items, and one unmatched item; invoke `create_purchase_order`; assert one draft PO is linked, header `transaction_date` and `schedule_date` equal the reviewed date, both included PO items have that date, the unmatched row is absent, and included rates remain zero;
   - repeat with no `delivery_date` while pinning/recording the site's current date; assert the header and every included item use that same site date;
   - re-invoke against the linked source and confirm the existing duplicate guard denies a second document.
3. **Combined-candidate Pass R/runtime:** remains portfolio-coordinator work after all isolated ERP-P2 repair units are accepted.

## Self-review

- Exact implementation diff contains only the controller and focused mocked tests.
- The fallback date is evaluated once, preventing header/item drift across a date boundary.
- Required-by dates are set before `po.insert()`, so ERPNext v15 validation receives complete header and child values.
- No unrelated behavior or owned surface changed.
