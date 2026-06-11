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
  file              : binary — JPEG/PNG/PDF, ≤10MB, magic-byte validated (reuse upload_pdf checks)
  client_request_id : str    — client-generated UUID at submit (REQUIRED; idempotency key)
  fleet_vehicle     : str?    — Fleet Vehicle name; shell pre-fills from the driver's
                               get_driver_context (Fleet Vehicle.assigned_user), picker-overridable
  vehicle_registration : str? — fallback plate string if no vehicle picked
  captured_at       : str?    — ISO datetime of capture on device (offline-queued uploads)

Server behaviour:
  1. Permission: has_permission("OCR Fleet Slip","create"); NOT OCR Import create. The driver
     role is create-on-OCR-Fleet-Slip ONLY, with if_owner read scoping (a driver cannot read
     other drivers' slips) — the pattern fleet already uses for inspections.
  2. Idempotency (R-B house template, verbatim): client_request_id stored in a NEW custom field
     with a DB UNIQUE constraint. Insert-and-catch — on DuplicateEntryError, fetch the existing
     slip and return it with "duplicate": true. No pre-check SELECT; the unique constraint is the
     source of truth, safe under concurrent 3G retries.
  3. source_type discriminates Drive vs API. NOTE: source_type already EXISTS on OCR Fleet Slip
     (Data; Drive sets "Gemini Drive Scan") — set the API path to a distinct value
     (e.g. "Gemini Shell Upload"). No new field needed for the discriminator; only
     client_request_id is a schema add.
  4. Validate file (type/size/magic bytes), insert OCR Fleet Slip (status=Pending,
     company=default), attach File (private).
  5. If fleet_vehicle supplied + feature-detected present → set it, vehicle_match_status="Confirmed",
     skip fuzzy plate match; posting_mode then auto-derives from custom_fleet_card_provider.
  6. FAIL SAFE: if posting_mode cannot resolve to "Fleet Card" (vehicle missing the provider),
     the slip lands flagged-for-review (Needs Review) — NEVER silently routed toward the invoice
     path. The recon-vs-invoice fork must not depend on a data field being perfectly maintained.
  7. Enqueue fleet_gemini_process on the long queue. Return immediately.

Return shape (200):
  { "ocr_fleet_slip": "OCR-FS-00040", "status": "Pending",
    "client_request_id": "<uuid>", "duplicate": false }
```

- **Scope enforcement:** the endpoint only ever creates an OCR Fleet Slip; it cannot create a PI
  or an OCR Import. The driver role grants create on OCR Fleet Slip and nothing else, `if_owner`-scoped.
- **Idempotency:** the `client_request_id` + DB unique constraint (insert-and-catch) makes the
  POST safe to retry on 3G — a duplicate returns the existing slip with `"duplicate": true`.
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

## 5. Decisions (resolved 2026-06-11) + carried-forward verification

The report's original open questions were resolved in the architecture chat + by Willie on
2026-06-11. Recorded here as the design baseline for the P4 kickoff:

1. **Drive vs API → SUPPLEMENT, never replace.** Drive keeps serving office/bulk/back-capture;
   the API serves phone capture. `source_type` discriminates them — and **already exists** on
   OCR Fleet Slip (Data field), so no schema add for the discriminator.
2. **Recon consumption of API slips → P4 kickoff Task T1 (verify, don't assume).** The Wesbank
   recon lives in `fleet_management` (`Wesbank Import` + maps, module "Fleet"); it almost
   certainly keys on the slip record, not its provenance, but this must be **verified** before
   relying on it. Until then, design so an API slip is **indistinguishable from a Drive slip at
   the doctype level except `source_type`**. *(This is the one item NOT closed here — it's a
   kickoff task, owner = whoever runs T1 against `fleet_management`.)*
3. **Driver↔vehicle mapping → already exists.** `Fleet Vehicle.assigned_user` via
   `get_driver_context`. Shell pre-fills the driver's vehicle with a **picker override**
   (possession ruling — relief drivers fuel the truck they're actually driving). Driver-confirmed
   vehicle beats plate-OCR and fixes the 20/39 unresolved-vehicle problem going forward.
4. **Create-role + idempotency → APPROVED, tightened.** Role = create-on-OCR-Fleet-Slip ONLY
   (not OCR Import), **`if_owner`-scoped** so a driver cannot read other drivers' slips (the
   pattern fleet uses for inspections). Idempotency = the **R-B house template verbatim**: client
   UUID at submit → `client_request_id` custom field → DB unique constraint → insert-and-catch →
   return existing + `duplicate:true`.
5. **`custom_fleet_card_provider` audit → Willie runs the one-query check (prod + mirror).**
   Independent of the audit, the contract **must FAIL SAFE**: a slip against an unprovided vehicle
   lands **flagged-for-review**, never silently routed toward the invoice path. The
   recon-vs-invoice fork may not depend on a data field being perfectly maintained. *(Baked into
   §3 server behaviour step 6.)*
6. **`driver-dev.local` not validated here** (unreachable this session; facts are from prod). If
   the mirror has diverged, re-verify the doctype/role facts there before building.

### Bottom line
The hard part already exists: a non-accounting **recon record** (OCR Fleet Slip / Fleet Card),
in production, consumed by `fleet_management`'s Wesbank recon. The shell work is **additive and
contained**: one new shell-agnostic upload endpoint, one idempotency field, one narrow driver
role, and a client that compresses + queues offline and passes the driver's vehicle. No change to
the recon model, no invoices, no shell awareness in this app.
