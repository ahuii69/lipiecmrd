/**
 * Buduje `history` dla POST /chat/turn — spójnie dla Cockpit i User shell.
 * Kolejność: rosnąco po `createdAt` (stabilność po rehydrate / migracjach).
 */
import type { ChatMessageInput } from "@/lib/api/types";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";

export function toChatHistoryPayload(
    messages: ChatUIMessage[],
): ChatMessageInput[] {
    const rows = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        /* Pusta bańka asystenta ze streamu — nie wysyłaj do API (duplikat / szum). */
        .filter((m) => !(m.role === "assistant" && m.streaming === true))
        .map((m) => ({
            m,
            createdAt: Number(m.createdAt ?? 0),
        }));
    rows.sort((a, b) => a.createdAt - b.createdAt);
    return rows.map(({ m }) => ({
        role: m.role,
        content: m.content ?? "",
        tool_calls: m.diagnostics?.tool_calls ?? [],
    }));
}
