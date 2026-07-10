import { clearApiKeyOverrideAfterHubAuthFailure } from "@/lib/api/api-key-override-recovery";
import { buildAihubProxyUrl } from "@/lib/api/client";
import { normalizeOptionalApiKeyOverride } from "@/lib/api/normalize-api-key-override";

export interface ChatSttResponse {
    ok: boolean;
    text?: string;
    error?: string;
    code?: string;
}

export async function transcribeChatAudio(
    blob: Blob,
    filename: string,
    apiKeyOverride?: string,
    signal?: AbortSignal,
): Promise<ChatSttResponse> {
    const form = new FormData();
    form.append("file", blob, filename);

    const headers: HeadersInit = {};
    const trimmed = normalizeOptionalApiKeyOverride(apiKeyOverride);
    if (trimmed) {
        (headers as Record<string, string>)["x-aihub-api-key-override"] =
            trimmed;
    }

    const url = buildAihubProxyUrl("/chat/stt");
    const res = await fetch(url, {
        method: "POST",
        body: form,
        headers,
        signal,
        cache: "no-store",
    });

    const text = await res.text();
    if (!res.ok) {
        let detail = `Błąd STT (${res.status})`;
        try {
            const j = text ? JSON.parse(text) : null;
            if (j && typeof j === "object") {
                const d = (j as { detail?: unknown }).detail;
                if (typeof d === "string") {
                    detail = d;
                }
            }
        } catch {
            if (text) detail = text.slice(0, 400);
        }
        clearApiKeyOverrideAfterHubAuthFailure(res.status, detail);
        return { ok: false, error: detail, code: `http_${res.status}` };
    }

    try {
        return JSON.parse(text) as ChatSttResponse;
    } catch {
        return { ok: false, error: "Niepoprawna odpowiedź STT", code: "bad_json" };
    }
}
