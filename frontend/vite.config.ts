import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import proxyOptions from "./proxyOptions";

// https://vite.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		port: 5173,
		host: "0.0.0.0",
		proxy: proxyOptions,
		// Allow Cloudflare quick tunnels (random *.trycloudflare.com hostnames)
		// to reach the dev server during user testing. Vite 6 blocks unknown
		// Host headers by default.
		allowedHosts: [".trycloudflare.com", ".starpops.co.za"],
	},
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
		},
	},
	build: {
		// Built assets are served by Frappe from
		// /assets/erpocr_integration/accounts/ once the app is installed.
		outDir: "../erpocr_integration/public/accounts",
		emptyOutDir: true,
		// No source map: the built dist is committed to the repo (managed-host
		// deploy runs no Node step), so we don't ship a ~1.8 MB .js.map per
		// release. Mirrors how fleet_management commits its dashboard dist.
		sourcemap: false,
	},
});
