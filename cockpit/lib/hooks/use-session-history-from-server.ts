"use client";

import { useEffect } from "react";

import { chatSessionRuntime } from "@/lib/chat/chat-session-runtime";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

/**
 * Po wejściu w sesję: transkrypt z backendu.
 * - oznacza historyStatus loading/ready/error
 * - ignoruje stale odpowiedzi po switchu
 * - nie nadpisuje lokalnego streamu / inflight turn
 */
export function useSessionHistoryFromServer(opts: {
    sessionId: string;
    userId: string;
    apiKeyOverride: string;
}): void {
    const { sessionId, userId, apiKeyOverride } = opts;

    useEffect(() => {
        let cancelled = false;
        if (!userId || userId === "default") {
            return () => {
                cancelled = true;
            };
        }
        if (!sessionId) {
            return () => {
                cancelled = true;
            };
        }

        const store = useCockpitStore.getState();
        store.setSessionHistoryStatus(sessionId, "loading");
        const loadGeneration = chatSessionRuntime.currentGeneration();

        const controller = new AbortController();
        void (async () => {
            try {
                const r = await apiClient.getSessionHistory({
                    userId,
                    sessionId,
                    apiKeyOverride: apiKeyOverride || undefined,
                    timeoutMs: 45_000,
                });
                if (cancelled) return;
                if (!canApplyServerHistory(sessionId, loadGeneration)) {
                    return;
                }
                useCockpitStore
                    .getState()
                    .replaceSessionMessagesFromServer(sessionId, r.messages);
            } catch (error) {
                if (cancelled || controller.signal.aborted) return;
                console.warn(
                    "session history sync failed; keeping local transcript",
                    error,
                );
                useCockpitStore
                    .getState()
                    .setSessionHistoryStatus(
                        sessionId,
                        "error",
                        error instanceof Error ? error.message : "history_failed",
                    );
            }
        })();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [sessionId, userId, apiKeyOverride]);
}

function canApplyServerHistory(
    sessionId: string,
    loadGeneration: number,
): boolean {
    const store = useCockpitStore.getState();
    if (store.sessionHasStreamingMessage(sessionId)) {
        return false;
    }
    // Newer turn started after this load began — do not clobber.
    if (chatSessionRuntime.currentGeneration() !== loadGeneration) {
        if (chatSessionRuntime.getAbortController(sessionId)) {
            return false;
        }
        if (store.sessionHasStreamingMessage(sessionId)) {
            return false;
        }
    }
    if (chatSessionRuntime.getAbortController(sessionId)) {
        return false;
    }
    return true;
}

/** Po zakończonej turze — zsynchronizuj z serwerem (przy błędzie zostaw bieżący UI). */
export async function reloadSessionHistoryFromServer(opts: {
    sessionId: string;
    userId: string;
    apiKeyOverride: string;
    generation?: number;
}): Promise<void> {
    const { sessionId, userId, apiKeyOverride, generation } = opts;
    if (!userId || userId === "default") {
        return;
    }
    const loadGeneration =
        generation ?? chatSessionRuntime.currentGeneration();
    try {
        const r = await apiClient.getSessionHistory({
            userId,
            sessionId,
            apiKeyOverride: apiKeyOverride || undefined,
            timeoutMs: 45_000,
        });
        if (!canApplyServerHistory(sessionId, loadGeneration)) {
            return;
        }
        // If caller tied reload to a finished turn generation, allow apply even
        // after endTurn cleared the controller — unless a newer turn exists.
        if (
            generation !== undefined &&
            chatSessionRuntime.currentGeneration() !== generation &&
            chatSessionRuntime.getAbortController(sessionId)
        ) {
            return;
        }
        useCockpitStore
            .getState()
            .replaceSessionMessagesFromServer(sessionId, r.messages);
    } catch (error) {
        console.warn("session history reload failed; keeping local transcript", error);
    }
}
