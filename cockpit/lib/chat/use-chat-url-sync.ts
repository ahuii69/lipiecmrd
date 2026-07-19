"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import {
    readChatIdFromSearch,
    shouldApplyUrlSession,
    writeChatIdToSearch,
} from "@/lib/chat/chat-url";
import { useCockpitStore } from "@/lib/store/cockpit-store";

/**
 * Dwukierunkowy sync `?c=<sessionId>` ↔ `activeSessionId`.
 * URL jest źródłem przy pierwszym wejściu / odświeżeniu; potem store prowadzi replace.
 */
export function useChatUrlSync(opts?: { userId?: string | null }): void {
    const router = useRouter();
    const searchParams = useSearchParams();
    const activeSessionId = useCockpitStore((s) => s.activeSessionId);
    const setActiveSession = useCockpitStore((s) => s.setActiveSession);
    const ensureSessionStub = useCockpitStore((s) => s.ensureSessionStub);
    const applyingUrlRef = useRef(false);
    const userId = opts?.userId;

    // URL → store (deep link / refresh / back-forward)
    useEffect(() => {
        const fromUrl = readChatIdFromSearch(
            searchParams?.toString() ? `?${searchParams.toString()}` : "",
        );
        if (!shouldApplyUrlSession(fromUrl, activeSessionId)) return;
        applyingUrlRef.current = true;
        const exists = useCockpitStore
            .getState()
            .sessions.some((s) => s.id === fromUrl);
        if (!exists && fromUrl) {
            ensureSessionStub(fromUrl, userId || undefined);
        } else if (fromUrl) {
            setActiveSession(fromUrl);
        }
        queueMicrotask(() => {
            applyingUrlRef.current = false;
        });
    }, [searchParams, activeSessionId, setActiveSession, ensureSessionStub, userId]);

    // Store → URL
    useEffect(() => {
        if (applyingUrlRef.current) return;
        if (!activeSessionId) return;
        const current = searchParams?.get("c") || "";
        if (current === activeSessionId) return;
        const next = writeChatIdToSearch(
            searchParams?.toString() ? `?${searchParams.toString()}` : "",
            activeSessionId,
        );
        router.replace(next || "/");
    }, [activeSessionId, router, searchParams]);
}
