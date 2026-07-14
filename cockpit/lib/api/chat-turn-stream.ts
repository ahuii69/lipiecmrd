import { buildAihubProxyUrl } from "@/lib/api/client";
import { ChatTurnRequest, ChatTurnResponse } from "@/lib/api/types";

export interface ChatTurnStreamHandlers {
    onDelta: (chunk: string) => void;
    onDone: (
        result: ChatTurnResponse | undefined,
        attachmentsSummary?: string,
        contextChips?: string[],
    ) => void;
    onStatus?: (stage: string, labelPl?: string) => void;
    onTool?: (name: string, status: "start" | "done") => void;
    onMemory?: (count: number) => void;
    onReplace?: (fullText: string) => void;
}

export interface StreamChatTurnOptions extends ChatTurnStreamHandlers {
    /** Cockpit / debug: full JSON result on `done` */
    includeTurnResult?: boolean;
}

function dispatchEvent(
    ev: Record<string, unknown>,
    handlers: StreamChatTurnOptions,
): void {
    const t = ev.type;
    if (t === "delta" && typeof ev.content === "string") {
        handlers.onDelta(ev.content);
    }
    if (t === "replace" && typeof ev.content === "string") {
        handlers.onReplace?.(ev.content);
    }
    if (t === "status" && typeof ev.stage === "string") {
        handlers.onStatus?.(ev.stage, typeof ev.label_pl === "string" ? ev.label_pl : undefined);
    }
    if (
        t === "tool" &&
        typeof ev.name === "string" &&
        (ev.status === "start" || ev.status === "done")
    ) {
        handlers.onTool?.(ev.name, ev.status);
    }
    if (t === "memory" && ev.used === true) {
        const c = typeof ev.count === "number" ? ev.count : 0;
        handlers.onMemory?.(c);
    }
    if (t === "done") {
        const r = ev.result;
        const attachmentsSummary =
            typeof ev.attachments_summary === "string"
                ? ev.attachments_summary
                : undefined;
        const contextChips = Array.isArray(ev.context_chips)
            ? (ev.context_chips as string[])
            : undefined;
        // HTTP 200 + ok=false is a runtime failure — raise so ChatShell shows error.
        const okFlag = ev.ok;
        if (okFlag === false) {
            const detail =
                typeof ev.error === "string" && ev.error
                    ? ev.error
                    : "turn_failed";
            throw new Error(
                `Chat turn failed (${detail}). Odpowiedź nie została uznana za sukces.`,
            );
        }
        if (
            r &&
            typeof r === "object" &&
            (r as { ok?: unknown }).ok === false
        ) {
            throw new Error(
                "Chat turn failed (ok=false). Odpowiedź nie została uznana za sukces.",
            );
        }
        handlers.onDone(
            r && typeof r === "object"
                ? (r as ChatTurnResponse)
                : undefined,
            attachmentsSummary,
            contextChips,
        );
    }
}

/** POST /chat/turn?stream=true — parse SSE (data: JSON lines). */
export async function streamChatTurn(
    payload: ChatTurnRequest,
    signal: AbortSignal,
    handlers: StreamChatTurnOptions,
    _apiKeyOverride?: string,
): Promise<void> {
    const headers: HeadersInit = {
        "content-type": "application/json",
        accept: "text/event-stream",
    };

    const url = buildAihubProxyUrl("/chat/turn", {
        stream: true,
        ...(handlers.includeTurnResult ? { include_turn_result: true } : {}),
    });
    const res = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal,
        cache: "no-store",
    });

    if (!res.ok) {
        const text = await res.text();
        let detail = `Błąd API (${res.status})`;
        try {
            const j = text ? JSON.parse(text) : null;
            if (
                j &&
                typeof j === "object" &&
                typeof (j as { detail?: unknown }).detail === "string"
            ) {
                detail = (j as { detail: string }).detail;
            }
        } catch {
            if (text) detail = text.slice(0, 500);
        }
        throw new Error(detail);
    }

    const reader = res.body?.getReader();
    if (!reader) {
        throw new Error("Brak body odpowiedzi (stream)");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    const flushBlock = (block: string) => {
        const lines = block.split(/\r?\n/);
        for (const line of lines) {
            const t = line.trim();
            if (!t.startsWith("data:")) continue;
            const raw = t.slice(5).trimStart();
            if (!raw) continue;
            let ev: Record<string, unknown>;
            try {
                ev = JSON.parse(raw) as Record<string, unknown>;
            } catch {
                continue;
            }
            dispatchEvent(ev, handlers);
        }
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let sep: number;
            while ((sep = buffer.indexOf("\n\n")) >= 0) {
                const chunk = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                flushBlock(chunk);
            }
        }
        if (buffer.trim()) {
            flushBlock(buffer);
        }
    } finally {
        reader.releaseLock();
    }
}
