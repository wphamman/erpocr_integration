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

#### Adding the Folder to Your Phone Home Screen

So you don't have to navigate through Drive every time:

**Android:**
1. Open the **Google Drive** app
2. Navigate to the shared **OCR Invoices** folder
3. You should now be inside the folder (you'll see its contents, or it will be empty)
4. Tap the **three dots (...)** in the top-right corner of the screen
5. Tap **Add to Home screen**
6. A shortcut icon appears on your home screen — tap it to go straight to the folder

**iPhone / iPad:**
1. Open the **Google Drive** app
2. Navigate to the shared **OCR Invoices** folder
3. You should now be inside the folder
4. Tap the **three dots (...)** in the top-right corner of the screen
5. Tap **Add to Home Screen** (on newer iOS) or **Copy link**, then open Safari, paste the link, tap the **Share** button, and tap **Add to Home Screen**
6. Name it something short like "OCR Invoices" and tap **Add**

Now you can scan a slip, tap the home screen shortcut, and upload — all in a few seconds.

### Option B: Email

1. Forward the invoice email (with PDF or image attached) to the designated email address
2. That's it — the system checks for new emails every hour

The attachment is extracted automatically. You can forward emails with multiple attachments — each one is processed separately. Both PDF and image attachments are accepted. A copy of the file is saved on the record, so the accounting team can retry extraction even after the original email is deleted.

### Option C: Manual Upload in ERPNext

1. Log in to ERPNext
2. In the search bar, type **OCR Import** and click **+ Add OCR Import**
3. Click the **Upload File** button in the top toolbar
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
Usually 5–30 seconds after the system picks up the file. Remember: Drive is checked every 15 minutes, email every hour. If you drop many files at once, some may take longer — the system processes them in batches to avoid overloading the AI service.

**Can I send a statement with multiple invoices on one PDF?**
Yes. The system detects multiple invoices and creates a separate record for each one. (This only works with PDFs — a photo of a single invoice creates one record.)

**What if I take a blurry photo?**
The AI will try its best, but blurry or poorly lit photos reduce accuracy. The accounting team will see a low confidence score and check the data more carefully. If possible, use the "Scan" feature in your phone's camera or Google Drive for cleaner results.

**What if the supplier doesn't exist in ERPNext yet?**
The import will still be created — it will just show the supplier as "Unmatched". The accounting team will handle it.

**The original file — where does it go?**
If you used Google Drive, the file is moved to the archive folder (organised by year/month/supplier). There's a "View Original Invoice" link on each OCR Import record. If you used email, the original stays in your email.

**Can I send the same invoice twice?**
The system will process it again, but it automatically detects potential duplicates (matching supplier and invoice number) and shows a warning banner on the record. The accounting team will see this during review. Try to avoid sending duplicates, but if it happens, it will be flagged.
