# Handback — Grok 4.5 — P2A-L1 secret-safe Error Log — 2026-07-13

## Identity

- **Unit:** P2A-L1 (Low) — Drive `test_drive_connection` secret disclosure
- **Builder:** Grok 4.5 isolated session (Pass-R rework)
- **Prior builder tip (Pass-R CONCERNS base):** `1c47fcfaf43c309bf649a1a9d952279d5f0dbcf0`
- **Finding:** Fresh GPT Pass-R returned CONCERNS — client envelope was generic, but failure path still persisted `frappe.get_traceback()` verbatim to Error Log; the regression deliberately injected a synthetic secret and asserted that secret *reached* Error Log.
- **This rework:** fix only the second disclosure sink (Error Log message). Keep POST-only, System-Manager-only, settings validation, success behavior, and the exact generic client failure envelope.

## Branch / refs

| Ref | Value |
|---|---|
| **Branch** | `codex/fix-p2a-l1-secret-safe-error-log` |
| **Frozen / reviewed base** | `1c47fcfaf43c309bf649a1a9d952279d5f0dbcf0` |
| **Implementation (code) commit** | `c777f1a552b2decfcb69fa9c4dd651e3e9dcfb6c` |
| **Branch tip** | use `git rev-parse HEAD` on this branch (includes handback docs after the fix) |

Exact SHAs for review:

- base: `1c47fcfaf43c309bf649a1a9d952279d5f0dbcf0`
- implementation: `c777f1a552b2decfcb69fa9c4dd651e3e9dcfb6c`
- tip: `git rev-parse HEAD` (must be descendant of implementation)

```bash
git merge-base --is-ancestor 1c47fcfaf43c309bf649a1a9d952279d5f0dbcf0 HEAD && echo base-ancestor-ok
git merge-base --is-ancestor c777f1a552b2decfcb69fa9c4dd651e3e9dcfb6c HEAD && echo impl-ancestor-ok
git rev-parse HEAD
```

## Files changed (this rework)

1. `erpocr_integration/tasks/drive_integration.py` — `test_drive_connection()` failure path only:
   - no longer calls `frappe.get_traceback()`
   - logs stable diagnostics only: operation + exception **type** name
   - client message unchanged: `Connection failed. Check Error Log for details.`
2. `erpocr_integration/tests/test_failure_paths.py` — invert `TestDriveConnectionTest.test_exception_does_not_echo_secret_like_text`:
   - synthetic secret must be absent from **response** and **every** `log_error` arg/kwarg
   - still proves diagnostic `log_error` title/call occurs
   - asserts `get_traceback` is **not** called and logged message has no `Traceback`
3. `docs/handbacks/HANDBACK-GROK-DRIVE-CONNECTION-ERROR-2026-07-12.md` — this handback

**Not changed:** schema, version, hooks, CROSS_APP_SURFACE, authorization (`only_for("System Manager")`), POST whitelist, success path, settings validation, Drive scan/processing, unrelated tests, dependencies.

## Change detail

### Pass-R CONCERNS state (base `1c47fcf`)

```python
except Exception:
    frappe.log_error(title="Drive Connection Test Failed", message=frappe.get_traceback())
    return {
        "success": False,
        "message": "Connection failed. Check Error Log for details.",
    }
```

Client was safe; Error Log still received full traceback (exception strings can carry SA/API diagnostics).

### After (this rework)

```python
except Exception as e:
    frappe.log_error(
        title="Drive Connection Test Failed",
        message=(
            "Drive connection test failed.\n"
            "operation=test_drive_connection\n"
            f"exception_type={type(e).__name__}"
        ),
    )
    return {
        "success": False,
        "message": "Connection failed. Check Error Log for details.",
    }
```

Persisted fields are intentionally limited to:

- stable title (`Drive Connection Test Failed`) — correlation via Error Log list / document name
- stable operation id (`operation=test_drive_connection`)
- exception **type** only (`exception_type=HttpError` etc.)

**Not** logged: exception string (`str(e)`), traceback text, credentials, tokens, private keys, request payloads, provider response bodies.

## Test / lint evidence

Runtime: system Python 3.12.3 + user-site `pytest 9.1.1`, `ruff 0.15.21`, Pillow (decode-gate collection). Suite run with `TMPDIR=/tmp TMP=/tmp TEMP=/tmp`.

### Focused regression

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
  pytest erpocr_integration/tests/test_failure_paths.py::TestDriveConnectionTest -v
```

**Result: 3 passed**

| Test | Asserts |
|---|---|
| `test_exception_does_not_echo_secret_like_text` | Client generic envelope; secret absent from response **and** all `log_error` args/kwargs; title `Drive Connection Test Failed`; logged body has `operation=test_drive_connection` + `exception_type=Exception`; `get_traceback` not called; no `Traceback` in log message |
| `test_success_shape_unchanged` | Happy path still returns `success: True` with folder name/id; no error log |
| `test_endpoint_is_post_only` | Source still has `@frappe.whitelist(methods=["POST"])` immediately above `def test_drive_connection` |

### Full suite

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp pytest erpocr_integration/tests/ -q
```

**Result: 858 passed**

### Ruff / format / compile / static

```bash
ruff check .                 # All checks passed!
ruff format --check .        # 81 files already formatted
python3 -m compileall -q erpocr_integration   # exit 0
# static: test_drive_connection body has no get_traceback; has exception_type=; generic client message
```

## Scope attestation

- **In scope:** second disclosure sink on `test_drive_connection` failure path + inverted unit regression + this handback.
- **Out of scope / not done:** Docker, localhost, bench, sibling repos, Git credentials use, runtime/browser/live-Drive, merge, tag, deploy, portfolio closure, independent GPT review claim.
- **Authorization / contract:** POST-only, System Manager, settings gates, success shape, generic client failure message — preserved.
- **Cross-app flag:** none.

## Still-open gates (do not claim closed)

- **Serialized runtime / Phase-B / E4–E5:** still open. No live ERPNext, no real Drive service-account call, no desk/browser proof.
- **Independent review:** not claimed; standing architect commissions Pass-R / adjudication separately.
- **No push** performed from this session (local commit only per source-only ADR-015 sandbox).

## Self-review

- Client envelope unchanged and still secret-free.
- Error Log no longer a secret sink; diagnostics remain useful (operation + exception type + Error Log title/id).
- Regression inverted so a future re-introduction of traceback logging fails the suite.
- No schema/version/hooks/surface/auth/dependency drift.
