import { NextRequest, NextResponse } from "next/server";

import { classifyBffRoute } from "@/lib/api/bff-route-policy";
import { validateSessionFromRequest } from "@/lib/api/bff-session-auth";
import { SESSION_COOKIE_NAME } from "@/lib/auth/session-constants";
import { newRequestId, signPrincipalForBackend } from "@/lib/api/signed-principal";

const DEFAULT_TIMEOUT_MS = Number(process.env.AIHUB_TIMEOUT_MS || "120000");

/** Longer routes (chat turn / image gen) must not be shorter than backend work. */
function timeoutMsForPath(logicalPath: string): number {
    const base = DEFAULT_TIMEOUT_MS;
    if (
        logicalPath === "/chat/turn" ||
        logicalPath.startsWith("/chat/turn?") ||
        logicalPath.startsWith("/chat/file/")
    ) {
        const turnMs = Number(process.env.AIHUB_CHAT_TURN_TIMEOUT_MS || "");
        if (Number.isFinite(turnMs) && turnMs > 0) {
            return Math.max(base, turnMs);
        }
        // Align with aihub.config AIHUB_REQUEST_TIMEOUT_S default (120s).
        return Math.max(base, 120_000);
    }
    return base;
}
const HOP_BY_HOP_HEADERS = new Set([
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
]);

function backendBaseUrl(): string {
    return (process.env.AIHUB_BASE_URL || "http://127.0.0.1:8080").replace(
        /\/+$/,
        "",
    );
}

function buildTargetPath(path: string[]): string {
    if (!Array.isArray(path) || path.length === 0) return "/";
    return `/${path.map((segment) => encodeURIComponent(segment)).join("/")}`;
}

function logicalPathnameFromSegments(segments: string[]): string {
    if (!Array.isArray(segments) || segments.length === 0) {
        return "/";
    }
    return `/${segments.join("/")}`;
}

function buildForwardHeaders(
    req: NextRequest,
    canHaveBody: boolean,
    opts: {
        routeClass: "public" | "user" | "admin";
        session: Awaited<ReturnType<typeof validateSessionFromRequest>>;
        targetPath: string;
        method: string;
    },
): Headers {
    const headers = new Headers();
    for (const [name, value] of req.headers.entries()) {
        const key = name.toLowerCase();
        if (HOP_BY_HOP_HEADERS.has(key)) continue;
        if (
            key === "x-aihub-api-key-override" ||
            key === "x-api-key" ||
            key === "authorization" ||
            key === "x-aihub-proxy-token" ||
            key === "x-user-id" ||
            key.startsWith("x-aihub-principal")
        ) {
            continue;
        }
        headers.set(name, value);
    }

    if (!headers.has("accept")) {
        headers.set("accept", "application/json");
    }

    const requestId =
        headers.get("x-request-id") ||
        headers.get("x-correlation-id") ||
        newRequestId();
    headers.set("x-request-id", requestId);
    headers.set("x-correlation-id", requestId);

    if (canHaveBody && !headers.has("content-type")) {
        headers.set("content-type", "application/json");
    }

    if (opts.routeClass === "public") {
        return headers;
    }

    if (!opts.session) {
        throw new Error("missing session for protected route");
    }

    const signature = signPrincipalForBackend({
        session: opts.session,
        method: opts.method,
        path: opts.targetPath,
        requestId,
    });
    headers.set("x-aihub-principal", signature);

    const sessionToken = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    if (sessionToken) {
        headers.set("cookie", `${SESSION_COOKIE_NAME}=${sessionToken}`);
    }
    if (opts.session.csrfToken && !["GET", "HEAD"].includes(opts.method)) {
        headers.set("x-csrf-token", opts.session.csrfToken);
    }

    return headers;
}

function buildResponseHeaders(source: Headers): Headers {
    const headers = new Headers();
    const passthrough = [
        "content-type",
        "cache-control",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "etag",
        "last-modified",
        "location",
        "vary",
        "www-authenticate",
        "x-request-id",
        "x-correlation-id",
        "x-accel-buffering",
        "set-cookie",
    ];

    for (const name of passthrough) {
        const value = source.get(name);
        if (value) headers.set(name, value);
    }

    if (!headers.has("content-type")) {
        headers.set("content-type", "application/json");
    }
    headers.set("cache-control", "private, no-store, max-age=0");
    return headers;
}

async function forward(req: NextRequest, path: string[]) {
    const targetPath = buildTargetPath(path);
    const query = req.nextUrl.search || "";
    const url = `${backendBaseUrl()}${targetPath}${query}`;
    const method = req.method.toUpperCase();
    const logicalPath = logicalPathnameFromSegments(path);
    const routeClass = classifyBffRoute(method, logicalPath);

    if (routeClass === "deny") {
        return NextResponse.json(
            {
                detail: "route denied by BFF policy",
                ok: false,
                code: "bff_route_denied",
            },
            { status: 403 },
        );
    }

    let session: Awaited<ReturnType<typeof validateSessionFromRequest>> = null;
    if (routeClass === "user" || routeClass === "admin") {
        session = await validateSessionFromRequest(req);
        if (!session) {
            return NextResponse.json(
                { detail: "authentication required", ok: false },
                { status: 401 },
            );
        }
        if (routeClass === "admin" && !session.roles.includes("admin")) {
            return NextResponse.json(
                { detail: "admin role required", ok: false },
                { status: 403 },
            );
        }
    }

    const canHaveBody = !["GET", "HEAD"].includes(method);
    let headers: Headers;
    try {
        headers = buildForwardHeaders(req, canHaveBody, {
            routeClass,
            session,
            targetPath,
            method,
        });
    } catch (error) {
        return NextResponse.json(
            {
                detail: error instanceof Error ? error.message : "auth failure",
                ok: false,
            },
            { status: 401 },
        );
    }

    let body: BodyInit | undefined = undefined;
    if (canHaveBody) {
        const raw = await req.arrayBuffer();
        if (raw.byteLength > 0) {
            body = raw;
        }
    }

    const controller = new AbortController();
    const timeoutMs = timeoutMsForPath(logicalPath);
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, {
            method,
            headers,
            body,
            signal: controller.signal,
            cache: "no-store",
            redirect: "manual",
        });

        return new NextResponse(response.body, {
            status: response.status,
            headers: buildResponseHeaders(response.headers),
        });
    } catch (error) {
        const isTimeout =
            error instanceof DOMException && error.name === "AbortError";
        const message =
            error instanceof Error
                ? error.message
                : "Błąd połączenia z AI-Hub backend";

        return NextResponse.json(
            { detail: message, ok: false },
            { status: isTimeout ? 504 : 502 },
        );
    } finally {
        clearTimeout(timer);
    }
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(req, path);
}

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(req, path);
}

export async function PUT(
    req: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(req, path);
}

export async function PATCH(
    req: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(req, path);
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(req, path);
}
