// Dev proxy: forward Frappe API/asset/file calls to the dev backend.
//
// changeOrigin so the upstream sees its own Host header (Frappe resolves the
// site that way). The browser only ever talks to whatever fronts the SPA —
// localhost during dev, a Cloudflare tunnel hostname during user testing —
// so cookies stay first-party to that hostname. Empty cookieDomainRewrite
// strips the Domain attribute from Set-Cookie, which makes the cookie
// implicitly scoped to the request hostname (works on localhost AND on any
// tunneled domain without any further config).
//
// Override the upstream with VITE_FRAPPE_TARGET (default: erp-dev UAT).
const target = process.env.VITE_FRAPPE_TARGET || "https://erp-dev.starpops.co.za";

const routes = ["/api", "/assets", "/files", "/private", "/app"];

export default Object.fromEntries(
	routes.map((route) => [
		route,
		{
			target,
			changeOrigin: true,
			secure: true,
			cookieDomainRewrite: "",
		},
	]),
);
