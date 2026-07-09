import { Link, Navigate, useParams } from "react-router-dom";
import { useFrappeGetDocList } from "frappe-react-sdk";
import TopNav from "@/components/TopNav";
import {
	ACTIONABLE_STATUSES,
	DESK_SLUG,
	DOCTYPE_BY_SLUG,
	QUEUE_CONFIG,
	STATUS_STYLE,
	type ActionableStatus,
	type FrappeRow,
} from "@/lib/doctypeMeta";

const PAGE_LIMIT = 100;

export default function QueueList() {
	const { slug, status: rawStatus } = useParams<{ slug: string; status: string }>();
	const doctype = slug ? DOCTYPE_BY_SLUG[slug] : undefined;
	const status = rawStatus as ActionableStatus | undefined;

	// Unknown slug / status → bounce to overview (handles typos and stale bookmarks).
	if (!doctype || !status || !ACTIONABLE_STATUSES.includes(status)) {
		return <Navigate to="/" replace />;
	}

	const config = QUEUE_CONFIG[doctype];

	const { data, isLoading, error, mutate } = useFrappeGetDocList<FrappeRow>(doctype, {
		fields: config.fields,
		filters: [["status", "=", status]],
		orderBy: { field: "creation", order: "desc" },
		limit: PAGE_LIMIT,
	});

	const rows = data ?? [];

	return (
		<div className="min-h-screen bg-slate-50">
			<TopNav title={doctype} subtitle={`Status: ${status}`} backTo="/" />
			<main className="mx-auto max-w-5xl space-y-4 px-6 py-6">
				<div className="flex items-center justify-between">
					<div className="flex flex-wrap items-center gap-2">
						{ACTIONABLE_STATUSES.map((s) => (
							<Link
								key={s}
								to={`/q/${DESK_SLUG[doctype]}/${encodeURIComponent(s)}`}
								className={`rounded-md border px-2 py-1 text-xs font-medium ${
									s === status
										? STATUS_STYLE[s]
										: "border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
								}`}
							>
								{s}
							</Link>
						))}
					</div>
					<button
						onClick={() => mutate()}
						className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
					>
						Refresh
					</button>
				</div>

				<div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
					<table className="min-w-full divide-y divide-slate-200 text-sm">
						<thead className="bg-slate-50">
							<tr>
								{config.columns.map((c) => (
									<th
										key={c.key}
										className={`px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 ${c.className ?? ""}`}
									>
										{c.label}
									</th>
								))}
								<th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">
									Open
								</th>
							</tr>
						</thead>
						<tbody className="divide-y divide-slate-100">
							{isLoading && (
								<tr>
									<td
										colSpan={config.columns.length + 1}
										className="px-3 py-6 text-center text-sm text-slate-400"
									>
										Loading…
									</td>
								</tr>
							)}
							{error && !isLoading && (
								<tr>
									<td
										colSpan={config.columns.length + 1}
										className="px-3 py-6 text-center text-sm text-red-600"
									>
										{error.message || "Failed to load."}
									</td>
								</tr>
							)}
							{!isLoading && !error && rows.length === 0 && (
								<tr>
									<td
										colSpan={config.columns.length + 1}
										className="px-3 py-6 text-center text-sm text-slate-400"
									>
										No records in this queue.
									</td>
								</tr>
							)}
							{rows.map((row) => (
								<tr key={String(row.name)} className="hover:bg-slate-50">
									{config.columns.map((c) => (
										<td key={c.key} className={`px-3 py-2 ${c.className ?? ""}`}>
											{c.render(row)}
										</td>
									))}
									<td className="px-3 py-2 text-right">
										<a
											href={`/app/${DESK_SLUG[doctype]}/${encodeURIComponent(String(row.name))}`}
											target="_blank"
											rel="noreferrer"
											className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
											title="Open in ERPNext"
										>
											Open ↗
										</a>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>

				{rows.length === PAGE_LIMIT && (
					<p className="text-xs text-slate-400">
						Showing the most recent {PAGE_LIMIT} records. Use ERPNext desk for the full list.
					</p>
				)}
			</main>
		</div>
	);
}
