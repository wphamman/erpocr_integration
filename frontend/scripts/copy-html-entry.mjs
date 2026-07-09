// After `vite build`, copy the built index.html into the Frappe app's www/
// folder as accounts.html, so Frappe serves the SPA shell at the /accounts
// route (paired with website_route_rules in hooks.py). Mirrors Mint/Raven.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = resolve(__dirname, "../../erpocr_integration/public/accounts/index.html");
const dest = resolve(__dirname, "../../erpocr_integration/www/accounts.html");

mkdirSync(dirname(dest), { recursive: true });
copyFileSync(src, dest);
console.log(`Copied SPA entry → ${dest}`);
