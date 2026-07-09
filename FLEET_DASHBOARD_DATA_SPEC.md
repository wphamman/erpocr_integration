# Fleet Dashboard — OCR Fleet Slip Data Specification

**Purpose:** This document describes the new data available from the `erpocr_integration` Frappe app (v0.6.0) that the Fleet Dashboard project can consume. It covers the new DocType, its fields, the Custom Fields added to Fleet Vehicle, and how to query the data via the ERPNext REST API.

---

## New DocType: OCR Fleet Slip

Every fuel fill-up or toll transaction scanned by a driver creates one `OCR Fleet Slip` record in ERPNext. Data is extracted automatically from the slip image/PDF using Gemini AI.

**Naming:** `OCR-FS-00001`, `OCR-FS-00002`, etc.

### Status Workflow

```
Pending → Needs Review → Matched → Draft Created → Completed
                                                  → No Action
                                                  → Error
```

- **Completed** = a Purchase Invoice has been submitted for this slip
- **Draft Created** = a PI draft exists but hasn't been submitted yet
- **No Action** = marked as not requiring processing (e.g., personal purchase)

### Fields Available

#### Core / Header

| Field | Type | Description |
|---|---|---|
| `name` | String | Record ID (e.g., `OCR-FS-00042`) |
| `status` | Select | `Pending`, `Needs Review`, `Matched`, `Draft Created`, `Completed`, `No Action`, `Error` |
| `slip_type` | Select | `Fuel`, `Toll`, or `Other` |
| `unauthorized_flag` | Check (0/1) | Set when `slip_type = Other` — flags non-fuel/toll purchases |
| `company` | Link → Company | |
| `merchant_name_ocr` | Data | Extracted merchant/station name (e.g., "Shell Rivonia", "SANRAL N1 Toll") |
| `transaction_date` | Date | Date of the transaction (YYYY-MM-DD) |
| `total_amount` | Currency | Total amount on the slip (ZAR) |
| `vat_amount` | Currency | VAT portion (0 for diesel, which is VAT-exempt) |
| `currency` | Data | Currency code (typically "ZAR") |
| `confidence` | Percent (0-100) | AI extraction confidence score |
| `description` | Small Text | Full description extracted from slip |

#### Vehicle

| Field | Type | Description |
|---|---|---|
| `vehicle_registration` | Data | Number plate extracted from slip by AI |
| `fleet_vehicle` | Link → Fleet Vehicle | Matched vehicle record (requires fleet_management app) |
| `vehicle_match_status` | Select | `Auto Matched`, `Suggested`, `Unmatched`, `Confirmed` |
| `posting_mode` | Select | `Fleet Card` or `Direct Expense` (auto-set from vehicle config) |
| `fleet_card_supplier` | Link → Supplier | Supplier for the PI (fleet card provider or default supplier) |
| `expense_account` | Link → Account | Expense account (Direct Expense slips). **Blank on Fleet Card slips since v1.8.0 (Q6)** — no PI is created on that path, so the control account is no longer captured per-slip; read it from `Fleet Vehicle.custom_fleet_control_account` if needed |
| `cost_center` | Link → Cost Center | From vehicle config or manually set |

#### Fuel-Specific (only populated when `slip_type = Fuel`)

| Field | Type | Description |
|---|---|---|
| `litres` | Float (2 decimal) | Litres filled |
| `price_per_litre` | Currency | Price per litre at time of fill |
| `fuel_type` | Data | "Diesel", "Petrol", "95 Unleaded", "93 Unleaded", etc. |
| `odometer_reading` | Float (1 decimal) | Odometer reading if printed on slip |

#### Toll-Specific (only populated when `slip_type = Toll`)

| Field | Type | Description |
|---|---|---|
| `toll_plaza_name` | Data | Name of the toll plaza (e.g., "Huguenot Tunnel") |
| `route` | Data | Road/route (e.g., "N1", "N2", "R21") |

#### Result / Linked Document

| Field | Type | Description |
|---|---|---|
| `purchase_invoice` | Link → Purchase Invoice | The PI created from this slip (read-only) |
| `no_action_reason` | Small Text | Reason if marked as No Action |

#### Metadata

| Field | Type | Description |
|---|---|---|
| `source_type` | Data | How the slip was ingested (e.g., "Google Drive") |
| `uploaded_by` | Link → User | Who uploaded/submitted the scan |
| `creation` | Datetime | When the record was created |
| `modified` | Datetime | Last modification time |

---

## Custom Fields on Fleet Vehicle

The OCR app adds these custom fields to the `Fleet Vehicle` DocType (from the `fleet_management` app) via fixtures. They configure how each vehicle's fleet slips are posted.

| Custom Field | Type | Description |
|---|---|---|
| `custom_fleet_card_provider` | Link → Supplier | Fleet card company (e.g., WesBank). **If set**, fleet slips for this vehicle create PIs against this supplier. **If blank**, the default supplier from OCR Settings is used. |
| `custom_fleet_control_account` | Link → Account | Control/clearing account debited on fleet card PIs. Used to reconcile against the monthly fleet card statement. |
| `custom_cost_center` | Link → Cost Center | Cost center for expense allocation on this vehicle's fleet slips. |

### Posting Mode Logic

The `posting_mode` on OCR Fleet Slip is determined by the vehicle's configuration:

- **Fleet Card** (vehicle has `custom_fleet_card_provider` set):
  - Supplier = `custom_fleet_card_provider` (e.g., WesBank)
  - Expense account = `custom_fleet_control_account`
  - Used for vehicles with fleet cards — individual transactions accumulate, provider sends monthly consolidated statement

- **Direct Expense** (vehicle has no `custom_fleet_card_provider`):
  - Supplier = `fleet_default_supplier` from OCR Settings (a generic supplier)
  - Expense account = `fleet_expense_account` from OCR Settings
  - Used for vehicles paying with company bank cards

---

## API Access Patterns

All queries use the standard ERPNext REST API with `Authorization: token <api_key>:<api_secret>`.

### List All Fleet Slips

```
GET /api/resource/OCR Fleet Slip?fields=["name","status","slip_type","merchant_name_ocr","transaction_date","total_amount","vat_amount","fleet_vehicle","vehicle_registration","litres","price_per_litre","fuel_type","odometer_reading","toll_plaza_name","route","posting_mode","fleet_card_supplier","cost_center","purchase_invoice","confidence"]&order_by=transaction_date desc&limit_page_length=500
```

### Filter by Status

```
# Only completed (submitted PI exists)
&filters=[["status","=","Completed"]]

# Completed + Draft Created (PI exists, may or may not be submitted)
&filters=[["status","in",["Completed","Draft Created"]]]
```

### Filter by Slip Type

```
# Fuel only
&filters=[["slip_type","=","Fuel"]]

# Toll only
&filters=[["slip_type","=","Toll"]]

# Fuel + Toll (exclude Other/unauthorized)
&filters=[["slip_type","in",["Fuel","Toll"]]]
```

### Filter by Vehicle

```
# Specific vehicle
&filters=[["fleet_vehicle","=","FLEET-VEH-00003"]]

# By registration (Data field, use exact match or like)
&filters=[["vehicle_registration","like","%ABC%"]]
```

### Filter by Date Range

```
&filters=[["transaction_date",">=","2026-01-01"],["transaction_date","<=","2026-03-31"]]
```

### Filter by Posting Mode

```
# Fleet card transactions only
&filters=[["posting_mode","=","Fleet Card"]]

# Direct expense only
&filters=[["posting_mode","=","Direct Expense"]]
```

### Combined Example: Fuel Costs Per Vehicle This Month

```
GET /api/resource/OCR Fleet Slip?fields=["fleet_vehicle","vehicle_registration","sum(total_amount) as total_fuel","sum(litres) as total_litres","count(name) as fill_count"]&filters=[["slip_type","=","Fuel"],["status","in",["Completed","Draft Created","Matched","Needs Review"]],["transaction_date",">=","2026-03-01"]]&group_by=fleet_vehicle&order_by=total_fuel desc&limit_page_length=0
```

> **Note:** ERPNext supports `sum()`, `count()`, `avg()` in the `fields` parameter with `group_by` for aggregate queries.

### Get a Single Fleet Slip (Full Detail)

```
GET /api/resource/OCR Fleet Slip/OCR-FS-00042
```

Returns all fields including `raw_payload` (the original Gemini extraction JSON).

### Get Fleet Vehicle Custom Fields

```
GET /api/resource/Fleet Vehicle/FLEET-VEH-00003
```

The response includes the custom fields: `custom_fleet_card_provider`, `custom_fleet_control_account`, `custom_cost_center`.

---

## Dashboard Use Cases

### 1. Fuel Spend Summary

Query fuel slips grouped by vehicle and/or date range. Key fields:
- `total_amount` — cost of each fill
- `litres` — volume filled
- `price_per_litre` — unit price (allows tracking price trends)
- `fuel_type` — diesel vs petrol breakdown
- `fleet_vehicle` / `vehicle_registration` — per-vehicle breakdown
- `cost_center` — per-department/division breakdown

### 2. Toll Spend Summary

Query toll slips. Key fields:
- `total_amount` — toll cost
- `toll_plaza_name` — which tolls are being used
- `route` — which routes (N1, N2, etc.)
- `fleet_vehicle` — per-vehicle breakdown

### 3. Fuel Efficiency / Consumption Tracking

For vehicles that report odometer readings:
- `odometer_reading` — track mileage between fills
- `litres` — fuel consumed
- Calculate km/litre or litres/100km between consecutive fills for the same vehicle
- **Note:** Odometer is only available if printed on the slip (common with fleet card transactions, less common with bank card payments)

### 4. Unauthorized Purchase Monitoring

- Filter: `unauthorized_flag = 1` or `slip_type = "Other"`
- Shows non-fuel/toll purchases made at fuel stations
- `description` field contains what was purchased (e.g., "Doritos, Coca-Cola")
- Cross-reference with `fleet_vehicle` to identify which vehicle/driver

### 5. Fleet Card vs Direct Expense Breakdown

- Group by `posting_mode` to see proportion of fleet card vs bank card spend
- Fleet card transactions: supplier = fleet card provider (e.g., WesBank)
- Direct expense transactions: supplier = generic/default supplier

### 6. Processing Pipeline Status

- Group by `status` to show how many slips are pending review, matched, completed, etc.
- Useful for monitoring the accounting team's backlog
- `confidence` score indicates extraction quality — low confidence = needs manual verification

---

## Important Notes

1. **OCR Fleet Slip is NOT a child table** — it's a standalone DocType, fully queryable via `/api/resource/OCR Fleet Slip`. No need to fetch a parent document.

2. **Fleet Vehicle link may be empty** — if the registration wasn't on the slip or couldn't be matched. In this case `vehicle_registration` (raw text) may still have a value.

3. **Odometer readings are opportunistic** — only present if the slip printer included them. Don't assume every fuel slip has an odometer reading. Check for `odometer_reading > 0` before using.

4. **`total_amount` is always in ZAR** — the currency field is informational; all amounts are stored in the company's default currency.

5. **Status matters for reporting** — for financial reports, filter on `Completed` (PI submitted). For operational dashboards (how much fuel was used), you may want to include `Matched` and `Draft Created` too, since the extracted data is valid even before the PI is finalized.

6. **Pagination** — Use `limit_page_length` and `limit_start` for large datasets. Default page size is 20. Set `limit_page_length=0` for all records (use with caution on large datasets).

7. **Permissions** — API access requires the user to have read permission on `OCR Fleet Slip` (System Manager or OCR Manager role).
