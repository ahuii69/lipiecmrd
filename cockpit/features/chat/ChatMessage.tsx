"use client";

import { Check, Copy, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMemo, useState } from "react";

import { ChatError } from "@/features/chat/ChatError";
import { ChatMarkdown } from "@/features/chat/ChatMarkdown";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

export function ChatMessage({
    message,
    onRetry,
}: {
    message: ChatUIMessage;
    onRetry?: () => void;
}) {
    const [copied, setCopied] = useState(false);
    const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

    const isUser = message.role === "user";
    const body = (message.content ?? "").trim();
    const isErrorOnly = Boolean(message.error) && !isUser;

    const sources = useMemo(() => {
        const chips = message.contextChips ?? [];
        return chips.filter(
            (c) =>
                /web|source|źród|research|http/i.test(c) || c.startsWith("http"),
        );
    }, [message.contextChips]);

    const copy = async () => {
        if (!body) return;
        await navigator.clipboard.writeText(message.content ?? "");
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
    };

    if (isErrorOnly) {
        return (
            <div className="chat-stage-inner py-4">
                <ChatError
                    message={message.error || ""}
                    onRetry={onRetry}
                    rawError={message.error}
                />
            </div>
        );
    }

    if (isUser) {
        return (
            <div
                className="chat-stage-inner flex justify-end py-2"
                data-testid="chat-message"
                data-role="user"
            >
                <div
                    className="max-w-[70%] rounded-[20px] border border-[rgba(34,197,94,0.25)] bg-[#12372D] px-4 py-3 text-base leading-[1.65] text-[var(--chat-text)] md:max-w-[70%] max-md:max-w-[85%]"
                >
                    <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
            </div>
        );
    }

    return (
        <article
            className="group/msg chat-stage-inner py-7"
            data-testid="chat-message"
            data-role="assistant"
            data-streaming={message.streaming ? "true" : "false"}
        >
            <header className="mb-2 flex items-baseline gap-2 text-xs text-[var(--chat-text-muted)]">
                <span className="font-medium text-[var(--chat-text)]">AI-Hub</span>
                {!message.streaming ? (
                    <time dateTime={new Date(message.createdAt).toISOString()}>
                        {formatTs(message.createdAt)}
                    </time>
                ) : null}
            </header>

            <div className="text-base leading-[1.65] text-[var(--chat-text)]">
                {message.streaming && !body ? (
                    <div className="flex items-center gap-2 text-[var(--chat-text-muted)]">
                        <span className="chat-thinking-dots" aria-hidden>
                            <span />
                            <span />
                            <span />
                        </span>
                        <span className="text-sm">Piszę…</span>
                    </div>
                ) : message.streaming ? (
                    <div className="whitespace-pre-wrap break-words">
                        {message.content}
                        <span className="chat-stream-cursor" aria-hidden />
                    </div>
                ) : body ? (
                    <ChatMarkdown content={message.content || ""} />
                ) : null}

                {message.error ? (
                    <div className="mt-3">
                        <ChatError
                            message={message.error}
                            onRetry={onRetry}
                            rawError={message.error}
                        />
                    </div>
                ) : null}
            </div>

            {!message.streaming && body ? (
                <div className="mt-3 flex flex-wrap items-center gap-1 opacity-0 transition group-hover/msg:opacity-100 group-focus-within/msg:opacity-100">
                    <ActionBtn label="Kopiuj" onClick={() => void copy()}>
                        {copied ? (
                            <Check className="h-3.5 w-3.5" />
                        ) : (
                            <Copy className="h-3.5 w-3.5" />
                        )}
                    </ActionBtn>
                    {onRetry ? (
                        <ActionBtn label="Ponów" onClick={onRetry}>
                            <RotateCcw className="h-3.5 w-3.5" />
                        </ActionBtn>
                    ) : null}
                    <ActionBtn
                        label="Przydatne"
                        active={feedback === "up"}
                        onClick={() => setFeedback("up")}
                    >
                        <ThumbsUp className="h-3.5 w-3.5" />
                    </ActionBtn>
                    <ActionBtn
                        label="Nieprzydatne"
                        active={feedback === "down"}
                        onClick={() => setFeedback("down")}
                    >
                        <ThumbsDown className="h-3.5 w-3.5" />
                    </ActionBtn>
                    {sources.length > 0 ? (
                        <span className="ml-2 text-xs text-[var(--chat-text-muted)]">
                            {sources.length}{" "}
                            {sources.length === 1 ? "źródło" : "źródeł"}
                        </span>
                    ) : null}
                </div>
            ) : null}
        </article>
    );
}

function ActionBtn({
    children,
    label,
    onClick,
    active,
}: {
    children: React.ReactNode;
    label: string;
    onClick: () => void;
    active?: boolean;
}) {
    return (
        <button
            type="button"
            aria-label={label}
            title={label}
            onClick={onClick}
            className={`rounded p-1.5 text-[var(--chat-text-muted)] transition hover:bg-white/[0.06] hover:text-[var(--chat-text)] ${active ? "text-[var(--chat-accent)]" : ""}`}
        >
            {children}
        </button>
    );
}
