// Shared per-doctype configuration: URL slugs, status palette, list-view columns.
// Overview and QueueList both source from here so adding a new column or a new
// doctype is a single-file change.

export type DocTypeKey =
	| "OCR Import"
	| "OCR Delivery Note"
	| "OCR Fleet Slip"
	| "OCR Statement";

export const DOCTYPES: DocTypeKey[] = [
	"OCR Import",
	"OCR Delivery Note",
	"OCR Fleet Slip",
	"OCR Statement",
];

// Import, DN, and Fleet retain their existing queues. Statements have their own
// workflow: Reviewed is terminal and deliberately absent from outstanding work.
export const DOCUMENT_ACTIONABLE_STATUSES = [
	"Needs Review",
	"Matched",
	"Draft Created",
	"Error",
] as const;
export const STATEMENT_ACTIONABLE_STATUSES = [
	"Pending",
	"Extracting",
	"Reconciled",
	"Error",
] as const;
export type QueueStatus =
	| (typeof DOCUMENT_ACTIONABLE_STATUSES)[number]
	| (typeof STATEMENT_ACTIONABLE_STATUSES)[number];

export const STATUS_STYLE: Record<QueueStatus, string> = {
	"Needs Review": "text-amber-700 bg-amber-50 border-amber-200",
	Matched: "text-blue-700 bg-blue-50 border-blue-200",
	"Draft Created": "text-slate-700 bg-slate-50 border-slate-200",
	Pending: "text-amber-700 bg-amber-50 border-amber-200",
	Extracting: "text-violet-700 bg-violet-50 border-violet-200",
	Reconciled: "text-emerald-700 bg-emerald-50 border-emerald-200",
	Error: "text-red-700 bg-red-50 border-red-200",
};

// Slug used both for the SPA URL (/q/:slug/:status) and for the Frappe desk
// list URL (/app/:slug). Keeping them aligned means a row's "Open in desk"
// link is just a join of these pieces.
export const DESK_SLUG: Record<DocTypeKey, string> = {
	"OCR Import": "ocr-import",
	"OCR Delivery Note": "ocr-delivery-note",
	"OCR Fleet Slip": "ocr-fleet-slip",
	"OCR Statement": "ocr-statement",
};

// Reverse lookup: URL slug → doctype name.
export const DOCTYPE_BY_SLUG: Record<string, DocTypeKey> = Object.fromEntries(
	(Object.entries(DESK_SLUG) as [DocTypeKey, string][]).map(([dt, slug]) => [slug, dt]),
);

// --- QueueList column configs ---

export type FrappeRow = Record<string, unknown>;

export type Column = {
	key: string;
	label: string;
	className?: string;
	render: (row: FrappeRow) => React.ReactNode;
};

export type QueueConfig = {
	statuses: readonly QueueStatus[];
	// Fields requested from get_list (always includes name, status, creation).
	fields: string[];
	columns: Column[];
};

function formatDate(value: unknown): string {
	if (!value) return "—";
	return String(value).slice(0, 10);
}

function ageDays(creation: unknown): string {
	if (!creation) return "";
	const d = new Date(String(creation));
	const days = Math.max(0, Math.floor((Date.now() - d.getTime()) / 86_400_000));
	return `${days}d`;
}

function formatAmount(amount: unknown, currency: unknown): string {
	const n = typeof amount === "number" ? amount : parseFloat(String(amount ?? "0"));
	if (!Number.isFinite(n) || n === 0) return "—";
	const ccy = currency ? `${currency} ` : "";
	return `${ccy}${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatBalance(amount: unknown, currency: unknown): string {
	if (amount === null || amount === undefined || amount === "") return "—";
	const n = typeof amount === "number" ? amount : parseFloat(String(amount));
	if (!Number.isFinite(n)) return "—";
	const ccy = currency ? `${currency} ` : "";
	return `${ccy}${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function countValue(value: unknown): number {
	const n = typeof value === "number" ? value : Number(value ?? 0);
	return Number.isFinite(n) ? n : 0;
}

// Bold matched supplier; muted OCR'd name as fallback.
function renderSupplier(row: FrappeRow): React.ReactNode {
	if (row.supplier) return String(row.supplier);
	const ocrName = row.supplier_name_ocr;
	return ocrName ? (
		<span className="text-slate-400 italic">{String(ocrName)}</span>
	) : (
		<span className="text-slate-300">—</span>
	);
}

export const QUEUE_CONFIG: Record<DocTypeKey, QueueConfig> = {
	"OCR Import": {
		statuses: DOCUMENT_ACTIONABLE_STATUSES,
		fields: [
			"name",
			"supplier",
			"supplier_name_ocr",
			"invoice_number",
			"invoice_date",
			"total_amount",
			"currency",
			"status",
			"creation",
		],
		columns: [
			{ key: "name", label: "Name", render: (r) => String(r.name ?? "") },
			{ key: "supplier", label: "Supplier", render: renderSupplier },
			{
				key: "invoice_number",
				label: "Invoice #",
				render: (r) => (r.invoice_number ? String(r.invoice_number) : "—"),
			},
			{ key: "invoice_date", label: "Date", render: (r) => formatDate(r.invoice_date) },
			{
				key: "total_amount",
				label: "Amount",
				className: "text-right tabular-nums",
				render: (r) => formatAmount(r.total_amount, r.currency),
			},
			{
				key: "age",
				label: "Age",
				className: "text-right tabular-nums text-slate-500",
				render: (r) => ageDays(r.creation),
			},
		],
	},
	"OCR Delivery Note": {
		statuses: DOCUMENT_ACTIONABLE_STATUSES,
		fields: [
			"name",
			"supplier",
			"supplier_name_ocr",
			"delivery_note_number",
			"delivery_date",
			"vehicle_number",
			"status",
			"creation",
		],
		columns: [
			{ key: "name", label: "Name", render: (r) => String(r.name ?? "") },
			{ key: "supplier", label: "Supplier", render: renderSupplier },
			{
				key: "delivery_note_number",
				label: "DN #",
				render: (r) => (r.delivery_note_number ? String(r.delivery_note_number) : "—"),
			},
			{ key: "delivery_date", label: "Date", render: (r) => formatDate(r.delivery_date) },
			{
				key: "vehicle_number",
				label: "Vehicle",
				render: (r) => (r.vehicle_number ? String(r.vehicle_number) : "—"),
			},
			{
				key: "age",
				label: "Age",
				className: "text-right tabular-nums text-slate-500",
				render: (r) => ageDays(r.creation),
			},
		],
	},
	"OCR Fleet Slip": {
		statuses: DOCUMENT_ACTIONABLE_STATUSES,
		fields: [
			"name",
			"vehicle_registration",
			"fleet_vehicle",
			"slip_type",
			"unauthorized_flag",
			"total_amount",
			"currency",
			"transaction_date",
			"fleet_card_supplier",
			"status",
			"creation",
		],
		columns: [
			{ key: "name", label: "Name", render: (r) => String(r.name ?? "") },
			{
				key: "vehicle",
				label: "Vehicle",
				render: (r) => {
					if (r.fleet_vehicle) return String(r.fleet_vehicle);
					const reg = r.vehicle_registration;
					return reg ? (
						<span className="text-slate-400 italic">{String(reg)}</span>
					) : (
						<span className="text-slate-300">—</span>
					);
				},
			},
			{
				key: "slip_type",
				label: "Type",
				render: (r) => {
					const t = r.slip_type ? String(r.slip_type) : "—";
					if (r.unauthorized_flag) {
						return <span className="font-medium text-orange-600">⚠ {t}</span>;
					}
					return t;
				},
			},
			{
				key: "transaction_date",
				label: "Date",
				render: (r) => formatDate(r.transaction_date),
			},
			{
				key: "total_amount",
				label: "Amount",
				className: "text-right tabular-nums",
				render: (r) => formatAmount(r.total_amount, r.currency),
			},
			{
				key: "fleet_card",
				label: "Card supplier",
				render: (r) =>
					r.fleet_card_supplier ? (
						String(r.fleet_card_supplier)
					) : (
						<span className="text-slate-300">—</span>
					),
			},
			{
				key: "age",
				label: "Age",
				className: "text-right tabular-nums text-slate-500",
				render: (r) => ageDays(r.creation),
			},
		],
	},
	"OCR Statement": {
		statuses: STATEMENT_ACTIONABLE_STATUSES,
		fields: [
			"name",
			"supplier",
			"supplier_name_ocr",
			"statement_date",
			"period_from",
			"period_to",
			"closing_balance",
			"currency",
			"mismatch_count",
			"missing_count",
			"not_in_statement_count",
			"status",
			"creation",
		],
		columns: [
			{ key: "name", label: "Name", render: (r) => String(r.name ?? "") },
			{ key: "supplier", label: "Supplier", render: renderSupplier },
			{ key: "statement_date", label: "Date", render: (r) => formatDate(r.statement_date) },
			{
				key: "period",
				label: "Period",
				render: (r) => {
					const from = formatDate(r.period_from);
					const to = formatDate(r.period_to);
					return from === "—" && to === "—" ? "—" : `${from} – ${to}`;
				},
			},
			{
				key: "closing_balance",
				label: "Closing balance",
				className: "text-right tabular-nums",
				render: (r) => formatBalance(r.closing_balance, r.currency),
			},
			{
				key: "issues",
				label: "Recon issues",
				render: (r) => {
					const mismatch = countValue(r.mismatch_count);
					const missing = countValue(r.missing_count);
					const notInStatement = countValue(r.not_in_statement_count);
					if (mismatch + missing + notInStatement === 0) {
						return <span className="text-emerald-600">None</span>;
					}
					return (
						<span className="text-amber-700">
							{mismatch} mismatch · {missing} missing · {notInStatement} not listed
						</span>
					);
				},
			},
			{
				key: "age",
				label: "Age",
				className: "text-right tabular-nums text-slate-500",
				render: (r) => ageDays(r.creation),
			},
		],
	},
};
