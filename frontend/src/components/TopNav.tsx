import { Link } from "react-router-dom";
import { useFrappeAuth } from "frappe-react-sdk";

export default function TopNav({
	title,
	subtitle,
	backTo,
}: {
	title: string;
	subtitle?: string;
	backTo?: string;
}) {
	const { currentUser, logout } = useFrappeAuth();

	return (
		<header className="border-b border-slate-200 bg-white">
			<div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
				<div className="flex items-center gap-3">
					{backTo && (
						<Link
							to={backTo}
							className="rounded-md border border-slate-200 px-2 py-1 text-sm text-slate-600 hover:bg-slate-50"
							aria-label="Back"
						>
							←
						</Link>
					)}
					<div>
						<h1 className="text-base font-semibold text-slate-900">{title}</h1>
						{subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
					</div>
				</div>
				<div className="flex items-center gap-3 text-sm text-slate-500">
					<span className="hidden sm:inline">{currentUser}</span>
					<button
						onClick={() => logout()}
						className="rounded-md border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100"
					>
						Sign out
					</button>
				</div>
			</div>
		</header>
	);
}
