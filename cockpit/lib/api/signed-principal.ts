import { createHmac, randomUUID } from "crypto";

import type { ValidatedSession } from "@/lib/api/bff-session-auth";

const SCHEME = "v1";

function secretBytes(): Buffer {
    const raw =
        process.env.AIHUB_BFF_PRINCIPAL_SECRET?.trim() ||
        process.env.AIHUB_PROXY_TOKEN?.trim() ||
        process.env.AIHUB_API_KEY?.trim() ||
        process.env.API_KEY?.trim() ||
        "";
    if (!raw) {
        throw new Error("AIHUB_BFF_PRINCIPAL_SECRET is required");
    }
    return Buffer.from(raw, "utf8");
}

function b64url(data: Buffer | string): string {
    const buf = typeof data === "string" ? Buffer.from(data, "utf8") : data;
    return buf.toString("base64url");
}

export function signPrincipalForBackend(input: {
    session: ValidatedSession;
    method: string;
    path: string;
    requestId?: string;
    nonce?: string;
}): string {
    const payload = {
        method: input.method.toUpperCase(),
        nonce: input.nonce || randomUUID(),
        path: input.path,
        principal_id: input.session.principalId,
        request_id: input.requestId || randomUUID(),
        roles: input.session.roles,
        session_id: input.session.sessionId,
        tenant_id: input.session.tenantId,
        timestamp: Date.now() / 1000,
        user_id: input.session.userId,
    };
    const canonical = JSON.stringify(payload, Object.keys(payload).sort());
    const digest = createHmac("sha256", secretBytes()).update(canonical).digest();
    return `${SCHEME}.${b64url(canonical)}.${b64url(digest)}`;
}

export function newRequestId(): string {
    return randomUUID();
}
