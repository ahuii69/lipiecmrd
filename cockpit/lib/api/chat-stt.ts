import { buildAihubProxyUrl } from "@/lib/api/client";

export interface ChatSttResponse {
    ok: boolean;
    text?: string;
    error?: string;
    code?: string;
}

export async function transcribeChatAudio(
    blob: Blob,
    filename: string,
    _apiKeyOverride?: string,
    signal?: AbortSignal,
): Promise<ChatSttResponse> {
    const form = new FormData();
    form.append("file", blob, filename);

    const url = buildAihubProxyUrl("/chat/stt");
    const res = await fetch(url, {
        method: "POST",
        body: form,
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
            if (text) detail = text.slice(0, 500);
        }
        return { ok: false, error: detail, code: String(res.status) };
    }

    try {
        return JSON.parse(text) as ChatSttResponse;
    } catch {
        return { ok: false, error: "Nieprawidłowa odpowiedź STT" };
    }
}
