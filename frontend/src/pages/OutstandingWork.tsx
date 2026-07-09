import TopNav from "@/components/TopNav";
import QueueCard from "@/components/QueueCard";
import { DOCTYPES } from "@/lib/doctypeMeta";

export default function OutstandingWork() {
	return (
		<div className="min-h-screen bg-slate-50">
			<TopNav title="Outstanding Work" subtitle="OCR review queues across the accounting pipeline" />
			<main className="mx-auto max-w-5xl space-y-4 px-6 py-6">
				{DOCTYPES.map((doctype) => (
					<QueueCard key={doctype} doctype={doctype} />
				))}
				<p className="pt-2 text-xs text-slate-400">
					Counts are live from ERPNext and reflect your permissions. Click any number to drill into the
					list.
				</p>
			</main>
		</div>
	);
}
