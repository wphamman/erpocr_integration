import { BrowserRouter, Route, Routes } from "react-router-dom";
import { FrappeProvider, useFrappeAuth } from "frappe-react-sdk";
import Login from "@/components/Login";
import OutstandingWork from "@/pages/OutstandingWork";
import QueueList from "@/pages/QueueList";
import { ROUTER_BASENAME } from "@/lib/router";

function Gate() {
	const { currentUser, isLoading } = useFrappeAuth();

	if (isLoading) {
		return <CenteredMessage text="Loading…" />;
	}

	if (!currentUser) {
		return <Login />;
	}

	return (
		<Routes>
			<Route path="/" element={<OutstandingWork />} />
			<Route path="/q/:slug/:status" element={<QueueList />} />
			<Route path="*" element={<OutstandingWork />} />
		</Routes>
	);
}

function CenteredMessage({ text }: { text: string }) {
	return (
		<div className="flex min-h-screen items-center justify-center bg-slate-50 text-slate-500">
			{text}
		</div>
	);
}

export default function App() {
	// enableSocket={false}: this dashboard is request/response only — no realtime.
	// Leaving it on makes frappe-react-sdk hammer a socket.io port that isn't
	// exposed through the dev proxy (noisy ERR_CONNECTION_REFUSED retries).
	return (
		<FrappeProvider enableSocket={false}>
			<BrowserRouter basename={ROUTER_BASENAME}>
				<Gate />
			</BrowserRouter>
		</FrappeProvider>
	);
}
