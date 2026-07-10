/**
 * Cockpit → AI-Hub proxy allowlist gate (method + path templates with `{param}` segments).
 * Allowed routes are defined in cockpit-proxy-allowlist.json (validated against
 * aihub/canonical_http_surface.py via pytest).
 */

import allowlistJson from "./cockpit-proxy-allowlist.json";

export type CockpitProxyRouteRule = {
    method: string;
    path: string;
};

export type CockpitProxyAllowlistFile = {
    description?: string;
    version: number;
    routes: CockpitProxyRouteRule[];
};

const file = allowlistJson as CockpitProxyAllowlistFile;

const METHODS = new Set([
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
]);

function splitPath(pathname: string): string[] {
    const p = pathname.startsWith("/") ? pathname : `/${pathname}`;
    return p.split("/").filter(Boolean);
}

/**
 * True if `actualPath` matches FastAPI-style `template` (e.g. `/a/{id}/b`).
 */
export function pathMatchesTemplate(
    template: string,
    actualPath: string,
): boolean {
    const ta = splitPath(template);
    const tb = splitPath(actualPath);
    if (ta.length !== tb.length) {
        return false;
    }
    for (let i = 0; i < ta.length; i++) {
        const tok = ta[i]!;
        if (tok.startsWith("{") && tok.endsWith("}")) {
            if (!tb[i] || tb[i]!.length === 0) {
                return false;
            }
            continue;
        }
        if (tok !== tb[i]) {
            return false;
        }
    }
    return true;
}

export function getCockpitProxyAllowlist(): readonly CockpitProxyRouteRule[] {
    return file.routes;
}

export function isCockpitProxyAllowed(
    method: string,
    pathname: string,
): boolean {
    const m = method.toUpperCase();
    if (!METHODS.has(m)) {
        return false;
    }
    const path =
        pathname.length === 0 || pathname === "/"
            ? "/"
            : pathname.startsWith("/")
              ? pathname
              : `/${pathname}`;

    for (const rule of file.routes) {
        if (rule.method.toUpperCase() !== m) {
            continue;
        }
        if (pathMatchesTemplate(rule.path, path)) {
            return true;
        }
    }
    return false;
}

export function cockpitProxyForbiddenDetail(
    method: string,
    pathname: string,
): { detail: string; code: string } {
    return {
        detail:
            "Żądanie odrzucone: ścieżka nie należy do dozwolonej powierzchni cockpit → backend. " +
            "Dodaj wpis w cockpit/lib/api/cockpit-proxy-allowlist.json i zweryfikuj pytest (cockpit proxy allowlist).",
        code: "cockpit_proxy_forbidden",
    };
}
