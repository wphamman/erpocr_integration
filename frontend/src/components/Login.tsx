import { useState, type FormEvent } from "react";
import { useFrappeAuth } from "frappe-react-sdk";
import { ROUTER_BASENAME } from "@/lib/router";

export default function Login() {
	const { login, error } = useFrappeAuth();
	const [username, setUsername] = useState("");
	const [password, setPassword] = useState("");
	const [busy, setBusy] = useState(false);

	async function onSubmit(e: FormEvent) {
		e.preventDefault();
		setBusy(true);
		try {
			await login({ username, password });
			// Reload so the next mount of useFrappeAuth() sees the new sid cookie
			// and lands on the dashboard. In-place state-refresh (updateCurrentUser)
			// is unreliable here because the initial guest get_logged_user error
			// stays cached in SWR. The reload is one extra second of a login flash
			// but it's bulletproof; cookie persists across it. Target MUST be the
			// router basename (/accounts in prod) — assigning "/" dumps the user on
			// the Desk root instead of the dashboard.
			window.location.assign(ROUTER_BASENAME);
		} catch {
			setPassword("");
			setBusy(false);
		}
	}

	return (
		<div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
			<form
				onSubmit={onSubmit}
				className="w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-8 shadow-sm"
			>
				<div>
					<h1 className="text-lg font-semibold text-slate-900">Star Pops · Accounts</h1>
					<p className="text-sm text-slate-500">Sign in with your ERPNext account.</p>
				</div>

				<label className="block space-y-1">
					<span className="text-sm font-medium text-slate-700">Email</span>
					<input
						type="text"
						autoComplete="username"
						value={username}
						onChange={(e) => setUsername(e.target.value)}
						className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
						required
					/>
				</label>

				<label className="block space-y-1">
					<span className="text-sm font-medium text-slate-700">Password</span>
					<input
						type="password"
						autoComplete="current-password"
						value={password}
						onChange={(e) => setPassword(e.target.value)}
						className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
						required
					/>
				</label>

				{error && (
					<p className="text-sm text-red-600">{error.message || "Login failed. Check your credentials."}</p>
				)}

				<button
					type="submit"
					disabled={busy}
					className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
				>
					{busy ? "Signing in…" : "Sign in"}
				</button>

				<p className="text-center text-xs text-slate-400">
					Forgot password?{" "}
					<a
						href="/app/login#forgot"
						target="_blank"
						rel="noreferrer"
						className="text-slate-500 underline hover:text-slate-700"
					>
						Reset via ERPNext
					</a>
				</p>
			</form>
		</div>
	);
}
