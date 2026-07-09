# ERPNext OCR Integration (erpocr_integration)

Frappe v15 custom app: Gemini 2.5 Flash extracts structured data from PDFs/images and creates
ERPNext draft documents. Four pipelines — invoices (OCR Import → PI/PR/JE), delivery notes
(OCR Delivery Note → PO/PR), fleet slips (OCR Fleet Slip → PI, or a Fleet-Card control record
with no PI), and statement reconciliation (OCR Statement → match lines vs submitted PIs).
Ingest via manual upload, email forwarding, Google Drive folder polling, plus a driver-shell
phone-upload contract for fleet slips (`upload_fleet_slip`, P4); opt-in auto-draft for
high-confidence matches. ~$0.0001/doc. Installs via `bench get-app`; all config lives in
the OCR Settings single DocType. Currently v1.7.0.

## Knowledge (always loaded)
@docs/architecture.md
@docs/implementation-patterns.md

## Reference (load on demand)
- Cross-app API + integration contract (R3): [CROSS_APP_SURFACE.md](CROSS_APP_SURFACE.md)
- Architecture decisions + open questions (ADRs · *why we chose X*): [docs/architecture/DECISIONS.md](docs/architecture/DECISIONS.md), [docs/architecture/OPEN-QUESTIONS.md](docs/architecture/OPEN-QUESTIONS.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- End-user usage: `OCR_Quick_Start_Guide.md`, `OCR_User_Guide.md`, `OCR_Delivery_Note_Guide.md`, `OCR_Fleet_Slip_Guide.md`, `OCR_Statement_Guide.md`
- Fleet Dashboard data contract: [FLEET_DASHBOARD_DATA_SPEC.md](FLEET_DASHBOARD_DATA_SPEC.md)

## Durable Frappe rules (cross-app, user scope)
At session start, read `/home/willie/.claude/frappe-app-learnings.md` — cross-app Frappe
invariants. Reference it; do not duplicate its rules here.

## Relevant skills
- **frappe-pre-deploy-gates** — submit smoke + permission audit before tagging a release
- **frappe-agent-architect** — multi-app boundaries (the fleet_management integration, planned starpops_accounts fold-in)
- **frappe-react-spa** — the starpops_accounts dashboard slated to fold into this app
- **cloudflare-bot-fight-blocks-spa** — "403 everywhere after login" on the Cloudflare-proxied desk
- **frappe-impl-scheduler** — the hourly email poll + 15-min Drive polls (scan / DN / fleet)
- **frappe-impl-integrations** — Gemini API + Google Drive service-account integration
- **frappe-core-permissions** / **frappe-errors-permissions** — DocPerm shadows, role gating
- **frappe-impl-controllers** / **frappe-impl-clientscripts** — doctype controllers + desk JS
- **frappe-testing-unit** — the test suite (`pytest erpocr_integration/tests/`, frappe fully mocked)
- **ai-handoff** — package a Codex / second-opinion handoff (per the brief-driven workflow)

## Must-know gotchas (code-level)
- **Background jobs:** `frappe.set_user("Administrator")` INSIDE the enqueued fn (`enqueue` ignores `user=`); `frappe.db.commit()` required (with `# nosemgrep`).
- **No auto-creation by default:** documents are created by explicit user action; opt-in `enable_auto_draft` (off) auto-drafts high-confidence matches only.
- **v1.2.0 fleet invariant:** `Completed` ≠ `purchase_invoice IS NOT NULL`. Fleet Card slips close with NULL PI by design (cost lives in the provider's monthly invoice) — query `posting_mode` too, not just status.
- **Retry clears stale links:** retry endpoints reset supplier/vehicle/item links + child tables; changing supplier/PO cascades stale-field clearing.
- **Device `captured_at` is tz-aware → normalize before insert** (v1.4.1): the driver shell sends `captured_at` as UTC `new Date().toISOString()` (`…Z`); `get_datetime()` returns a tz-AWARE value that MariaDB's naive DATETIME column rejects with error 1292 — and the failure fires at `insert()`, OUTSIDE the parse try/except. `upload_fleet_slip` converts to site tz then strips `tzinfo`/microseconds (mirrors `fleet_management` P3.5). Any future shell-fed datetime needs the same. Tests must feed a REAL tz-aware value — an echo/naive mock for `get_datetime` HIDES this bug.
- **The wholesale frappe mock hides missing/misplaced framework functions** (v1.5.1): a MagicMock attribute never raises, so calling a NONEXISTENT function (e.g. `frappe.utils.get_fiscal_year` — real one: `erpnext.accounts.utils.get_fiscal_year`) passes every test and AttributeErrors on prod; inside a blanket `except` it masquerades as a domain failure (blocked ALL auto-drafts as "outside any active Fiscal Year"). Verify unfamiliar framework functions on a real bench (`docker exec starpops-test-backend-1 bash -lc 'cd /home/frappe/frappe-bench && ./env/bin/python -c ...'`); import ERPNext functions from `erpnext.*` with a separate ImportError path, and register the module in conftest so tests hit the real import location.
- **Rate-limit stagger lives at the pollers** (`time.sleep(5)` between enqueues), not the processors.
- **Link → optional-app doctype:** use a conditional Custom Field, never a doctype-JSON Link (breaks meta resolution on sites without that app). See [CROSS_APP_SURFACE.md §4](CROSS_APP_SURFACE.md).
- **`upload_fleet_slip` permission posture is endpoint-scoped** (v1.6.0, D0): the gate passes on `OCR Fleet Slip` create OR the plain `Driver` role (possession-based, mirrors fleet's `submit_vehicle_inspection`). Do NOT "fix" driver 403s by granting roles or adding a Driver doctype-perm row — the in-code check is the contract, and it stays immune to the prod Custom-DocPerm shadow on OCR Fleet Slip.

## Data & credential hygiene (standing rules, 2026-06-10)
- **Never touch live** includes read-only API calls. If verification genuinely needs prod data and Willie authorizes it in-session: read-only scoped credentials only, and record the authorized deviation explicitly in the handback.
- **No real personal data** (names, IDs, pay/cost figures, contact details) in any committed artefact — screenshots, fixtures, test data, docs. Synthesize or redact before committing. POPIA applies.
- **Credentials:** per-project `.env` (gitignored — verify, don't assume), least-privilege key per consumer (dedicated ERPNext service user, not the master prod key). Never copy a shared prod credential into a new project's `.env`; reference the canonical secrets file instead.
