# ERPNext OCR Integration (erpocr_integration)

Frappe v15 custom app: Gemini 2.5 Flash extracts structured data from PDFs/images and creates
ERPNext draft documents. Four pipelines — invoices (OCR Import → PI/PR/JE), delivery notes
(OCR Delivery Note → PO/PR), fleet slips (OCR Fleet Slip → PI, or a Fleet-Card control record
with no PI), and statement reconciliation (OCR Statement → match lines vs submitted PIs).
Ingest via manual upload, email forwarding, and Google Drive folder polling; opt-in auto-draft
for high-confidence matches. ~$0.0001/doc. Installs via `bench get-app`; all config lives in
the OCR Settings single DocType. Currently v1.2.0.

## Knowledge (always loaded)
@docs/architecture.md
@docs/implementation-patterns.md

## Reference (load on demand)
- Cross-app API + integration contract (R3): [CROSS_APP_SURFACE.md](CROSS_APP_SURFACE.md)
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
- **Rate-limit stagger lives at the pollers** (`time.sleep(5)` between enqueues), not the processors.
- **Link → optional-app doctype:** use a conditional Custom Field, never a doctype-JSON Link (breaks meta resolution on sites without that app). See [CROSS_APP_SURFACE.md §4](CROSS_APP_SURFACE.md).
