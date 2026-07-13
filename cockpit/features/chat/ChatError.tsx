"use client";

import { formatUserFacingError } from "@/lib/api/hub-auth-errors";

export function ChatError({
    message,
    onRetry,
    rawError,
}: {
    message: string;
    onRetry?: () => void;
    rawError?: string;
}) {
    if (rawError) {
        console.error("[chat-error]", rawError);
    }
    return (
        <div
            className="border-l-2 border-red-500/50 py-2 pl-4 text-[15px] text-[var(--chat-text)]"
            data-testid="chat-message"
            data-role="error"
        >
            <p>{formatUserFacingError(message)}</p>
            {onRetry ? (
                <button
                    type="button"
                    className="mt-2 text-sm text-[var(--chat-text-muted)] underline-offset-2 hover:text-[var(--chat-text)] hover:underline"
                    onClick={onRetry}
                >
                    Ponów
                </button>
            ) : null}
        </div>
    );
}
