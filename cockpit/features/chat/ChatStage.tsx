"use client";

import { useRef } from "react";

import { ChatEmptyState } from "@/features/chat/ChatEmptyState";
import { ChatMessage } from "@/features/chat/ChatMessage";
import { useStickToBottomScroll } from "@/lib/chat/use-stick-to-bottom-scroll";
import type {
    ChatUIMessage,
    SessionHistoryStatus,
} from "@/lib/store/cockpit-store";

export function ChatStage({
    sessionId,
    historyNonce,
    historyStatus,
    messages,
    loading,
    onSuggestion,
    onRetry,
}: {
    sessionId: string;
    historyNonce: number;
    historyStatus: SessionHistoryStatus;
    messages: ChatUIMessage[];
    loading: boolean;
    onSuggestion?: (text: string) => void;
    onRetry?: () => void;
}) {
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const stickToBottomRef = useRef(true);
    const lastId = messages.length ? messages[messages.length - 1].id : "";
    const streamSig = messages
        .map((m) => `${m.id}:${m.content.length}:${m.streaming ? 1 : 0}`)
        .join("|");

    const { onScroll } = useStickToBottomScroll({
        scrollRef,
        contentRef,
        stickToBottomRef,
        sessionId,
        historyNonce,
        messagesLength: messages.length,
        lastMessageId: lastId,
        streamSig,
        loading,
        streamingBubble: messages.some((m) => m.streaming === true),
    });

    const showHistorySkeleton =
        messages.length === 0 && historyStatus === "loading";

    return (
        <div
            ref={scrollRef}
            onScroll={onScroll}
            data-testid="user-message-scroll"
            className="chat-stage-scroll h-full overflow-y-auto overflow-x-hidden [scrollbar-width:thin]"
        >
            <div ref={contentRef} className="min-h-full">
                {showHistorySkeleton ? (
                    <div
                        className="chat-stage-messages space-y-6 pb-[180px] pt-10 max-md:px-4 max-md:pb-[150px] max-md:pt-4"
                        aria-busy
                        aria-label="Ładowanie historii"
                    >
                        <div className="chat-stage-inner mx-auto h-16 max-w-[70%] animate-pulse rounded-[20px] bg-white/[0.06]" />
                        <div className="chat-stage-inner mx-auto h-24 max-w-[90%] animate-pulse rounded-lg bg-white/[0.04]" />
                        <div className="chat-stage-inner mx-auto h-12 max-w-[60%] animate-pulse rounded-[20px] bg-white/[0.06]" />
                    </div>
                ) : messages.length === 0 ? (
                    <ChatEmptyState onSuggestion={onSuggestion} />
                ) : (
                    <div className="chat-stage-messages pb-[180px] pt-10 max-md:pb-[150px] max-md:pt-4 max-md:px-4">
                        {messages.map((m) => (
                            <ChatMessage
                                key={m.id}
                                message={m}
                                onRetry={m.error ? onRetry : undefined}
                            />
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
