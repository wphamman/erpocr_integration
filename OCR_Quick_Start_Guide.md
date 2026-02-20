# OCR Invoice Import — Quick Start Guide

**For: Star Pops Team**
**System: erp.starpops.co.za**

---

## How It Works (The Short Version)

You send us a supplier invoice PDF, the system reads it automatically, and creates a **draft Purchase Invoice** in ERPNext for review.

Nothing is posted or submitted automatically — there's always a chance to check the data before it hits the books.

---

## Sending Invoices In

You have two ways to send invoices into the system. Use whichever is easier for you.

### Option A: Google Drive

1. Open the shared **OCR Invoices** folder in Google Drive
2. Drop your invoice PDF(s) into the folder
3. That's it — the system checks the folder every 15 minutes

You can drop multiple PDFs at once. Each one will be processed separately. After processing, the PDF is automatically moved to an archive folder organised by year, month, and supplier name.

### Option B: Email

1. Forward the invoice email (with PDF attached) to the designated OCR email address
2. That's it — the system checks for new emails every hour

The PDF attachment is extracted automatically. You can forward emails with multiple PDF attachments — each one is processed separately.

---

## What Happens Next

After you send in an invoice, the system:

1. **Reads the PDF** using AI — extracts the supplier name, invoice number, date, line items, quantities, amounts, and VAT
2. **Matches the supplier** to an existing supplier in ERPNext
3. **Matches each line item** to existing items in ERPNext
4. **Creates an OCR Import record** in ERPNext with all the extracted data

---

## Reviewing Imports in ERPNext

Once invoices have been processed, someone on the accounts team needs to review them.

### Finding Imports to Review

1. Log in to **erp.starpops.co.za**
2. In the search bar, type **OCR Import** and press Enter
3. You'll see a list of all imports. Filter by **Status = Needs Review** to see the ones that need attention

### Reviewing a Single Import

Open an OCR Import record. You'll see:

- **Supplier (OCR)** — the supplier name the AI read from the invoice
- **Supplier** — the matched ERPNext supplier (may be blank if no match was found)
- **Invoice Number, Date, Amounts** — extracted from the invoice
- **Tax Template** — automatically set based on whether VAT was detected
- **Confidence** — a colour-coded badge showing how confident the AI is:
  - **Green** = high confidence, data is likely correct
  - **Orange** = medium confidence, worth double-checking
  - **Red** = low confidence, check everything carefully
- **Items table** — each line item with description, quantity, rate, and match status

### Confirming the Supplier

- If the **Supplier** field is already filled and correct:
  1. Change **Supplier Match Status** to **Confirmed**
  2. Click **Save**

- If the **Supplier** field is blank or wrong:
  1. Click the **Supplier** field and search for the correct supplier
  2. Change **Supplier Match Status** to **Confirmed**
  3. Click **Save**
  4. The system remembers this — next time it will match automatically

### Confirming Line Items

Look at the **Match Status** column in the Items table:

| Match Status | What It Means | What to Do |
|---|---|---|
| **Auto Matched** | System found the item | Just verify it's correct |
| **Suggested** | System found a close match | Check carefully — it might be wrong |
| **Unmatched** | No match found | You need to select the item manually |

For unmatched or incorrect items:
1. Click the **Item Code** field in that row
2. Search for and select the correct item
3. For service/expense items (not stock), also set the **Expense Account**
4. Change **Match Status** to **Confirmed**
5. Click **Save**

### Document Creation (Purchase Invoice or Purchase Receipt)

The system auto-detects whether to create a **Purchase Invoice** (for services and expenses) or a **Purchase Receipt** (for stock items arriving at the warehouse). You can change this in the **Document Type** field before creating the document.

**Automatic:** When the system matches everything automatically (supplier + all items), a draft is created straight away — no action needed from you. The status jumps to **Completed**.

**Manual (after review):** If you had to confirm or fix matches, click **Actions > Create Purchase Invoice** (or **Create Purchase Receipt**) after saving. A draft is created and the status changes to **Completed**.

Either way, the document is saved as a **draft** — open it, check the details, and submit it when you're happy.

---

## The System Learns

Every time you confirm a supplier or item match, the system remembers it. Next time an invoice comes in from the same supplier with the same items, everything matches automatically.

The more invoices you process, the less manual work is needed.

---

## Statuses at a Glance

| Status | Meaning | What to Do |
|---|---|---|
| **Pending** | Waiting to be processed | Nothing — it starts automatically |
| **Needs Review** | Data extracted, needs checking | Review supplier and items, then confirm |
| **Matched** | Everything matched, document auto-created | Check the draft PI/PR |
| **Completed** | Purchase Invoice or Purchase Receipt created | All done |
| **Error** | Something went wrong | Let your administrator know |

---

## Common Questions

**How long does processing take?**
Usually 5–30 seconds after the system picks up the file. Remember: Drive is checked every 15 minutes, email every hour.

**Can I send a statement with multiple invoices on one PDF?**
Yes. The system detects multiple invoices and creates a separate record for each one.

**What if the supplier doesn't exist in ERPNext yet?**
The import will show the supplier as "Unmatched". Ask your administrator to create the supplier, then come back and select it.

**What if the amounts look wrong?**
You can edit the amounts directly on the OCR Import record before creating the Purchase Invoice. Some unusual PDF formats can confuse the AI.

**What file types are supported?**
PDF only. Maximum file size is 10 MB.

**The original PDF — where does it go?**
If you used Google Drive, the PDF is moved to the archive folder (organised by year/month/supplier). There's a "View Original Invoice" link on each OCR Import record. If you used email, the original stays in your email.
