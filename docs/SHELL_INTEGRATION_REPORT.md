# Shell Integration Report — erpocr_integration ↔ driver_ui_shell

> **Scope:** Fleet Slip Scanning for the driver shell (Phase 4). Read-only investigation,
> 2026-06-11. No code was changed. Evidence is cited by `file:line`, doctype name, or live
> prod query. Where a fact could not be verified it is marked **unverified**.
>
> **Sites checked:** Live = **prod `erp.starpops.co.za`** (runs `erpocr_integration 1.2.0`,
> confirmed via `frappe.utils.change_log.get_versions`). The dev mirror **`driver-dev.local`
> on `starpops-test-backend-1` was NOT reachable** from this session, so all "live" facts below
> are from prod (the stated prod mirror). Checkout = this repo at `master` (v1.2.0).
>
> **Dependency rule respected:** this report proposes shell → erpocr_integration contracts only.
> erpocr_integration must not, and does not, reference the shell. All proposals keep the upload
> surface generic (a fleet-slip upload API), not shell-aware.

---

## 1. What this app is

Frappe v15 app that uses **Gemini 2.5 Flash** to extract structured data from PDFs/images and
stage them as review records in ERPNext. Four pipelines (invoices, delivery notes, **fleet
slips**, statement reconciliation). Fleet slips are the relevant one.

### Fleet slip pipeline — purpose
A driver's fuel/toll/other slip is scanned, Gemini extracts it, and it lands as an **OCR Fleet
Slip** record tied to a vehicle. In `posting_mode = "Fleet Card"` it is a **control / recon
record that creates NO accounting document** — the cost is booked by the fleet-card provider's
monthly invoice elsewhere. This is exactly the "backup + recon, not an invoice" model the shell
wants; **it already exists.**

### Main doctype — `OCR Fleet Slip`
Source: [`ocr_fleet_slip.json`](../erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.json),
controller [`ocr_fleet_slip.py`](../erpocr_integration/erpnext_ocr/doctype/ocr_fleet_slip/ocr_fleet_slip.py).
- **Not submittable** (`is_submittable` unset); `track_changes: 1`; autoname `OCR-FS-.#####`.
- **Recon-relevant fields (the contract surface):** `transaction_date`, `total_amount`,
  `vat_amount`, `currency`, `slip_type` (Fuel/Toll/Other), `merchant_name_ocr`,
  `vehicle_registration` (OCR'd plate string), `fleet_vehicle` (Link → Fleet Vehicle),
  `vehicle_match_status` (Auto Matched/Suggested/Unmatched/Confirmed), `litres`,
  `price_per_litre`, `fuel_type`, `odometer_reading`, `toll_plaza_name`, `route`,
  `confidence`, `description`.
- **Image ref:** `drive_file_id`, `drive_link`, `drive_folder_path` (Drive archive) + the scan
  is stored as a private `File` attachment on the record.
- **Disposition fields:** `posting_mode` (Fleet Card / Direct Expense), `fleet_card_supplier`,
  `expense_account`, `cost_center`, `purchase_invoice`, `document_type`.
- **Lifecycle/status:** `Pending → Needs Review → Matched → Draft Created → Completed / No Action / Error`.
  - **Fleet Card** path: `Matched → mark_recorded() → Completed`, `purchase_invoice` stays NULL
    (the v1.2.0 invariant — `ocr_fleet_slip.py:290`).
  - **Direct Expense** path: `create_purchase_invoice()` → Draft Created → (PI submit) → Completed
    (`ocr_fleet_slip.py:76`, guarded so `posting_mode != "Direct Expense"` raises).

### How data flows in / out today
- **IN (only path): Google Drive folder poll.** `poll_drive_fleet_folder()`
  ([`drive_integration.py:1031`](../erpocr_integration/tasks/drive_integration.py)) runs every
  15 min (`hooks.py` scheduler `*/15 * * * *`). For each new file it creates an OCR Fleet Slip
  placeholder (`status=Pending`, `source_type="Gemini Drive Scan"`, `uploaded_by="Administrator"`,
  `drive_file_id` for dedup), saves the scan as a private `File`, and enqueues
  `fleet_gemini_process` on the `long` queue (timeout 600s).
- **Extraction:** `fleet_gemini_process()` ([`fleet_api.py:22`](../erpocr_integration/fleet_api.py))
  → `extract_fleet_slip_data()` ([`gemini_extract.py:662`](../erpocr_integration/tasks/gemini_extract.py))
  → `_populate_ocr_fleet()` + `_run_fleet_matching()` (vehicle match) → save → move Drive file to archive.
- **OUT:** the record itself. `fleet_management` reads OCR Fleet Slips (per CLAUDE.md /
  [CROSS_APP_SURFACE.md §3](../CROSS_APP_SURFACE.md), `monthly_summary.py`) for LITRES_MISMATCH
  and the Fuel Efficiency Tracker. **No PI is produced on the Fleet Card path.**

### Who uses it today, through what UI
- **Drivers:** drop slip scans into a shared Google Drive folder (`fleet_scan_folder_id`). No
  app UI — they never touch ERPNext.
- **Accounts (OCR Manager role):** review OCR Fleet Slips in the ERPNext Desk; click **Mark
  Recorded** (Fleet Card) or **Create PI** (Direct Expense) or **Mark No Action**.
- **Live usage (prod, 2026-06-11):** **39 OCR Fleet Slips** — by status `Matched 26 / Needs
  Review 12 / Draft Created 1`; by mode `Fleet Card 19 / blank 20`; by type `Fuel 37 / Toll 2`.
  **18/19 Fleet Card slips have NULL `purchase_invoice`** (the 1 exception is the single
  `Draft Created` record — a known pre-v1.2.0 artifact noted in CHANGELOG [1.2.0]). The pipeline
  is **already in production use** — the shell would replace the Drive-folder round-trip with
  direct phone capture.

---

## 2. Current API surface (fleet-relevant)

Whitelisted methods (`@frappe.whitelist`). **There is no fleet-slip upload endpoint** — the only
upload endpoint, `api.upload_pdf`, creates an **OCR Import (invoice)**, not a fleet slip.

| Method | Path | HTTP | Permission posture |
|---|---|---|---|
| `upload_pdf` | `erpocr_integration.api.upload_pdf` | **POST** | `has_permission("OCR Import","create")`; per-user pending cap 20; **creates OCR Import, not a fleet slip** (`api.py:36`) |
| `retry_fleet_extraction` | `erpocr_integration.fleet_api.retry_fleet_extraction` | **POST** | OCR Fleet Slip write (`fleet_api.py:387`) |
| `route_to_invoice_pipeline` | `erpocr_integration.fleet_api.route_to_invoice_pipeline` | **POST** | requires Fleet Slip **write** AND OCR Import **create** — narrow reader cannot use it (`fleet_api.py:468`) |
| `create_purchase_invoice` | `OCR Fleet Slip` doc method (`run_doc_method`) | — | Desk action; guarded to `posting_mode="Direct Expense"` (`ocr_fleet_slip.py:76`) |
| `mark_recorded` | `OCR Fleet Slip` doc method | — | Desk action; Fleet Card terminal disposition (`ocr_fleet_slip.py:290`) |
| `unlink_document` | `OCR Fleet Slip` doc method | — | Desk action (`ocr_fleet_slip.py:231`) |
| `mark_no_action` | `OCR Fleet Slip` doc method | — | Desk action (`ocr_fleet_slip.py:330`) |

- **0 `allow_guest`** endpoints anywhere in the app (verified last session).
- **`CROSS_APP_SURFACE.md` claims vs reality:** that file was authored 2026-06-11 from the same
  evidence; its §2 (endpoint list), §3 (read surface), §4 (custom-field contract), and §5
  (roles incl. `OCR Fleet Slip Reader`) match what is verified here. **`OCR Fleet Slip Reader`
  role confirmed present on prod.** No drift found.

### Gemini call pattern, cost, latency (per document)
- One `generateContent` call per slip to `gemini-2.5-flash` (`gemini_extract.py:263`,`:688`),
  structured-output schema, file as inline base64 (max 10MB), per-request `timeout=60s`,
  up to **5 retries** with 429 backoff (15/30/60/120s) (`gemini_extract.py` `_call_gemini_api`).
- **Latency:** documented 3–15s/doc (CLAUDE.md / implementation-patterns); `extraction_time` is
  recorded per slip but not independently re-measured here — treat 3–15s as documented, not
  re-benchmarked. **Cost:** documented **~$0.0001/doc** (not independently verified).
- **Implication for the shell:** extraction is **async on the `long` queue** — the driver's
  upload must NOT block on it. The driver does not need the OCR result on the phone; the slip is
  a recon artifact reviewed by accounts later.

---

## 3. Shell tie-in — Fleet Slip capture (Phase 4)

### What exists
- ✅ The **recon record model** (OCR Fleet Slip, Fleet Card mode, no PI) — exactly the target shape.
- ✅ **Gemini extraction + vehicle matching + Drive archive** pipeline, in production.
- ✅ A **narrow read role** (`OCR Fleet Slip Reader`) for cross-app consumers.
- ✅ Feature-detected `fleet_vehicle` link to `fleet_management`'s Fleet Vehicle.

### What is missing (for a phone-driven capture)
1. **No upload endpoint** for fleet slips — ingestion is Drive-folder only. `upload_pdf` targets
   OCR Import (invoices), wrong doctype.
2. **No client idempotency field.** Drive dedups via `drive_file_id`; an HTTP upload retried over
   flaky 3G would create **duplicate slips** — there is no client-supplied key to dedup on.
3. **No driver create-role.** `OCR Fleet Slip Reader` is read-only; `OCR Manager` is full. A
   driver needs **create on OCR Fleet Slip only** (and explicitly NOT create on OCR Import, so
   they can't open the invoice surface — cf. the `route_to_invoice_pipeline` guard).
4. **Vehicle identity is OCR-guessed.** Match is by OCR'd plate via `_fuzzy_match_vehicle`
   (error-prone — 20/39 prod slips have blank `posting_mode`, i.e. vehicle never confidently
   resolved). A driver-supplied vehicle would be far more reliable, but no ingest parameter
   exists to pass it.
5. **No server-side image compression** — server accepts ≤10MB and validates magic bytes; the
   client must compress before upload.

### DRAFT contract proposal (proposal only — no implementation)

A new whitelisted **POST** endpoint, kept shell-agnostic (a generic fleet-slip upload API):

```
erpocr_integration.fleet_api.upload_fleet_slip   (@frappe.whitelist(methods=["POST"]))

Request (multipart/form-data):
  file            : binary  — JPEG/PNG/PDF, ≤10MB, magic-byte validated (reuse upload_pdf checks)
  idempotency_key : str     — client-generated UUID per capture (REQUIRED)
  fleet_vehicle   : str?     — Fleet Vehicle name, if the driver picked their vehicle (preferred)
  vehicle_registration : str? — fallback plate string if no vehicle picked
  captured_at     : str?     — ISO datetime of capture on device (for offline-queued uploads)

Server behaviour:
  1. Permission: has_permission("OCR Fleet Slip","create"); NOT OCR Import create.
  2. Idempotency: if an OCR Fleet Slip already exists with this idempotency_key → return it
     (200, same body) instead of inserting a duplicate. (Requires a new indexed field, e.g.
     client_upload_id, on OCR Fleet Slip — see Missing #2.)
  3. Validate file (type/size/magic bytes), insert OCR Fleet Slip placeholder
     (status=Pending, source_type="API Upload", company=default), attach File (private).
  4. If fleet_vehicle supplied + feature-detected present → set it and skip fuzzy plate match
     (vehicle_match_status="Confirmed"); posting_mode then auto-sets from the vehicle's
     custom_fleet_card_provider (→ Fleet Card for Wesbank vehicles) exactly as today.
  5. Enqueue fleet_gemini_process on the long queue. Return immediately.

Return shape (200):
  { "ocr_fleet_slip": "OCR-FS-00040", "status": "Pending", "idempotency_key": "<uuid>",
    "duplicate": false }
```

- **Scope enforcement:** the endpoint only ever creates an OCR Fleet Slip in the vehicle-derived
  posting_mode; it cannot create a PI or an OCR Import. The driver role grants create on
  OCR Fleet Slip and nothing else.
- **Idempotency:** the `idempotency_key` makes the POST safe to retry on 3G — a duplicate POST
  returns the existing slip (`"duplicate": true`) rather than a second record.
- **Async:** returns the slip name + `Pending` immediately; the shell shows "uploaded / queued"
  and does **not** wait for Gemini. Accounts review later in the Desk (unchanged).
- **Status read-back (optional):** the shell can poll `GET /api/resource/OCR Fleet Slip/<name>`
  (the driver role would need read on its own records) if a "processed ✓" indicator is wanted —
  but per the recon model the driver doesn't need the extracted data.

---

## 4. Constraints the shell must respect (Ulefone Armor X6 / Android 9 / ~2GB RAM / 3G)

- **Compress on-device before upload.** Phone-camera slips are 3–12MB JPEGs; the server cap is
  10MB and base64-inlining to Gemini bloats large files. Target **≤1.5MB**, long edge ~1280–1600px,
  JPEG quality ~0.7. Legibility of a fuel slip survives this easily; it keeps 3G uploads short.
- **Binary multipart, not base64.** Send `multipart/form-data` (as `upload_pdf` does) — base64 in
  JSON adds ~33% over 3G for no benefit.
- **One image in memory at a time.** 2GB RAM on Android 9: capture → compress → release the
  full-res bitmap before the next slip. Don't hold a gallery of full-res captures.
- **Offline-first queue.** No signal at customer sites is normal. Queue captures locally
  (IndexedDB: compressed blob + idempotency_key + captured_at), upload when signal returns,
  retry with backoff. The `idempotency_key` is what makes retries safe.
- **Don't wait on extraction.** Gemini is 3–15s + up to ~225s of 429 backoff server-side — never
  on the upload's critical path. Driver's task completes at "queued/uploaded".
- **Small, static SPA.** Per portfolio rule, a standalone React SPA; keep the bundle lean and
  avoid heavy client deps — this is a low-end device on a weak network.
- **Vehicle picker over plate-OCR.** Let the driver pick their vehicle once (cache the list);
  passing `fleet_vehicle` removes the single most error-prone server step and fixes the
  blank-`posting_mode` problem seen on 20/39 prod slips.

---

## 5. Risks / open questions (ranked)

| # | Risk / question | Owner | Notes |
|---|---|---|---|
| 1 | **Does the recon actually consume shell-uploaded slips?** The Wesbank recon target lives in **`fleet_management`** (prod doctypes `Wesbank Import`, `Wesbank Account Map`, `Wesbank Account Map Entry` — module "Fleet"), **not in this app**. It reads OCR Fleet Slips by status (per CLAUDE.md). Need to confirm a slip ingested via the new API (source_type "API Upload", not Drive) is picked up identically. | **Architecture chat** (+ fleet_management owner) | The recon target EXISTS — good. But the read path was not inspected here (different app). **Unverified** that source/ingest-path doesn't matter to it. |
| 2 | **Vehicle→driver mapping.** Reliable recon needs the driver's vehicle on the slip. How does the shell know which vehicle(s) a driver may capture for? | **Willie** | 20/39 prod slips never resolved a vehicle (blank posting_mode). A vehicle picker fed from `Fleet Vehicle` fixes this — but the driver↔vehicle assignment source is unspecified. |
| 3 | **New driver create-role + idempotency field** are schema/permission additions to *this* app. | **Willie** (then implement) | Role: create on OCR Fleet Slip only. Field: `client_upload_id` (indexed) for dedup. Both are erpocr_integration changes, not shell. |
| 4 | **posting_mode correctness for shell slips.** It auto-sets from the vehicle's `custom_fleet_card_provider`. If a driver's vehicle lacks the provider, the slip won't become Fleet Card and could be mis-handled as Direct Expense. | **Willie** | Confirm all fleet-card vehicles have `custom_fleet_card_provider` set (it drives the whole recon-vs-invoice fork). |
| 5 | **Drive vs API coexistence.** If drivers move to the shell but the Drive folder stays active, slips could arrive twice (one Drive, one API). | **Architecture chat** | Decide whether the shell replaces or supplements the Drive drop; idempotency only dedups *within* the API path, not across Drive+API. |
| 6 | **driver-dev.local not validated.** All live facts are from prod; the stated dev mirror was unreachable from this session. | Willie (FYI) | If dev has diverged from prod, re-verify the doctype/role facts there before building. |

### Bottom line
The hard part already exists: a non-accounting **recon record** (OCR Fleet Slip / Fleet Card),
in production, consumed by `fleet_management`'s Wesbank recon. The shell work is **additive and
contained**: one new shell-agnostic upload endpoint, one idempotency field, one narrow driver
role, and a client that compresses + queues offline and passes the driver's vehicle. No change to
the recon model, no invoices, no shell awareness in this app.
