/**
 * BFF route classification: public, user-scoped, admin-scoped, or deny-by-default.
 */

import {
    getCockpitProxyAllowlist,
    isCockpitProxyAllowed,
    pathMatchesTemplate,
} from "@/lib/api/cockpit-proxy-gate";

export type BffRouteClass = "public" | "user" | "admin" | "deny";

const PUBLIC_EXACT = new Set<string>([
    "GET /system/ping",
    "POST /auth/login",
    "POST /auth/register",
    "GET /auth/registration-status",
    "GET /ops/ready",
    "GET /ops/health",
]);

const ADMIN_TEMPLATES: Array<{ method: string; path: string }> = [
    { method: "GET", path: "/cockpit/schema-health" },
    { method: "POST", path: "/chat/capabilities/execute" },
];

const USER_PREFIXES = [
    "/memory",
    "/psyche",
    "/chat",
    "/sessions",
    "/goals",
    "/planner",
    "/agent",
    "/relations",
    "/procedures",
    "/identity",
    "/reflection",
    "/autobiography",
    "/fs",
    "/snapshot",
    "/web/ingest",
    "/cockpit/",
    "/system/health/",
    "/sse/",
    "/auth/me",
    "/auth/logout",
];

function normalizePath(pathname: string): string {
    if (!pathname || pathname === "/") return "/";
    return pathname.startsWith("/") ? pathname : `/${pathname}`;
}

export function classifyBffRoute(method: string, pathname: string): BffRouteClass {
    const m = method.toUpperCase();
    const path = normalizePath(pathname);
    const key = `${m} ${path}`;

    if (!isCockpitProxyAllowed(m, path)) {
        return "deny";
    }

    if (PUBLIC_EXACT.has(key)) {
        return "public";
    }

    for (const rule of ADMIN_TEMPLATES) {
        if (rule.method === m && pathMatchesTemplate(rule.path, path)) {
            return "admin";
        }
    }

    if (USER_PREFIXES.some((prefix) => path.startsWith(prefix))) {
        return "user";
    }

    if (path.includes("{user_id}") || /\/[a-f0-9-]{8,}\b/i.test(path)) {
        return "user";
    }

    return "deny";
}

export function listKnownProxyRoutes(): readonly { method: string; path: string }[] {
    return getCockpitProxyAllowlist();
}
