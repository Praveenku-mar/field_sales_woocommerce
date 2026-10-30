// The app is served at /fieldsales with the alias /fs (hooks.website_route_rules):
// keep whichever base it was opened under. Shared by the router, the session store
// (login redirects) and anywhere else that must not import the router for it.
export const APP_BASE = /^\/fs(\/|$)/.test(window.location.pathname) ? '/fs' : '/fieldsales'
