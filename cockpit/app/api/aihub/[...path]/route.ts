import { NextRequest, NextResponse } from "next/server";

import {
    cockpitProxyForbiddenDetail,
    isCockpitProxyAllowed,
} from "@/lib/api/cockpit-proxy-gate";
import {
    loadMordaDotenvFromDisk,
    resolveHubApiKeyFromNextRequest,
} from "@/lib/api/resolve-hub-api-key";

const DEFAULT_TIMEOUT_MS = Number(process.env.AIHUB_TIMEOUT_MS || "45000");
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

/** Decoded pathname for allowlist matching (must match canonical `{user_id}` segments). */
function logicalPathnameFromSegments(segments: string[]): string {
    if (!Array.isArray(segments) || segments.length === 0) {
        return "/";
    }
    return `/${segments.join("/")}`;
}

function buildForwardHeaders(req: NextRequest, canHaveBody: boolean): Headers {
    const headers = new Headers();

    for (const [name, value] of req.headers.entries()) {
        const key = name.toLowerCase();
        if (HOP_BY_HOP_HEADERS.has(key)) continue;
        if (key === "x-aihub-api-key-override") continue;
        headers.set(name, value);
    }

    if (!headers.has("accept")) {
        headers.set("accept", "application/json");
    }

    headers.delete("x-api-key");
    headers.delete("authorization");
    headers.delete("x-aihub-proxy-token");

    const diskEnv = loadMordaDotenvFromDisk();
    const apiKey = resolveHubApiKeyFromNextRequest(req);
    if (apiKey) {
        headers.set("x-api-key", apiKey);
    }

    const proxyTok =
        (process.env.AIHUB_PROXY_TOKEN || "").trim() ||
        (diskEnv ? (diskEnv.AIHUB_PROXY_TOKEN || "").trim() : "") ||
        apiKey;
    if (proxyTok) {
        headers.set("x-aihub-proxy-token", proxyTok);
    }

    if (canHaveBody && !headers.has("content-type")) {
        headers.set("content-type", "application/json");
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
    ];

    for (const name of passthrough) {
        const value = source.get(name);
        if (value) headers.set(name, value);
    }

    // Don't force content-type if already set (preserve text/event-stream for streaming)
    if (!headers.has("content-type")) {
        headers.set("content-type", "application/json");
    }

    return headers;
}

async function forward(req: NextRequest, path: string[]) {
    const targetPath = buildTargetPath(path);
    const query = req.nextUrl.search || "";
    const url = `${backendBaseUrl()}${targetPath}${query}`;

    const method = req.method.toUpperCase();
    const logicalPath = logicalPathnameFromSegments(path);
    if (!isCockpitProxyAllowed(method, logicalPath)) {
        const { detail, code } = cockpitProxyForbiddenDetail(method, logicalPath);
        console.warn("[aihub-proxy] forbidden", { method, path: logicalPath, code });
        return NextResponse.json({ detail, ok: false, code }, { status: 403 });
    }
    const canHaveBody = !["GET", "HEAD"].includes(method);
    const headers = buildForwardHeaders(req, canHaveBody);

    let body: BodyInit | undefined = undefined;
    if (canHaveBody) {
        const raw = await req.arrayBuffer();
        if (raw.byteLength > 0) {
            body = raw;
        }
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

    try {
        const response = await fetch(url, {
            method,
            headers,
            body,
            signal: controller.signal,
            cache: "no-store",
            redirect: "manual",
        });

        if (response.status >= 400) {
            console.warn("[aihub-proxy] backend_error", {
                method,
                path: targetPath,
                status: response.status,
            });
        }

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

        console.error("[aihub-proxy] upstream_failure", {
            method,
            path: targetPath,
            timeout: isTimeout,
            message,
        });

        return NextResponse.json(
            {
                detail: message,
                ok: false,
            },
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
