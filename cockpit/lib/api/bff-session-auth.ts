import type { NextRequest } from "next/server";

import { SESSION_COOKIE_NAME } from "@/lib/auth/session-constants";

export type ValidatedSession = {
    principalId: string;
    userId: string;
    tenantId: string;
    roles: string[];
    sessionId: string;
    csrfToken: string;
    expiresAt: number;
};

function backendBaseUrl(): string {
    return (process.env.AIHUB_BASE_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");
}

export async function validateSessionFromRequest(
    req: NextRequest,
): Promise<ValidatedSession | null> {
    const token = req.cookies.get(SESSION_COOKIE_NAME)?.value?.trim();
    if (!token) {
        return null;
    }
    const response = await fetch(`${backendBaseUrl()}/auth/me`, {
        method: "GET",
        headers: {
            accept: "application/json",
            cookie: `${SESSION_COOKIE_NAME}=${token}`,
        },
        cache: "no-store",
    });
    if (!response.ok) {
        return null;
    }
    const body = (await response.json()) as {
        principal?: {
            id?: string;
            user_id?: string;
            tenant_id?: string;
            role?: string;
        };
        csrf_token?: string;
        expires_at?: number;
    };
    const principal = body.principal;
    if (!principal?.user_id || !principal.id) {
        return null;
    }
    const role = (principal.role || "user").trim() || "user";
    return {
        principalId: String(principal.id),
        userId: String(principal.user_id),
        tenantId: String(principal.tenant_id || principal.user_id),
        roles: [role],
        sessionId: String(principal.id),
        csrfToken: String(body.csrf_token || ""),
        expiresAt: Number(body.expires_at || 0),
    };
}
