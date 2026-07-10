"use client";

import { useLayoutEffect, useRef } from "react";

import { MessageItem } from "@/features/chat/message-item";
import { EmptyState } from "@/features/shared/empty-state";
import { scrollContainerToBottom } from "@/lib/chat/scroll-near-bottom";
import { useStickToBottomScroll } from "@/lib/chat/use-stick-to-bottom-scroll";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";
import { cn } from "@/lib/utils";
import { MessageSquareMore } from "lucide-react";

export function MessageList({
    messages,
    loading,
    className,
    forceScrollBottomNonce = 0,
}: {
    messages: ChatUIMessage[];
    loading: boolean;
    className?: string;
    /** Po wysłaniu wiadomości — zawsze dół (niezależnie od wcześniejszego scrolla). */
    forceScrollBottomNonce?: number;
}) {
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const stickToBottomRef = useRef(true);
    const streamSig = messages.map((m) => m.content.length).join(",");
    const last = messages[messages.length - 1];
    const streamingBubble =
        last?.role === "assistant" && last.streaming === true;

    const { onScroll } = useStickToBottomScroll({
        scrollRef: viewportRef,
        contentRef,
        stickToBottomRef,
        messagesLength: messages.length,
        lastMessageId: last?.id ?? "",
        streamSig,
        loading,
        streamingBubble,
    });

    useLayoutEffect(() => {
        if (forceScrollBottomNonce <= 0) return;
        stickToBottomRef.current = true;
        const el = viewportRef.current;
        if (!el) return;
        requestAnimationFrame(() => scrollContainerToBottom(el, "auto"));
    }, [forceScrollBottomNonce]);

    if (!messages.length && !loading) {
        return (
            <EmptyState
                icon={MessageSquareMore}
                title="Pusto? To zaczynamy"
                description="Wyślij pierwszą wiadomość. AI-Hub odpowie i od razu pokaże ślad runtime, narzędzia i diagnostykę."
            />
        );
    }

    return (
        <div
            className={cn(
                "min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-y-contain rounded-xl border border-border bg-card/50 p-3",
                className,
            )}
            ref={viewportRef}
            onScroll={onScroll}
            data-testid="cockpit-message-scroll"
        >
            <div ref={contentRef} className="space-y-3 break-words">
                {messages.map((m) => (
                    <MessageItem key={m.id} message={m} />
                ))}
                {loading && !streamingBubble ? (
                    <div className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
                        AI-Hub myśli… i nie udaje, że już wszystko wie.
                    </div>
                ) : null}
            </div>
        </div>
    );
}
