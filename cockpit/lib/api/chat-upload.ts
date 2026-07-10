import { clearApiKeyOverrideAfterHubAuthFailure } from "@/lib/api/api-key-override-recovery";
import { buildAihubProxyUrl } from "@/lib/api/client";
import { normalizeOptionalApiKeyOverride } from "@/lib/api/normalize-api-key-override";
import type { ChatUploadResponse } from "@/lib/api/types";

export async function uploadChatFile(
    params: { user_id: string; session_id: string; file: File },
    apiKeyOverride?: string,
    signal?: AbortSignal,
): Promise<ChatUploadResponse> {
    const form = new FormData();
    form.append("user_id", params.user_id);
    form.append("session_id", params.session_id);
    form.append("file", params.file);

    const headers: HeadersInit = {};
    const trimmed = normalizeOptionalApiKeyOverride(apiKeyOverride);
    if (trimmed) {
        (headers as Record<string, string>)["x-aihub-api-key-override"] =
            trimmed;
    }

    const url = buildAihubProxyUrl("/chat/upload");
    const res = await fetch(url, {
        method: "POST",
        body: form,
        headers,
        signal,
        cache: "no-store",
    });

    const text = await res.text();
    if (!res.ok) {
        let detail = `Błąd uploadu (${res.status})`;
        try {
            const j = text ? JSON.parse(text) : null;
            if (j && typeof j === "object") {
                const d = (j as { detail?: unknown }).detail;
                if (typeof d === "string") {
                    detail = d;
                } else if (d && typeof d === "object") {
                    const o = d as { message?: unknown; error?: unknown };
                    if (typeof o.message === "string") {
                        detail = o.message;
                    } else if (typeof o.error === "string") {
                        detail = o.error;
                    }
                }
            }
        } catch {
            if (text) detail = text.slice(0, 400);
        }
        clearApiKeyOverrideAfterHubAuthFailure(res.status, detail);
        throw new Error(detail);
    }

    try {
        return JSON.parse(text) as ChatUploadResponse;
    } catch {
        throw new Error("Niepoprawna odpowiedź serwera po uploadzie");
    }
}
