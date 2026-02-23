# OCR Invoice Import — Accountant Guide

**For: Accounting team members who review imports and create documents**

---

## What This Does

The OCR Import tool reads supplier invoices (PDF or image) using AI and creates records in ERPNext with the extracted data. You review the data, confirm matches, and then create the appropriate document:

- **Purchase Invoice** — for service invoices, subscriptions, stock purchases
- **Purchase Receipt** — for receiving physical stock items into the warehouse
- **Journal Entry** — for expense receipts (restaurant bills, tolls, entertainment)

**Important:** The system only creates *drafts*. Nothing is posted or submitted automatically. You always review before anything hits the books.

---

## Part 1: Installation & Setup

### Installing the App

```bash
bench get-app https://github.com/wphamman/erpocr_integration
bench --site <site> install-app erpocr_integration
bench --site <site> migrate
bench restart
```

### Configuring OCR Settings

1. In the search bar, type **OCR Settings** and open it
2. Fill in the required settings:

**Gemini API**

| Field | What to Enter |
|---|---|
| **Gemini API Key** | Get from https://aistudio.google.com/apikey (starts with `AIza...`) |
| **Gemini Model** | Select **gemini-2.5-flash** (recommended — fast and accurate) |

**ERPNext Defaults**

| Field | What to Enter |
|---|---|
| **Default Company** | Your company |
| **Default Warehouse** | Main receiving warehouse (e.g. "Stores - SP") |
| **Default Expense Account** | General expense account for service items |
| **Default Cost Center** | Main cost center |
| **Default Item** | A non-stock item for unmatched line items (optional — OCR description becomes the item description) |
| **Default Credit Account** | Default credit account for Journal Entries (e.g. Accounts Payable, Petty Cash, Bank) |

**Tax Templates**

| Field | What to Enter |
|---|---|
| **VAT Tax Template** | Your VAT purchase tax template (applied when tax is detected on the invoice) |
| **Non-VAT Tax Template** | Your non-VAT template (applied when no tax is detected) |

**Matching**

| Field | What to Enter |
|---|---|
| **Matching Threshold** | Leave at **80** (minimum similarity score for fuzzy matching) |

3. Click **Save**

### Optional: Email Monitoring

1. In OCR Settings, check **Enable Email Monitoring**
2. Select the **Email Account** to monitor (must be an existing ERPNext Email Account)
3. Save

The system will check this email account every hour for invoice attachments (PDF, JPEG, PNG).

### Optional: Google Drive Integration

1. In OCR Settings, check **Enable Drive Integration**
2. Paste your Google Cloud **Service Account JSON** key
3. Set **Archive Folder ID** — the Drive folder where processed files are archived (organised by Year/Month/Supplier)
4. Set **Scan Inbox Folder ID** — the Drive folder where uploaders drop new files (checked every 15 minutes)
5. Save

Make sure the service account has been granted access to both folders.

---

## Part 2: Reviewing Imports

### Finding Imports to Review

1. In the search bar, type **OCR Import** and press Enter
2. Filter by **Status = Needs Review** to see imports that need attention
3. Alternatively, use the **OCR Dashboard** (search "OCR Dashboard") for an overview with status cards

### Understanding the OCR Import Record

When you open an import, you'll see:

| Section | What's There |
|---|---|
| **Header** | Supplier (OCR name + matched ERPNext supplier), invoice number, dates, amounts |
| **Tax Template** | Auto-set based on whether VAT was detected on the invoice |
| **Confidence** | Colour-coded badge: Green (high) / Orange (medium) / Red (low) |
| **Items table** | Each line item with description, qty, rate, amount, and match status |
| **Document Type** | Blank by default — you select this before creating a document |
| **Purchase Order section** | Optional PO/PR linking (see Part 4) |
| **Result section** | Links to created documents (PI/PR/JE) after creation |

### Confirming the Supplier

**If the Supplier field is already filled and correct:**
1. Change **Supplier Match Status** to **Confirmed**
2. Click **Save** — the system saves this as an alias for future auto-matching

**If the Supplier field is blank or wrong:**
1. Click the **Supplier** field and search for the correct supplier
2. Change **Supplier Match Status** to **Confirmed**
3. Click **Save** — the alias is saved for next time

**If the supplier doesn't exist in ERPNext yet:**
1. Create the supplier first (Buying > Supplier > New)
2. Come back to the OCR Import and select it

### Confirming Line Items

Look at the **Match Status** column in the Items table:

| Match Status | What It Means | What to Do |
|---|---|---|
| **Auto Matched** | System found the item via alias or exact match | Verify it's correct |
| **Suggested** | System found a close fuzzy match | Check carefully — might be wrong |
| **Service Mapped** | Matched via service mapping pattern | Verify the item, expense account, and cost center |
| **Unmatched** | No match found | Select the item manually |

For unmatched or incorrect items:
1. Click the **Item Code** field in that row
2. Search for and select the correct item
3. For service/expense items (not stock), also set the **Expense Account** and optionally the **Cost Center**
4. Change **Match Status** to **Confirmed**
5. Click **Save**

---

## Part 3: Creating Documents

### Step 1 — Select a Document Type

The **Document Type** field is blank by default. You must select one before creating a document:

| Document Type | When to Use |
|---|---|
| **Purchase Invoice** | Most invoices — services, subscriptions, stock purchases with or without PO |
| **Purchase Receipt** | Receiving physical stock items into the warehouse (all items must be stock items) |
| **Journal Entry** | Expense receipts — restaurant bills, toll slips, entertainment, petty cash |

### Step 2 — Create the Document

After selecting a document type and confirming your matches, click the appropriate button under **Actions**:

- **Create Purchase Invoice** — appears when Document Type = "Purchase Invoice"
- **Create Purchase Receipt** — appears when Document Type = "Purchase Receipt" and status = "Matched"
- **Create Journal Entry** — appears when Document Type = "Journal Entry"

The system creates a **draft** document. The OCR Import status changes to **Completed** and a link to the created document appears in the Result section.

### Step 3 — Review and Submit the Draft

Open the created document (click the link in the Result section) and:
1. Verify all details are correct
2. Make any needed adjustments
3. Submit the document when ready

### Journal Entry — Additional Steps

When creating a Journal Entry:
1. Set Document Type to **Journal Entry**
2. The **Credit Account** field appears — set it to the appropriate account (e.g. Accounts Payable, Petty Cash, Bank). The system auto-fills this from OCR Settings if configured.
3. Ensure each item row has an **Expense Account** set
4. Click **Create Journal Entry**

The JE will have:
- A **debit line** for each item's expense account
- A separate **debit line** for VAT (if tax was detected)
- A single **credit line** to the credit account for the total

---

## Part 4: Purchase Order Linking (Optional)

If the invoice relates to an existing Purchase Order, you can link them before creating the Purchase Invoice. This closes the PO→PR→PI chain in ERPNext.

### Linking a Purchase Order

1. Make sure the **Supplier** field is set
2. Click **Actions > Find Open POs** — a dialog shows open POs for this supplier
3. Select the relevant PO
4. Click **Actions > Match PO Items** — the system auto-matches OCR items to PO items by item code
5. Review the matches in the dialog (see quantities and rates side by side)
6. Click **Apply Matches** to write the PO references to the items

### Linking a Purchase Receipt

If the PO already has a Purchase Receipt (goods already received):

1. After linking a PO, the **Purchase Receipt** field appears (under Purchase Order)
2. Select the PR — only PRs created against the selected PO are shown
3. The system auto-matches PR items and populates PR references on the items

When you then create a Purchase Invoice, the PI items will include both PO references (marks the PO as billed) and PR references (marks the PR as billed).

### When PO/PR Linking Matters

| Scenario | Link PO? | Link PR? |
|---|---|---|
| Invoice for goods received with PO and delivery note | Yes | Yes |
| Invoice for services with PO (no delivery) | Yes | No |
| Invoice without any PO | No | No |

---

## Part 5: The System Learns

Every time you confirm a supplier or item match, the system remembers it:

- **Supplier aliases** — OCR text → ERPNext supplier (e.g. "Star Pops ( Pty ) Ltd" → "Star Pops")
- **Item aliases** — OCR text → ERPNext item
- **Service mappings** — pattern-based rules for recurring services (can be set up in OCR Service Mapping)

The more invoices you process, the less manual work is needed. After a few invoices from the same supplier, most imports will be fully auto-matched.

---

## Status Reference

| Status | Meaning | Action Needed |
|---|---|---|
| **Pending** | Just uploaded, waiting to be processed | Wait — processing starts automatically |
| **Needs Review** | Data extracted, supplier or items need checking | Review and confirm matches, then create document |
| **Matched** | All suppliers and items auto-matched | Select document type and create the document |
| **Completed** | Document (PI/PR/JE) created | All done — review and submit the draft |
| **Error** | Something went wrong during extraction | Check details, try re-uploading, or ask admin |

---

## Where to Find Things

| What | Where |
|---|---|
| Upload a new invoice | OCR Import > New > Actions > Upload File |
| See all imports | OCR Import list (filter by status) |
| Review pending imports | OCR Import list > filter: Status = Needs Review |
| Created Purchase Invoices | Click the **Purchase Invoice** link on any Completed OCR Import |
| Created Purchase Receipts | Click the **Purchase Receipt** link on any Completed OCR Import |
| Created Journal Entries | Click the **Journal Entry** link on any Completed OCR Import |
| Supplier aliases | OCR Supplier Alias list |
| Item aliases | OCR Item Alias list |
| Service mappings | OCR Service Mapping list |
| Error logs | Error Log (filter by "OCR") |
| Settings | Search "OCR Settings" in the search bar |
| Dashboard | Search "OCR Dashboard" in the search bar |

---

## Troubleshooting

| Problem | What to Do |
|---|---|
| Status stuck on "Pending" for more than 5 minutes | The background job may have failed. Check Error Log, or try uploading again. |
| Supplier not matching | The supplier name on the invoice may differ from ERPNext (e.g. "ABC Pty Ltd" vs "ABC (Pty) Ltd"). Select manually and confirm — next time it will match. |
| Items not matching | Item descriptions on invoices often differ from ERPNext item names. Confirm once and it learns. |
| Wrong amounts extracted | Unusual PDF formatting can confuse the AI. Edit the amounts on the OCR Import before creating the document. |
| Low confidence (red badge) | The file might be blurry, image-based, or have unusual formatting. Review all fields carefully. |
| "Upload File" button not showing | The button only appears on **new** (unsaved) OCR Import records. |
| "Create" button not showing | Make sure you've selected a **Document Type** first. For Purchase Receipt, status must be "Matched" (all items resolved). |
| Journal Entry won't create | Check that all items have an Expense Account set and a Credit Account is specified. |
| Can't find PRs for my PO | Only submitted Purchase Receipts with items linked to the selected PO are shown. |
