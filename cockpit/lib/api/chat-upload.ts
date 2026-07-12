import { buildAihubProxyUrl } from "@/lib/api/client";
import type { ChatUploadResponse } from "@/lib/api/types";

export async function uploadChatFile(
    params: { user_id: string; session_id: string; file: File },
    _apiKeyOverride?: string,
    signal?: AbortSignal,
): Promise<ChatUploadResponse> {
    const form = new FormData();
    form.append("user_id", params.user_id);
    form.append("session_id", params.session_id);
    form.append("file", params.file);

    const url = buildAihubProxyUrl("/chat/upload");
    const res = await fetch(url, {
        method: "POST",
        body: form,
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
                }
            }
        } catch {
            if (text) detail = text.slice(0, 500);
        }
        throw new Error(detail);
    }

    return (text ? JSON.parse(text) : {}) as ChatUploadResponse;
}
