# Implementation Patterns — ERPNext OCR Integration

> Knowledge file imported by [CLAUDE.md](../CLAUDE.md). Coding invariants an agent must respect
> when editing this app, the matching system, and the Gemini output schema. System map is in
> [architecture.md](architecture.md). Durable cross-app Frappe rules live in
> `~/.claude/frappe-app-learnings.md` — do not duplicate them here.

## Critical Implementation Patterns

### Permission Elevation in Background Jobs
```python
# frappe.enqueue() does NOT support user= kwarg — passes it to target function
# Instead, set user inside the enqueued function:
def process(raw_payload: str):
    frappe.set_user("Administrator")
    # ... rest of processing
```

### Gemini API Integration
- Uses Gemini 2.5 Flash API with structured output (JSON schema)
- Files sent as base64-encoded inline data with correct MIME type (max 10MB)
- Supported formats: PDF (`application/pdf`), JPEG (`image/jpeg`), PNG (`image/png`)
- Retry logic: 5 attempts with 429-specific long backoff (15s/30s/60s/120s) and shorter 5xx backoff (2s/5s/10s/20s)
- Drive scan staggers enqueue by 5s between files to avoid burst rate limiting
- Extraction time: 3-15 seconds depending on invoice complexity
- **Rate limits**: Free tier = 10 RPM / 500 RPD (will hit limits with batch uploads). Tier 1 (pay-as-you-go with billing linked) = 1,000 RPM / 10,000+ RPD. Check limits at https://aistudio.google.com/rate-limit

### Document Creation (PI / PR / JE)
- **Create dropdown** in top menu: Purchase Invoice, Purchase Receipt, Journal Entry — one click sets `document_type`, saves, and creates the draft
- `document_type` field is hidden from the form — users interact only via the Create menu
- **No auto-creation** — user clicks a Create menu option after reviewing all matches (the opt-in `enable_auto_draft` setting, off by default, auto-drafts high-confidence matches; see *Auto-Draft* below)
- After creation, status becomes **Draft Created** (not Completed) — user can Unlink & Reset if needed
- **Unlink & Reset**: deletes the draft document and resets OCR Import to Matched for re-creation
  - Clears link via `db_set()` BEFORE calling `frappe.delete_doc()` (Frappe blocks deletion of documents with incoming Link references)
  - Works on drafts (docstatus=0) and cancelled documents (docstatus=2); blocks on submitted (docstatus=1)
- **doc_events hooks** (hooks.py): `on_submit` → marks OCR Import as Completed; `on_cancel` → clears link + resets to Matched
- `flags.ignore_mandatory = True` on all created documents (drafts may have incomplete data)
- Set `bill_date` from OCR invoice_date; only set `due_date` if >= posting_date
- `default_item` in OCR Settings: when set, acts as the matching pipeline's tier 6 fallback (returns "Suggested") AND as the unmatched-line filler at PI creation time. Lets bulk-expense-invoice users skip per-row clicks. For rows matched to the default_item, the description→item **alias** and **Item Supplier** learning are skipped (useless when the item is always the catch-all), but **service-mapping learning IS kept** — for a catch-all item the `(supplier, pattern) → expense account + cost center` coding is the meaningful thing to learn, and it lets such lines auto-code (and auto-draft) next time.
- **Tax template**: `_build_taxes_from_template()` shared helper handles template validation, company check, tax-inclusive detection, and taxes list building for both PI and PR creation. **Actual-row injection**: when the selected template has a `charge_type="Actual"` row and the OCR Import carries a `tax_amount`, that amount is injected into the first Actual row (customs/import VAT is a fixed amount, not a percentage — the Cargo Compass fix). Template *selection* (`api._select_tax_template`) picks `import_tax_template` over `default_tax_template` when the extracted tax deviates >25% (relative) from the default template's percentage of the subtotal.
- **Alias learning upserts**: `_save_supplier_alias` / `_save_item_alias` UPDATE an existing alias when the user confirms a different target (first-mapping-wins-forever silently dropped corrections and kept auto-matching the wrong record at tier-1 confidence). **Item-alias learning is supplier-scoped since v1.8.0 (Q7c)**: when the parent supplier is known, the insert/correction targets the supplier-scoped row only — a confirm for supplier A never rewrites the global row other suppliers rely on. Confirms without a supplier still write global rows.
- **Cost Center precedence** (v1.1.3+): every PI line, PR line, JE debit line, JE tax line, and JE credit line resolves cost_center via **line override → doc-level parent (`OCR Import.cost_center`) → `OCR Settings.default_cost_center`**. The doc-level field is filtered by company on the client side. Service-mapping rows still populate line-level cost_center (which wins), so per-supplier cost centre splits keep working; the doc-level field is the bulk-review shortcut for everything else.

### Auto-Draft (opt-in; off by default)
- `tasks/auto_draft.py` — `attempt_auto_draft()` runs after matching in `gemini_process()` **only when `OCR Settings.enable_auto_draft` is on**.
- Confidence gate (`_is_high_confidence`): supplier + every item must be alias/exact/service-mapping matched (NOT fuzzy/unmatched). Low-confidence records fall through to "Needs Review" unchanged.
- Auto-links a PO if one exists (`_auto_link_purchase_order`), detects document type (`_auto_detect_document_type`, default PI), then calls the existing `create_purchase_invoice()`.
- **Invoice-date fiscal-year guard** (`_invoice_date_in_fiscal_year`): a Gemini date misread (e.g. 2001 for 2026) would fail deep in ERPNext's FY validation — the guard rejects it up front with a clean skip reason. It imports `get_fiscal_year` from **`erpnext.accounts.utils`** (NOT `frappe.utils` — v1.5.1 fixed a bug where the wrong import location AttributeError'd on every call and the blanket except skipped EVERY gate-passing invoice as "outside any active Fiscal Year"). ImportError fails open (guard passes; create surfaces any FY problem).
- **Totals-reconciliation gate** (`_totals_reconcile`, v1.9.0 / Q11): a PI builds from `qty × rate`, so a **globally-discounted invoice** — where Gemini captured the discount in the extracted subtotal but not the line rates (the schema has no discount field) — systematically **over-drafts** (live: `OCR-IMP-01918` drafted R2,654.98 vs a R2,522.22 invoice). The gate skips auto-draft (bidirectionally — over- *and* under-draft) when `Σ(qty × rate)` deviates from the reference beyond tolerance `max(1%, R1.00)` (module constants `_TOTALS_TOLERANCE_PCT` / `_TOTALS_TOLERANCE_ABS`, NOT a setting). **Reference depends on tax-inclusivity**: it reuses `_detect_tax_inclusive_rates` — inclusive rates reconcile against `total_amount`, exclusive against `subtotal` (with a `total − tax` fallback when subtotal is 0). Comparing an inclusive line sum against the *subtotal* would false-fail every inclusive invoice by the tax amount and silently kill auto-draft for that class (caught in code-review this release — the byte-for-byte trap). Degenerate cases (zero line sum, no usable reference) pass rather than block. Skip reason names both amounts. Auto-draft-only — manual creation is untouched (ADR-0002).
- Audit fields on OCR Import: `auto_drafted`, `auto_draft_skipped_reason`. Diagnosing "auto-draft isn't firing" starts with a group-by on `auto_draft_skipped_reason` — it names the blocking tier per record (supplier/item confidence, FY, or the new totals mismatch).
- `stats_api.get_ocr_stats` (role-gated: System Manager / Accounts Manager) backs the OCR Stats page (counts, auto-draft ratio, fallback reasons, per-supplier throughput).
- **Sibling pattern — Fleet Card auto-record** (v1.8.0, Q8): `tasks/auto_record.py` applies the same opt-in + confidence-gate + skip-reason-audit shape to Fleet Card slips (`enable_fleet_auto_record`, `auto_recorded`, `auto_record_skipped_reason`), completing via `mark_recorded()` instead of creating anything. See [architecture.md](architecture.md) → *OCR Fleet Slip Workflow*.

### Purchase Order / Purchase Receipt Linking
- Optional: user can link OCR Import to an existing PO via "Find Open POs" button
- When PO selected, "Match PO Items" auto-matches OCR items to PO items by item_code (user reviews before applying)
- If PO has existing PRs, system surfaces them for selection — PR field constrained to PRs against the selected PO only
- PI items get both PO refs (`purchase_order` + `po_detail`) and PR refs (`purchase_receipt` + `pr_detail`) — closes full PO→PR→PI chain
- PR items get PO refs (`purchase_order` + `purchase_order_item`) — different field names from PI (ERPNext v15 schema)
- **PO item auto-match fallback**: if user selects PO but skips "Match PO Items" dialog, PI/PR/DN creation auto-matches by `item_code` (FIFO) — prevents orphaned PO linkage
- Stale field clearing: changing supplier clears PO/PR; changing PO clears PR and all item-level refs

### Journal Entry Creation
- For expense receipts (restaurant bills, toll slips, entertainment) that don't need PI/PR
- Requires: expense_account on items + credit_account (from field or OCR Settings default)
- Builds balanced JE: debit lines per item → expense accounts, credit line → bank/payable
- Tax handled as separate debit line to VAT input account (if tax detected)
- Account validation: all accounts must belong to company, not be group or disabled

### Server-Side Guards
- **Status guards**: PI/JE require Matched or Needs Review; PR requires Matched only; Draft Created blocks all creation (matches UI gating, prevents API bypass)
- **Document type enforcement**: each create method validates `document_type` matches (prevents API bypass)
- **Cross-document lock**: row-lock checks all three output fields (PI, PR, JE) — only one document per OCR Import
- **PO/PR linkage validation**: at create time, re-verifies PR belongs to selected PO (server-side, not just UI)
- **Row-level permissions**: `match_po_items` and `match_pr_items` check per-document read permission (not just doctype-level)
- **XSS prevention**: all dynamic values in PO/PR/match dialogs escaped via `frappe.utils.escape_html()` and `encodeURIComponent()`
- **Tax ambiguity threshold**: `_detect_tax_inclusive_rates()` returns False (default exclusive) when inclusive vs exclusive difference is < 5% of tax amount
- **Account validation (JE)**: credit/expense/tax accounts checked for company, is_group=0, disabled=0
- **Drive retry cap**: `MAX_DRIVE_RETRIES=3` prevents infinite Gemini calls on permanently bad Drive files. The cap covers BOTH Gemini-call failures (post-download) and pre-download failures (empty content, oversize, magic-byte mismatch) — `_record_drive_scan_failure` in [drive_integration.py](../erpocr_integration/tasks/drive_integration.py) inserts a status=Error placeholder for every failure path so the dedup branch on the next poll can count it.
- **Drive archive-move 404 tolerance**: a 404 from Drive on `move_file_to_archive` is treated as already-archived (file was manually moved or archived by a prior run) — logged at info, not error. Other `HttpError` and exception types still escalate to Error Log.

### Upload Security
- Permission check: User must have "create" permission on OCR Import
- File validation: PDF, JPEG, PNG only; max 10MB; magic bytes verified
- Whitelisted endpoint: `@frappe.whitelist(methods=["POST"])`

### Background Processing
- Upload creates placeholder OCR Import immediately, returns record name
- Uploaded file saved as private Frappe File attachment (enables retry on failure)
- Processing runs on `long` queue with `timeout=600s` — covers worst-case Gemini retry shape (5 attempts × up to 60s + up to 225s of 429 backoff)
- **Rate-limit stagger lives at the caller**: batched ingestion pollers (`poll_drive_scan_folder`, `poll_drive_dn_folder`, `poll_drive_fleet_folder`, `email_monitor.poll_email_inbox`) `time.sleep(5)` between successive `frappe.enqueue` calls so workers don't all hit Gemini at once. Processor functions themselves no longer sleep — the full 600s job timeout is reserved for extraction + retries. Manual upload (single file) has no caller-side stagger; the request-layer 429 retry handles stampede.
- Real-time progress updates via `frappe.publish_realtime()`
- `frappe.db.commit()` required in enqueued jobs (with `# nosemgrep` comment)
- Failures logged to Error Log, status set to "Error"
- **Retry on error**: "Retry Extraction" button on all Error records — reads from Drive file or local attachment
- **Retry clears stale links**: retry endpoints reset supplier/vehicle/item links and child tables before re-extraction (prevents stale data from previous failed runs persisting)
- **Email attachments saved**: email monitor saves PDF/image as Frappe File attachment on the OCR Import, enabling retry even after the email is deleted

## Matching System

**Supplier matching** runs in this order:
1. **Alias table** (`OCR Supplier Alias` — exact, learned from confirmations)
2. **ERPNext Supplier** by name (exact)
3. **Fuzzy matching** (difflib SequenceMatcher, returns "Suggested" status)
4. If no match → "Unmatched"

**Item matching** runs in this order (highest specificity first; alias tier split in v1.8.0/Q7c):
1. **`Item Supplier` lookup** (`(supplier, supplier_part_no=product_code) → item_code`) — supplier-scoped, deterministic. Multi-hit ambiguity is logged and skipped (falls through). Highest precision because supplier-scoped, runs before description aliases.
2. **Supplier-scoped `OCR Item Alias`** (exact on description + this supplier; v1.8.0) — beats the global alias, so the same printed description can map to different items per supplier (cross-supplier collision resolution).

> **Chained confidence cap (v1.9.0 / Q10, ruled *cap, don't skip*):** tiers 1 and 2 are **supplier-keyed** — they only make sense if the supplier is right. When the supplier's own match is a fuzzy `Suggested`, these two tiers return the **min of the chain** (`Suggested`, not `Auto Matched`) via `_cap_to_supplier(status, supplier_status)`; the item pre-fill is kept. Confirmed/exact/manually-set suppliers are uncapped (today's behavior). Callers thread `ocr_import.supplier_match_status` into `match_item(..., supplier_status=)` and `match_item_by_supplier_part(..., supplier_status=)` on **both** the invoice (`api._run_matching`) and DN (`dn_api._run_dn_matching`) paths. Tiers 3–7 are NOT supplier-keyed and are never capped. Auto-draft is unaffected — a `Suggested` supplier already blocks it (regression-asserted).
3. **Global `OCR Item Alias`** (exact on description, blank supplier) — every pre-v1.8.0 alias row lives here unchanged; the fallback alias tier. NOTE: `OCR Item Alias` is hash-named since v1.8.0 — always look rows up by `ocr_text` + `supplier` filters, never by document name.
4. **ERPNext Item** by `item_name` / `item_code` (exact)
5. **Service mapping** (pattern-based: description substring → item + GL account + cost center). Priority within this tier: supplier-specific pattern → generic pattern → **supplier default** (a supplier-scoped row whose `description_pattern` is the literal `*` sentinel — codes ANY remaining line for that supplier; the last-resort tier for suppliers whose descriptions vary too much to learn per-pattern, e.g. a transport subcontractor where every line embeds route/driver/vehicle).
6. **Fuzzy matching** (difflib SequenceMatcher, configurable threshold, returns "Suggested" status)
7. **`default_item` fallback** (only if `OCR Settings.default_item` is configured) — returns "Suggested" so user still confirms and `auto_draft` skips
8. If no match → "Unmatched"

**Item Supplier learning** (v1.1.0+): when a user confirms an OCR row with `item_code` + `product_code` + parent `supplier` set (and `item_code != default_item`), `OCRImport._enqueue_item_supplier_learning` enqueues `tasks/learn_item_supplier.py` on the `short` queue with `enqueue_after_commit=True`. The job:
- Sets `frappe.set_user(originating_user)` and checks `has_permission("Item", "write")` — sites that don't grant Item write to OCR Manager get silent skip + log; matching still works without learning.
- Saves `Item.append("supplier_items", ...)` WITHOUT `ignore_permissions` — so the learning respects the originating user's actual perms.
- Dedup key: `item_code:supplier:product_code` (collapses concurrent confirms in queue); does NOT replace the in-job DB existence re-check.
- Try/except around save: failures are logged, never break OCR confirm flow.

**Default-item learning** (catch-all item): when `item.item_code == OCR Settings.default_item`, `_save_item_alias` and `_enqueue_item_supplier_learning` skip (a description→catch-all alias is useless; an Item-Supplier row would point a product code at the catch-all). **`_save_service_mapping` does NOT skip** — for a catch-all item the GL coding *is* the learnable signal, so the `(supplier, pattern) → expense account + cost center` mapping is saved and the line auto-codes next time. (Before this change, default_item lines learned nothing, so catch-all-heavy invoices never reached high confidence and auto-draft never fired — see the supplier-default `*` mapping above for variable-description suppliers.)

User confirmations saved as `OCR Supplier Alias` / `OCR Item Alias` (description-based) and `Item Supplier` (supplier-product-based).

Service mappings support supplier-specific patterns (higher priority), generic patterns, and a per-supplier **default** (`description_pattern = *`, supplier set) that codes any otherwise-unmatched line for that supplier as a last resort.

Both pattern storage and runtime matching use `normalize_for_matching()` (strips punctuation, lowercases, collapses whitespace) so patterns match regardless of formatting differences between invoices.

When saving service mappings, `_extract_service_pattern()` strips dates (DD/MM/YYYY, YYYY-MM-DD with plausible day/month bounds), month names, years (1900-2199), and trailing prepositions from OCR descriptions to produce reusable patterns (e.g., "Pro Plan - Jan 2026 to Feb 2026" → "pro plan"). A quality guard rejects patterns that reduce to only stop words (e.g., "for", "of the") and falls back to the full normalized description.

## Gemini Structured Output Schema
```json
{
  "invoices": [
    {
      "supplier_name": "string (required)",
      "supplier_tax_id": "string (empty if not present)",
      "invoice_number": "string (required)",
      "invoice_date": "YYYY-MM-DD (required)",
      "due_date": "YYYY-MM-DD (empty if not present)",
      "subtotal": "number (0 if not shown)",
      "tax_amount": "number (0 if not shown)",
      "total_amount": "number (required)",
      "currency": "string (e.g. USD, ZAR, EUR)",
      "line_items": [
        {
          "description": "string (required)",
          "product_code": "string (empty if not present)",
          "quantity": "number (required)",
          "unit_price": "number (required)",
          "amount": "number (required)"
        }
      ]
    }
  ]
}
```
- Wrapped in `invoices[]` array — supports multi-invoice PDFs (one PDF → multiple OCR Imports); images always produce a single invoice
- Gemini returns data in this structure (enforced by response_schema)
- Dates auto-converted to YYYY-MM-DD
- Currency symbols stripped from amounts
- Product codes extracted separately from descriptions
- The delivery-note, fleet-slip, and statement pipelines each use their own prompt + schema variant (`gemini_extract.py`)
