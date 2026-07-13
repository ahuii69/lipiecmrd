"use client";

import { useRef } from "react";

import { ChatEmptyState } from "@/features/chat/ChatEmptyState";
import { ChatMessage } from "@/features/chat/ChatMessage";
import { useStickToBottomScroll } from "@/lib/chat/use-stick-to-bottom-scroll";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";

export function ChatStage({
    messages,
    loading,
    onSuggestion,
    onRetry,
}: {
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
        messagesLength: messages.length,
        lastMessageId: lastId,
        streamSig,
        loading,
        streamingBubble: messages.some((m) => m.streaming === true),
    });

    return (
        <div
            ref={scrollRef}
            onScroll={onScroll}
            data-testid="user-message-scroll"
            className="chat-stage-scroll h-full overflow-y-auto overflow-x-hidden [scrollbar-width:thin]"
        >
            <div ref={contentRef} className="min-h-full">
                {messages.length === 0 ? (
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
