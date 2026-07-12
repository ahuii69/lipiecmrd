import { NextRequest, NextResponse } from "next/server";

import { validateSessionFromRequest } from "@/lib/api/bff-session-auth";

const PRIVATE_NO_STORE = "private, no-store, max-age=0";
const PUBLIC_PATHS = new Set(["/login"]);
const PUBLIC_API_PREFIXES = ["/api/aihub/system/ping", "/api/aihub/auth/login"];

export function buildContentSecurityPolicy(nonce: string): string {
    return [
        "default-src 'self'",
        `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
    ].join("; ");
}

function isPublicPath(pathname: string): boolean {
    if (PUBLIC_PATHS.has(pathname)) return true;
    return PUBLIC_API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
    const pathname = request.nextUrl.pathname;
    const nonce = btoa(crypto.randomUUID());
    const csp = buildContentSecurityPolicy(nonce);
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-nonce", nonce);
    requestHeaders.set("content-security-policy", csp);

    if (!isPublicPath(pathname)) {
        const session = await validateSessionFromRequest(request);
        if (!session) {
            if (pathname.startsWith("/api/")) {
                return NextResponse.json(
                    { detail: "authentication required", ok: false },
                    { status: 401 },
                );
            }
            const loginUrl = new URL("/login", request.url);
            loginUrl.searchParams.set("next", pathname);
            return NextResponse.redirect(loginUrl);
        }
        if (pathname.startsWith("/admin") && !session.roles.includes("admin")) {
            return NextResponse.redirect(new URL("/", request.url));
        }
        requestHeaders.set("x-aihub-user-id", session.userId);
    }

    const response = NextResponse.next({
        request: { headers: requestHeaders },
    });
    response.headers.set("Content-Security-Policy", csp);
    response.headers.set("Cache-Control", PRIVATE_NO_STORE);
    response.headers.set(
        "Permissions-Policy",
        "camera=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
    );
    response.headers.set("X-Content-Type-Options", "nosniff");
    response.headers.set("X-Frame-Options", "DENY");
    response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    return response;
}

export const config = {
    matcher: [
        {
            source: "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
            missing: [
                { type: "header", key: "next-router-prefetch" },
                { type: "header", key: "purpose", value: "prefetch" },
            ],
        },
    ],
};
