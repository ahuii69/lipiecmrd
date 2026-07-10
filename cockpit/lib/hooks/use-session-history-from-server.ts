"use client";

import { useEffect } from "react";

import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

/** Po wejściu w sesję: transkrypt z backendu. Przy błędzie sieci nie nadpisujemy bufora (SoT = backend, ale bez „wymazywania” UI). */
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
        void (async () => {
            try {
                const r = await apiClient.getSessionHistory({
                    userId,
                    sessionId,
                    apiKeyOverride: apiKeyOverride || undefined,
                    timeoutMs: 45_000,
                });
                if (cancelled) return;
                useCockpitStore
                    .getState()
                    .replaceSessionMessagesFromServer(sessionId, r.messages);
            } catch (error) {
                if (cancelled) return;
                console.warn("session history sync failed; keeping local transcript", error);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [sessionId, userId, apiKeyOverride]);
}

/** Po zakończonej turze — zsynchronizuj z serwerem (przy błędzie zostaw bieżący UI). */
export async function reloadSessionHistoryFromServer(opts: {
    sessionId: string;
    userId: string;
    apiKeyOverride: string;
}): Promise<void> {
    const { sessionId, userId, apiKeyOverride } = opts;
    if (!userId || userId === "default") {
        return;
    }
    try {
        const r = await apiClient.getSessionHistory({
            userId,
            sessionId,
            apiKeyOverride: apiKeyOverride || undefined,
            timeoutMs: 45_000,
        });
        useCockpitStore
            .getState()
            .replaceSessionMessagesFromServer(sessionId, r.messages);
    } catch (error) {
        console.warn("session history reload failed; keeping local transcript", error);
    }
}
