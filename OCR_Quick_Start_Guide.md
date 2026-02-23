# OCR Invoice Import — Uploader Guide

**For: Anyone who sends invoices into the system**

---

## How It Works

You send us a supplier invoice (PDF or photo), the system reads it automatically using AI, and creates a record in ERPNext for the accounting team to review.

Nothing is posted or submitted automatically — the accounting team always reviews the data before creating any documents.

---

## Accepted File Types

- **PDF** — scanned or digital invoices (can contain multiple invoices)
- **JPEG** (.jpg) — photos of invoices taken with a phone camera
- **PNG** — screenshots or scanned images

Maximum file size: **10 MB**

---

## Sending Invoices In

You have three ways to send invoices. Use whichever is easiest for you.

### Option A: Google Drive (Recommended for Batch)

1. Open the shared **OCR Invoices** folder in Google Drive
2. Drop your invoice file(s) into the folder (PDF, JPEG, or PNG)
3. That's it — the system checks the folder every 15 minutes

You can drop multiple files at once. Each one is processed separately. After processing, the file is automatically moved to an archive folder organised by year, month, and supplier name.

**Tip:** If you're using a phone, use the "Scan" feature in Google Drive to create a clean PDF. If you accidentally take a regular photo instead, that works too — just drop the JPEG into the folder.

### Option B: Email

1. Forward the invoice email (with PDF or image attached) to the designated email address
2. That's it — the system checks for new emails every hour

The attachment is extracted automatically. You can forward emails with multiple attachments — each one is processed separately. Both PDF and image attachments are accepted.

### Option C: Manual Upload in ERPNext

1. Log in to ERPNext
2. In the search bar, type **OCR Import** and click **+ Add OCR Import**
3. Click **Actions > Upload File** in the top-right
4. Select your file (PDF, JPEG, or PNG — max 10 MB)
5. Wait 5–30 seconds for processing

---

## What Happens After You Send an Invoice

The system:

1. **Reads the file** using AI — extracts the supplier name, invoice number, date, line items, quantities, amounts, and VAT
2. **Matches the supplier** to an existing supplier in ERPNext
3. **Matches each line item** to existing items in ERPNext
4. **Creates an OCR Import record** in ERPNext for the accounting team to review

The accounting team then reviews the data, corrects any mistakes, and creates the appropriate document (Purchase Invoice, Purchase Receipt, or Journal Entry).

---

## Common Questions

**How long does processing take?**
Usually 5–30 seconds after the system picks up the file. Remember: Drive is checked every 15 minutes, email every hour.

**Can I send a statement with multiple invoices on one PDF?**
Yes. The system detects multiple invoices and creates a separate record for each one. (This only works with PDFs — a photo of a single invoice creates one record.)

**What if I take a blurry photo?**
The AI will try its best, but blurry or poorly lit photos reduce accuracy. The accounting team will see a low confidence score and check the data more carefully. If possible, use the "Scan" feature in your phone's camera or Google Drive for cleaner results.

**What if the supplier doesn't exist in ERPNext yet?**
The import will still be created — it will just show the supplier as "Unmatched". The accounting team will handle it.

**The original file — where does it go?**
If you used Google Drive, the file is moved to the archive folder (organised by year/month/supplier). There's a "View Original Invoice" link on each OCR Import record. If you used email, the original stays in your email.

**Can I send the same invoice twice?**
The system will process it again and create a duplicate record. The accounting team will spot this during review. Try to avoid sending duplicates.
