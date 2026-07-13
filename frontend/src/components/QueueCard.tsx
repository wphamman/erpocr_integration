import { Link } from "react-router-dom";
import { useFrappeGetDocCount } from "frappe-react-sdk";
import {
	DESK_SLUG,
	QUEUE_CONFIG,
	STATUS_STYLE,
	type DocTypeKey,
	type QueueStatus,
} from "@/lib/doctypeMeta";

function StatCount({ doctype, status }: { doctype: DocTypeKey; status: QueueStatus }) {
	const { data, isLoading, error } = useFrappeGetDocCount(doctype, [["status", "=", status]]);
	const to = `/q/${DESK_SLUG[doctype]}/${encodeURIComponent(status)}`;

	return (
		<Link
			to={to}
			className={`flex flex-col rounded-lg border px-4 py-3 transition hover:shadow-sm ${STATUS_STYLE[status]}`}
			title={`Open ${doctype} · ${status}`}
		>
			<span className="text-2xl font-semibold tabular-nums">
				{isLoading ? "…" : error ? "—" : (data ?? 0)}
			</span>
			<span className="text-xs font-medium">{status}</span>
		</Link>
	);
}

export default function QueueCard({ doctype }: { doctype: DocTypeKey }) {
	const config = QUEUE_CONFIG[doctype];

	return (
		<section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
			<h2 className="mb-3 text-sm font-semibold text-slate-900">{doctype}</h2>
			<div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
				{config.statuses.map((status) => (
					<StatCount key={status} doctype={doctype} status={status} />
				))}
			</div>
		</section>
	);
}
