# OCR Fleet Slip Scanning — Guide for Drivers

**For: Anyone who fills up fuel or passes through toll plazas with a company vehicle**

---

## How It Works

After every fuel stop or toll, you scan or photograph the slip and drop it into a shared Google Drive folder. The system reads it automatically using AI, identifies the vehicle, extracts the amounts and fuel/toll details, and creates a record in ERPNext for the accounting team to process.

Nothing is posted or submitted automatically — the accounting team always reviews the data before creating any documents.

---

## What to Scan

- **Fuel slips** — the receipt you get after filling up (petrol, diesel)
- **Toll slips** — the receipt from toll plazas (e-toll slips, manual toll receipts)

Scan your slip as soon as possible after the transaction while it's still legible. Thermal paper (the shiny receipts from fuel stations) fades quickly.

---

## Accepted File Types

- **PDF** — scanned slips
- **JPEG** (.jpg) — photos taken with a phone camera
- **PNG** — screenshots or scanned images

Maximum file size: **10 MB**

**One slip per file.** Each scan should contain a single fuel or toll transaction.

---

## How to Submit a Fleet Slip

### Step 1: Photograph the Slip

- **Best option:** Use the "Scan" feature in Google Drive (phone app) — this creates a clean, well-lit PDF
- **Also fine:** Take a regular photo with your phone camera (JPEG)
- Make sure the full slip is visible, especially:
  - **Total amount**
  - **Date**
  - **Vehicle registration number** (if printed on the slip)
  - **Litres and price per litre** (for fuel)
  - **Toll plaza name** (for tolls)

### Step 2: Drop the File in Google Drive

1. Open the shared **OCR Fleet Slips** folder in Google Drive
2. Drop your file into the folder
3. That's it — the system checks the folder every 15 minutes

After processing, the file is automatically moved to an archive folder.

#### Adding the Folder to Your Phone Home Screen

So you don't have to navigate through Drive every time:

**Android:**
1. Open the **Google Drive** app
2. Navigate to the shared **OCR Fleet Slips** folder
3. You should now be inside the folder (you'll see its contents, or it will be empty)
4. Tap the **three dots** menu in the top-right corner of the screen
5. Tap **Add to Home screen**
6. A shortcut icon appears on your home screen — tap it to go straight to the folder

**iPhone / iPad:**
1. Open the **Google Drive** app
2. Navigate to the shared **OCR Fleet Slips** folder
3. You should now be inside the folder
4. Tap the **three dots** menu in the top-right corner of the screen
5. Tap **Add to Home Screen** (on newer iOS) or **Copy link**, then open Safari, paste the link, tap the **Share** button, and tap **Add to Home Screen**
6. Name it something short like "Fleet Slips" and tap **Add**

Now you can scan a slip, tap the home screen shortcut, and upload — all in a few seconds.

---

## Tips for Good Scans

- **Scan promptly** — thermal paper fades within days or weeks, especially in hot vehicles
- **Flat and legible** — smooth out crumpled slips before scanning
- **Full slip visible** — include all edges, especially the total and date
- **Good lighting** — avoid shadows and glare
- **One slip per photo** — don't combine multiple slips in one image
- **Use "Scan" if you can** — the scan feature in the Google Drive app automatically crops, straightens, and enhances the image

---

## What Happens After You Drop the File

The system:

1. **Reads the slip** using AI — identifies whether it's fuel, toll, or something else
2. **Extracts the details** — amount, date, merchant name, and fuel or toll specifics
3. **Matches the vehicle** — uses the registration number (if visible) to link to the correct vehicle in ERPNext
4. **Sets up the posting** — determines the supplier and expense account based on the vehicle's configuration
5. **Creates an OCR Fleet Slip record** in ERPNext for the accounting team

The accounting team then reviews the data and creates a Purchase Invoice from it.

---

## What Gets Extracted

**For fuel slips:**
- Merchant name (e.g., Shell, Engen, Sasol)
- Total amount and VAT
- Litres filled
- Price per litre
- Fuel type (diesel, 95, 93, etc.)
- Odometer reading (if printed)

**For toll slips:**
- Toll plaza name
- Route (e.g., N1, N2)
- Total amount

**For all slips:**
- Transaction date
- Vehicle registration number (if visible)

---

## Common Questions

**How long does processing take?**
Usually 5–30 seconds after the system picks up the file. The folder is checked every 15 minutes, so there may be a short wait before processing starts.

**What if the vehicle registration isn't on the slip?**
The system will create the record but mark the vehicle as "Unmatched" and the slip stays in "Needs Review" status. The accounting team must link it to the correct Fleet Vehicle in ERPNext before a Purchase Invoice can be created — this ensures every fuel/toll charge is traceable to a verified vehicle.

**What if I take a blurry photo?**
The AI will try its best, but blurry or faded slips reduce accuracy. The accounting team will see a low confidence score and verify the data. Scan slips as soon as possible — thermal paper fades fast.

**Can I drop multiple slips at once?**
Yes. Each file is processed separately.

**What if I accidentally scan something that isn't a fuel or toll slip?**
The system will flag it as "Other" with an orange warning. The accounting team will review it and mark it as "No Action Required" if it's not a valid business expense (e.g., personal purchases at a fuel station).

**Where does the original file go?**
After processing, the file is moved from the scan folder to an archive folder. There's a "View Original Scan" link on each OCR Fleet Slip record in ERPNext.

**Do I need to write the vehicle registration on the slip?**
Not if it's already printed on the slip (common with fleet card transactions). If you pay with a bank card and the registration isn't on the slip, the accounting team will match it manually — but it helps if you jot the registration on the slip before scanning.
