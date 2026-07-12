# Handback — Grok 4.5 — P2A-L1 Drive connection error handling — 2026-07-12

## Identity

- **Unit:** P2A-L1 (Low) — raw upstream exception text in `test_drive_connection`
- **Builder:** Grok 4.5 isolated session
- **Kickoff:** [CODE-KICKOFF-GROK-DRIVE-CONNECTION-ERROR-2026-07-12.md](CODE-KICKOFF-GROK-DRIVE-CONNECTION-ERROR-2026-07-12.md)
- **Finding source:** [REVIEW-PASS2-PHASE-A-GPT-2026-07-12.md](REVIEW-PASS2-PHASE-A-GPT-2026-07-12.md)

## Branch / refs

- **Branch:** `fix/p2a-l1-drive-connection-error`
- **Frozen base:** v1.9.0 peeled commit `45f696a2b481a2773b994ffabcd82c4f14849204`
- **HEAD:** 17166d29c68df01335bf0b1a4f1cef3a04142c17
- **Worktree:** `/home/willie/.codex/worktrees/grok-p2a-l1/erpocr_integration`

## Files changed

1. `erpocr_integration/tasks/drive_integration.py` — `test_drive_connection()` failure path only:
   - logs server-side traceback via `frappe.log_error(title="Drive Connection Test Failed", message=frappe.get_traceback())`
   - returns stable generic message: `Connection failed. Check Error Log for details.`
   - no longer interpolates the caught exception string into the client response
2. `erpocr_integration/tests/test_failure_paths.py` — new `TestDriveConnectionTest` (3 tests)
3. `docs/handbacks/HANDBACK-GROK-DRIVE-CONNECTION-ERROR-2026-07-12.md` — this handback

**Not changed:** Drive scan/processing, retries, schemas, migrations, roles, frontend, CROSS_APP_SURFACE, version, changelog. Endpoint path, POST method, System Manager gate, success return shape, and settings/credential behavior preserved.

## Change detail

### Before (failure path)

```python
except Exception as e:
    return {"success": False, "message": f"Connection failed: {e!s}"}
```

### After (failure path)

```python
except Exception:
    frappe.log_error(title="Drive Connection Test Failed", message=frappe.get_traceback())
    return {
        "success": False,
        "message": "Connection failed. Check Error Log for details.",
    }
```

## Test / lint evidence

Runtime: `/home/willie/dev/OCRIntegration/.venv-review` (pytest 9.0.3, ruff 0.15.12, Pillow installed for decode-gate collection).

Environmental note: suite run with `TMPDIR=/tmp TMP=/tmp TEMP=/tmp` (safe default; no WSL Windows-path TMP observed in this Linux environment, but set per kickoff guidance).

### Focused regression

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
  pytest erpocr_integration/tests/test_failure_paths.py::TestDriveConnectionTest -v
```

**Result: 3 passed**

Assertions covered:

| Test | Asserts |
|---|---|
| `test_exception_does_not_echo_secret_like_text` | Client `success is False`; synthetic secret `sa-private-key-SYNTHETIC_SECRET_DO_NOT_ECHO_xyz789` absent from `message`; stable generic message exact match; `frappe.log_error` called once with title `Drive Connection Test Failed` and traceback message |
| `test_success_shape_unchanged` | Happy path still returns `success: True` with folder name/id; no error log |
| `test_endpoint_is_post_only` | Module source still has `@frappe.whitelist(methods=["POST"])` immediately above `def test_drive_connection` (conftest whitelist mock strips runtime attrs) |

### Full suite

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp pytest erpocr_integration/tests/ -q
```

**Result: 858 passed** (frozen baseline was 855 + 3 new tests).

### Ruff

```bash
ruff check .
ruff format --check .
```

**Result: All checks passed / 81 files already formatted.**

## Self-review

- **Scope:** only the failure return of `test_drive_connection` + focused tests + this handback. No Drive poll/processing edits.
- **Credential disclosure:** client response cannot contain exception text; secret-like synthetic string asserted absent. Server-side Error Log still receives the traceback (intentional for System Manager diagnosis).
- **Contract:** no CROSS_APP_SURFACE change; endpoint name/method/role/success shape unchanged.
- **Cross-app flag:** none — internal hardening only.
- **No accidental edits** outside the three files listed above.

## Skipped / unresolved (out of builder scope)

- **No shared Docker / full-stack harness** was started, stopped, rebuilt, or used.
- **No sibling repositories** were opened or modified.
- **No merge, tag, deploy, or re-freeze** was performed.
- **E4/E5 runtime / Phase-B gates** remain open for the architect/coordinator; this handback claims **no** browser or live-Drive connection proof. Static/mocked unit coverage only.

## Push status

Builder branch pushed to `origin/fix/p2a-l1-drive-connection-error` only.

- **Builder tip (pre-push):** 17166d29c68df01335bf0b1a4f1cef3a04142c17
- Standing architect commissions fresh GPT review before adjudication/merge.
