"use client";

import {
    type MutableRefObject,
    type RefObject,
    useCallback,
    useEffect,
    useLayoutEffect,
} from "react";

import {
    isScrollContainerNearBottom,
    scrollContainerToBottom,
} from "@/lib/chat/scroll-near-bottom";

/**
 * Autoscroll jak w typowym chacie: podąża tylko gdy użytkownik jest „przy dole”;
 * po przewinięciu wyżej nie zrywa widoku (stickToBottomRef = false z onScroll).
 *
 * Przy zmianie sesji / historyNonce — zawsze wracamy na dół.
 */
export function useStickToBottomScroll(opts: {
    scrollRef: RefObject<HTMLElement | null>;
    contentRef: RefObject<HTMLElement | null>;
    stickToBottomRef: MutableRefObject<boolean>;
    sessionId?: string;
    historyNonce?: number;
    messagesLength: number;
    lastMessageId: string;
    streamSig: string;
    loading: boolean;
    streamingBubble: boolean;
}): { onScroll: () => void } {
    const {
        scrollRef,
        contentRef,
        stickToBottomRef,
        sessionId,
        historyNonce,
        messagesLength,
        lastMessageId,
        streamSig,
        loading,
        streamingBubble,
    } = opts;

    const onScroll = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        stickToBottomRef.current = isScrollContainerNearBottom(el);
    }, [scrollRef, stickToBottomRef]);

    const scrollIfStuck = useCallback(
        (force = false) => {
            const el = scrollRef.current;
            if (!el) return;
            if (!force && !stickToBottomRef.current) return;
            const behavior: ScrollBehavior =
                force || loading || streamingBubble ? "auto" : "smooth";
            requestAnimationFrame(() => {
                scrollContainerToBottom(el, behavior);
            });
        },
        [scrollRef, stickToBottomRef, loading, streamingBubble],
    );

    // Nowa sesja / świeża historia → zawsze dół.
    useLayoutEffect(() => {
        stickToBottomRef.current = true;
        scrollIfStuck(true);
    }, [sessionId, historyNonce, stickToBottomRef, scrollIfStuck]);

    useLayoutEffect(() => {
        scrollIfStuck();
    }, [
        messagesLength,
        lastMessageId,
        streamSig,
        loading,
        streamingBubble,
        scrollIfStuck,
    ]);

    useEffect(() => {
        const outer = scrollRef.current;
        const inner = contentRef.current;
        if (!outer || !inner || typeof ResizeObserver === "undefined") return;
        const ro = new ResizeObserver(() => {
            if (!stickToBottomRef.current) return;
            scrollContainerToBottom(outer, "auto");
        });
        ro.observe(inner);
        return () => ro.disconnect();
    }, [scrollRef, contentRef, stickToBottomRef]);

    return { onScroll };
}
