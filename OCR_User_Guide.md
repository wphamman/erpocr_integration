# OCR Invoice Import — User Guide & Test Plan

**For: Star Pops Accounting Team**
**System: erp.starpops.co.za**

---

## What This Does

The OCR Import tool reads supplier invoice PDFs and automatically creates **draft Purchase Invoices** in ERPNext. It uses AI (Google Gemini) to extract the supplier name, invoice number, date, line items, quantities, and amounts from the PDF.

You upload a PDF → the system reads it → you review the extracted data → a draft PI is created.

**Important:** It only creates *drafts*. Nothing is posted or submitted automatically. You always have a chance to review before anything hits the books.

---

## Part 1: Initial Setup (One-Time)

Before testing, an administrator needs to configure OCR Settings.

### Step 1 — Open OCR Settings

1. In the search bar at the top of ERPNext, type **OCR Settings** and open it

### Step 2 — Fill In the Settings

| Field | What to Enter |
|---|---|
| **Gemini API Key** | The API key provided by your administrator (starts with `AIza...`) |
| **Gemini Model** | Select **gemini-2.5-flash** (recommended — fast and accurate) |
| **Default Company** | Star Pops |
| **Default Warehouse** | Your main receiving warehouse (e.g. "Stores - SP") |
| **Default Expense Account** | A general expense account for service items (e.g. "Cost of Goods Sold - SP") |
| **Default Cost Center** | Your main cost center (e.g. "Main - SP") |
| **VAT Tax Template** | South Africa Tax - SP |
| **Non-VAT Tax Template** | Z - Not Registered for VAT - SP |
| **Matching Threshold** | Leave at **80** (this controls how closely item names need to match — 80% is a good starting point) |

### Step 3 — Save

Click **Save**. You're ready to test.

---

## Part 2: Test Plan — Manual Upload

This is the simplest way to test. You upload a PDF directly.

### Test 1 — Upload a Simple Invoice

**What you need:** A single-page PDF invoice from a supplier that already exists in ERPNext.

1. In the search bar, type **OCR Import** and click **+ Add OCR Import** (or go to the OCR Import list and click **New**)
2. Click the **Actions > Upload PDF** button in the top-right
3. Select your PDF file (must be under 10 MB)
4. Wait 5–30 seconds — you'll see progress messages ("Uploading...", "Extracting...", "Processing...")
5. The page will reload automatically when extraction is done

**What to check after extraction:**

| Field | What to Look For |
|---|---|
| **Status** | Should be "Needs Review" or "Matched" |
| **Supplier (OCR)** | The supplier name the AI read from the invoice |
| **Supplier** | If auto-matched, this will be filled with the ERPNext supplier. If blank, the system couldn't find a match |
| **Supplier Match Status** | "Auto Matched", "Suggested", or "Unmatched" |
| **Invoice Number** | Should match the invoice number on the PDF |
| **Invoice Date** | Should match the date on the PDF |
| **Total Amount** | Should match the invoice total |
| **Tax Amount** | Should match the VAT amount (if applicable) |
| **Tax Template** | Should be auto-set: "South Africa Tax" for invoices with VAT, "Not Registered for VAT" for invoices without |
| **Confidence** | A colour-coded badge: Green (high) / Orange (medium) / Red (low) |
| **Items table** | Each line item from the invoice should appear with description, quantity, rate, and amount |

**Pass criteria:** The extracted data reasonably matches what's on the PDF. Small formatting differences are normal (e.g., "R 1,500.00" becomes "1500.0").

---

### Test 2 — Confirm Matches and Create a Purchase Invoice

**Starting from:** A completed Test 1 with status "Needs Review"

**If the supplier was auto-matched (Supplier field is filled):**

1. Verify the matched supplier is correct
2. Change the **Supplier Match Status** dropdown to **Confirmed**
3. Click **Save**

**If the supplier was NOT matched (Supplier field is blank):**

1. Click the **Supplier** link field
2. Search for and select the correct ERPNext supplier
3. Change the **Supplier Match Status** dropdown to **Confirmed**
4. Click **Save**
5. The system will remember this match for next time (creates a "Supplier Alias")

**For each line item in the Items table:**

1. Check the **Match Status** column:
   - **Auto Matched** — the system found the item automatically. Verify it's correct.
   - **Suggested** — the system found a close match but isn't sure. Check carefully.
   - **Unmatched** — no match found. You need to select the item manually.
2. For unmatched or incorrect items:
   - Click the **Item Code** field in that row
   - Search for and select the correct ERPNext item
   - If this is a service/expense item (not stock), also set the **Expense Account** and optionally the **Cost Center**
   - Change the **Match Status** to **Confirmed**
3. Click **Save**

**Once all items are matched and the status shows "Matched":**

If everything was auto-matched during extraction, a draft Purchase Invoice is created automatically — the status will already be "Completed" with a link to the PI.

If you had to manually confirm matches, click **Actions > Create Purchase Invoice** after saving. The system creates a draft PI and the status changes to "Completed".

**Pass criteria:** A draft Purchase Invoice is created with the correct supplier, line items, amounts, and tax template. The PI is in draft status (not submitted).

---

### Test 3 — Upload an Invoice from a New Supplier

**What you need:** A PDF from a supplier that does NOT exist in ERPNext yet.

1. Upload the PDF (same as Test 1)
2. After extraction, the **Supplier** field will be blank and **Supplier Match Status** will be "Unmatched"
3. **First:** Go create the supplier in ERPNext (Buying > Supplier > New)
4. Come back to the OCR Import record
5. Select the new supplier in the **Supplier** field
6. Set **Supplier Match Status** to **Confirmed** and save
7. Continue with item matching as in Test 2

**Pass criteria:** Works end-to-end even when the supplier didn't exist initially. After confirming, the supplier alias is saved so future invoices from this supplier match automatically.

---

### Test 4 — Test the Learning System

**What you need:** A second PDF from the same supplier used in Test 2 or Test 3.

1. Upload the second PDF
2. After extraction, check:
   - The **Supplier** should be auto-matched this time (because you confirmed it before)
   - Items that you previously confirmed should also be auto-matched
3. If everything is matched, the status should be "Matched" immediately

**Pass criteria:** The system remembers previous confirmations and auto-matches on the second invoice. Less manual work each time.

---

### Test 5 — Upload a Multi-Invoice PDF (Statement)

**What you need:** A PDF that contains multiple invoices (e.g., a monthly statement with several invoices).

1. Upload the PDF (same as Test 1)
2. After extraction, **multiple OCR Import records** will be created — one per invoice found in the PDF
3. Check the OCR Import list — you should see several new records from the same upload
4. Review each one individually

**Pass criteria:** Each invoice in the statement gets its own separate OCR Import record with the correct data.

---

### Test 6 — Error Handling

**What you need:** A file that is NOT a valid invoice (e.g., a photo, a blank PDF, or a non-PDF file).

1. Try uploading a non-PDF file → should show "Only PDF files are supported"
2. Try uploading a PDF larger than 10 MB → should show "File too large"
3. Try uploading a blank or illegible PDF → should complete extraction but with minimal/incorrect data and possibly a low confidence score

**Pass criteria:** The system handles bad input gracefully without crashing. Error records show status "Error" and can be found in the OCR Import list.

---

## Part 3: Day-to-Day Workflow (After Testing)

Once you're comfortable with the system, the daily workflow looks like this:

### Uploading Invoices

1. **Manual upload:** Go to OCR Import > New > Upload PDF
2. **Email (if enabled):** Forward invoice emails to the designated email address — they're picked up automatically every hour
3. **Google Drive (if enabled):** Drop PDFs into the shared Drive folder — they're picked up automatically every 15 minutes

### Reviewing Imports

1. Go to the **OCR Import list** (search "OCR Import" in the search bar)
2. Filter by **Status = Needs Review** to see imports that need attention
3. Open each one, confirm/correct the supplier and items, then create the Purchase Invoice

### Tips for Faster Processing

- **Confirm matches** (set status to "Confirmed") whenever the system gets it right — this teaches the system and improves future accuracy
- **Service items** (subscriptions, rent, professional fees) need an Expense Account set on the item row — the system will remember this for next time via Service Mappings
- **Check the confidence score** — green (high) means the AI is very confident in the extraction, red (low) means you should check the data more carefully
- Items and suppliers that you've confirmed before will auto-match in future — the system gets better over time

### Where to Find Things

| What | Where |
|---|---|
| Upload a new invoice | OCR Import > New > Actions > Upload PDF |
| See all imports | OCR Import list (filter by status) |
| Review pending imports | OCR Import list > filter: Status = Needs Review |
| See created Purchase Invoices | Click the **Purchase Invoice** link on any Completed OCR Import |
| View saved supplier aliases | OCR Supplier Alias list |
| View saved item aliases | OCR Item Alias list |
| View service mappings | OCR Service Mapping list |
| Check for errors | Error Log (filter by "ocr") |
| OCR Settings | Search "OCR Settings" in the search bar |

---

## Status Reference

| Status | Meaning | Action Needed |
|---|---|---|
| **Pending** | Just uploaded, waiting to be processed | Wait — processing starts automatically |
| **Needs Review** | Data extracted, but supplier or items need manual matching | Review and confirm matches |
| **Matched** | All suppliers and items matched, PI auto-created | Check the draft PI |
| **Completed** | Purchase Invoice created | Nothing — all done |
| **Error** | Something went wrong during extraction | Check the error details, retry or re-upload |

---

## Troubleshooting

| Problem | What to Do |
|---|---|
| Status stuck on "Pending" for more than 5 minutes | The background job may have failed. Check Error Log for OCR-related errors, or try uploading again. |
| Supplier not matching | The supplier name on the invoice may be different from the name in ERPNext (e.g., "ABC Pty Ltd" vs "ABC (Pty) Ltd"). Select it manually and confirm — next time it will match. |
| Items not matching | Same as above — item descriptions on invoices often differ from ERPNext item names. Confirm once and it learns. |
| Wrong amounts extracted | Check the original PDF — unusual formatting can confuse the AI. You can edit the amounts on the OCR Import before creating the PI. |
| Confidence score is red/low | The PDF might be a scan, image-based, or have unusual formatting. Review all fields carefully before creating the PI. |
| "Upload PDF" button not showing | Make sure you're on a **new** (unsaved) OCR Import record. The button only appears on new records. |
