# OCR Statement Reconciliation -- Accountant Guide

**For: Accounting team members who reconcile supplier statements**

---

## What This Does

The OCR Statement tool reads supplier account statements (PDF) using AI, extracts each transaction line, and automatically reconciles them against your submitted Purchase Invoices in ERPNext. It tells you:

- Which invoices on the statement match PIs in ERPNext
- Where amounts don't agree (mismatches)
- Which statement invoices are missing from ERPNext (not yet captured)
- Which ERPNext PIs don't appear on the statement (reverse check)
- Which lines are payments (credits)

**Important:** This is a read-only reconciliation tool. It does not create, modify, or delete any documents in ERPNext. It only compares and reports.

---

## How Statements Get Into the System

Statements are processed through the same Google Drive scan folder as invoices. The system uses AI to classify each uploaded file as either an **invoice** or a **statement**:

- **Invoice** --> creates an OCR Import (normal invoice workflow)
- **Statement** --> creates an OCR Statement (reconciliation workflow)

You don't need to separate them -- just drop everything in the same Drive folder and the system routes each file to the correct pipeline.

> **Note:** If the classifier can't determine the document type, it defaults to invoice. This ensures the existing invoice pipeline is never disrupted.

---

## Reviewing a Statement

### Finding Statements

1. In the search bar, type **OCR Statement** and press Enter
2. Filter by **Status = Reconciled** to see statements ready for review
3. Each record shows a summary bar at the top: matched / mismatches / missing / payments

### Understanding the Record

| Section | What's There |
|---|---|
| **Header** | Supplier (OCR name + matched ERPNext supplier), statement date, period |
| **Balances** | Opening balance, closing balance, currency |
| **Summary counts** | Total lines, matched, mismatches, missing from ERPNext, not in statement, payments |
| **Items table** | Each transaction line with reconciliation status and matched PI details |

### Understanding Reconciliation Statuses

Each line in the items table has a **Recon Status**:

| Status | Meaning | Action Needed |
|---|---|---|
| **Matched** | Statement amount matches ERPNext PI amount | None -- all good |
| **Amount Mismatch** | PI found but amounts differ | Check the difference column -- may be rounding, credit notes, or data entry errors |
| **Missing from ERPNext** | Invoice on statement but no matching PI in ERPNext | Investigate -- this invoice may not have been captured yet |
| **Not in Statement** | ERPNext PI exists for this supplier/period but isn't on the statement | May be timing (PI posted after statement cut-off) or a supplier omission |
| **Payment** | Credit line on the statement (payment received) | Informational -- no action needed |
| **Unreconciled** | No reference number to match against | Check the statement line manually |

### How Matching Works

The system matches statement transaction references to ERPNext Purchase Invoice `bill_no` fields:

1. The reference from the statement (e.g. "INV/2024/042") is normalised (punctuation removed, lowercased)
2. All submitted PIs for the same supplier are loaded, and their `bill_no` values are normalised the same way
3. A match is found when normalised values are identical (e.g. "INV/2024/042" matches "INV-2024-042")

This means your Purchase Invoices must have the `bill_no` (Supplier Invoice No) field filled in for reconciliation to work.

---

## Working with Statements

### Correcting the Supplier

If the supplier wasn't matched correctly (or at all):

1. Set the correct **Supplier** on the OCR Statement record
2. Click **Re-Reconcile** -- the system re-runs reconciliation against the correct supplier's PIs
3. The summary and item statuses update immediately

### Marking as Reviewed

Once you've reviewed all lines and taken any needed action:

1. Click **Actions > Mark Reviewed**
2. Status changes from "Reconciled" to "Reviewed"

This is optional but helps track which statements have been checked.

### Reverse Check

When the statement has valid period dates (Period From and Period To), the system also does a **reverse check**: it looks for submitted PIs in ERPNext that fall within the statement period but don't appear on the statement. These show up as "Not in Statement" rows.

If the period dates are missing from the statement, the reverse check is skipped and a yellow notice appears at the top of the form.

---

## Setup

No additional setup is required beyond the standard OCR Settings configuration. Statements are automatically detected and processed when dropped in the Drive scan folder.

For best results, ensure your Purchase Invoices have the **Supplier Invoice No** (`bill_no`) field filled in -- this is the reference used for matching.

---

## Status Reference

| Status | Meaning |
|---|---|
| **Pending** | Created, waiting to be processed |
| **Extracting** | AI is reading the statement |
| **Reconciled** | Extraction complete, lines reconciled against ERPNext |
| **Reviewed** | Accounting team has reviewed and signed off |
| **Error** | Something went wrong during extraction |

---

## Troubleshooting

| Problem | What to Do |
|---|---|
| Statement processed as invoice instead of statement | This can happen with simple statements. The OCR Import will be created instead. No harm done -- process it normally or delete it. |
| All lines showing "Missing from ERPNext" | Check that the supplier is matched correctly. If wrong, correct it and click Re-Reconcile. Also verify that your PIs have `bill_no` filled in. |
| No "Not in Statement" rows even though you expect some | The reverse check only runs when the statement has valid Period From and Period To dates. If those are missing, you'll see a yellow notice. |
| Amounts showing as mismatched by small amounts | Common with rounding differences between supplier and ERPNext calculations. Review the difference column -- if it's less than a few cents, it's likely rounding. |
| Statement shows "Unreconciled" for many lines | These are lines where the AI couldn't extract a clear reference number. Check the original statement manually for those lines. |

---

## Where to Find Things

| What | Where |
|---|---|
| All statements | OCR Statement list |
| Statements needing review | OCR Statement list > filter: Status = Reconciled |
| The original PDF | Click the Drive link on the OCR Statement record |
| Matched Purchase Invoices | Click the **Matched Invoice** link on any item row |
| Supplier aliases | OCR Supplier Alias list (shared with invoice pipeline) |
| Settings | Search "OCR Settings" in the search bar |
