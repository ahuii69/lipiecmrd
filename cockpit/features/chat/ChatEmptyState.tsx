"use client";

import { EMPTY_SUGGESTIONS } from "@/features/chat/chat-constants";

export function ChatEmptyState({
    onSuggestion,
}: {
    onSuggestion?: (text: string) => void;
}) {
    return (
        <div className="flex min-h-full flex-col items-center justify-center px-4 py-16 text-center">
            <p className="text-sm font-medium tracking-wide text-[var(--chat-text-muted)]">
                AI-Hub
            </p>
            <h1 className="mt-4 max-w-lg text-balance text-[1.35rem] font-medium leading-snug text-[var(--chat-text)] sm:text-2xl">
                No to lecimy, Mordo. Co dziś rozkminiamy?
            </h1>
            <ul className="mt-10 flex flex-col items-center gap-2">
                {EMPTY_SUGGESTIONS.map((s) => (
                    <li key={s}>
                        <button
                            type="button"
                            onClick={() => onSuggestion?.(s)}
                            className="max-w-[220px] truncate text-left text-sm text-[var(--chat-text-muted)] transition hover:text-[var(--chat-text)]"
                        >
                            {s}
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    );
}
