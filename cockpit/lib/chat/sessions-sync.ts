/**
 * Cross-tab sync for chat session list + archive state.
 * Server is source of truth; BroadcastChannel + focus refetch avoid stale lists.
 * Not SSE/WebSocket — session list is HTTP GET (no server push cache).
 */

export type SessionsSyncEvent =
    | { type: "sessions-changed"; userId: string }
    | { type: "archive-changed"; userId: string; sessionId: string; archived: boolean };

const CHANNEL = "aihub-chat-sessions-sync-v1";

function canUseBroadcast(): boolean {
    return typeof BroadcastChannel !== "undefined";
}

export function publishSessionsSync(event: SessionsSyncEvent): void {
    if (!canUseBroadcast()) return;
    try {
        const bc = new BroadcastChannel(CHANNEL);
        bc.postMessage(event);
        bc.close();
    } catch {
        // Ignore — sync is best-effort across tabs.
    }
}

export function subscribeSessionsSync(
    handler: (event: SessionsSyncEvent) => void,
): () => void {
    if (!canUseBroadcast()) {
        return () => undefined;
    }
    const bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = (msg: MessageEvent<SessionsSyncEvent>) => {
        if (msg.data && typeof msg.data === "object" && msg.data.type) {
            handler(msg.data);
        }
    };
    return () => {
        try {
            bc.close();
        } catch {
            void 0;
        }
    };
}
