/**
 * Pure helpers for `?c=<sessionId>` sync (testable without Next router).
 */

export function readChatIdFromSearch(search: string): string | null {
    const q = search.startsWith("?") ? search.slice(1) : search;
    const params = new URLSearchParams(q);
    const raw = (params.get("c") || "").trim();
    return raw || null;
}

export function writeChatIdToSearch(
    search: string,
    sessionId: string | null,
): string {
    const q = search.startsWith("?") ? search.slice(1) : search;
    const params = new URLSearchParams(q);
    if (sessionId) {
        params.set("c", sessionId);
    } else {
        params.delete("c");
    }
    const next = params.toString();
    return next ? `?${next}` : "";
}

export function shouldApplyUrlSession(
    urlSessionId: string | null,
    activeSessionId: string,
): boolean {
    if (!urlSessionId) return false;
    return urlSessionId !== activeSessionId;
}
