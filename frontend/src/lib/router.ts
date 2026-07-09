// The SPA is served at the host root in dev (localhost:5174/) and at /accounts
// in production (Frappe website_route_rules in hooks.py). The BrowserRouter
// basename AND any full-page redirect (post-login) must use this, so navigation
// stays inside the app instead of hitting the site root — where /q/... has no
// route and Frappe 404s.
export const ROUTER_BASENAME = import.meta.env.DEV ? "/" : "/accounts";
