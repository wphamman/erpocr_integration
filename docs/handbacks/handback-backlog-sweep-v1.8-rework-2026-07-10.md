# Re-handback (delta) — v1.8.0 backlog sweep, bounce rework — 2026-07-10

> Delta only; full context in `handback-backlog-sweep-v1.8-2026-07-10.md`.
> Responds to the architect close: "CHANGES REQUESTED — three confirmed correctness gaps."

---

## 1. Branch and commits

- **Branch:** `feature/backlog-sweep-v1.8` — **pushed**, tip `fd10833` (one rework commit on top of `adc6a5a`). Working tree clean. No merge/tag/deploy (still yours).

## 2. Rework items — all three done as specified

1. **`tasks/auto_record.py` — savepoint around `mark_recorded()`.** `frappe.db.savepoint("fleet_auto_record")` before the try; the except does `rollback(save_point=...)` FIRST (reverting the inner save's persisted `status="Completed"`), then clears messages, reverts the **in-memory** status too (`prior_status` captured before the try — the outer save's response serializes the doc, so DB and form now agree), then writes the audit fields. Test: `test_failure_after_status_write_rolls_back_to_savepoint` — failure after the status mutation → rollback to the named savepoint, `doc.status == "Matched"`, `auto_recorded == 0`, skip reason recorded.
2. **`tasks/matching.py` tier-1 scoped read — `order_by="modified desc, name asc"` added**, matching the global tier and the correction path. `db.get_value`'s `order_by` kwarg **bench-verified** on driver-dev before use (ADR-0009). Test: `test_scoped_read_orders_by_modified_desc` asserts the kwarg on the call (the ordering itself is the DB's; the mocked suite pins the contract).
3. **`create_purchase_invoice` — no-account guard.** Both `self.expense_account` and `OCR Settings.fleet_expense_account` empty → `frappe.throw` at create time: *"No expense account available for this Purchase Invoice. Set Fleet Expense Account in OCR Settings, or set an expense account on this slip."* Tests: `test_blocks_when_no_expense_account_anywhere` (throws, message matched, no draft built) + `test_expense_account_falls_back_to_settings` (fallback path intact).

## 3. Gates

- **Suite: 824 pass, 0 fail** (was 820; 4 tests added — all failure-path, exactly the coverage class the bounce identified). **ruff check + format clean.**
- **CHANGELOG:** the 1.8.0 Q6 bullet now states the flip-path fallback AND the create-time throw (its "Direct Expense behavior is unchanged" wording was invalidated by rework 3 — corrected to "Direct Expense *matching* behavior is unchanged" with the follow-through spelled out).
- No bench re-walk, per the close (happy paths untouched).

## 4. Open questions

None new. The §5 items from the original handback stand as you recorded them (tax_proxy refactor and Suggested-supplier tiers → OPEN-QUESTIONS; bulk stays synchronous; fleet-architect flag on `expense_account` is yours at merge).

---

**End of re-handback.** Ready for your merge pass.
