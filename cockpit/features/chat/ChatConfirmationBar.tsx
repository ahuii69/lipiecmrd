"use client";

import { useState } from "react";

import type { PendingConfirmation } from "@/lib/api/chat-turn-stream";
import { apiClient } from "@/lib/api/client";

export function ChatConfirmationBar({
    pending,
    userId,
    sessionId,
    mode = "chat",
    apiKeyOverride,
    onConfirmed,
    onDismiss,
}: {
    pending: PendingConfirmation;
    userId: string;
    sessionId: string;
    mode?: "chat" | "agent" | "readonly" | "debug";
    apiKeyOverride?: string;
    onConfirmed: (summary: string) => void;
    onDismiss: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const tool = pending.tool_name || "narzędzie";

    const confirm = async () => {
        setBusy(true);
        setError(null);
        try {
            const res = await apiClient.executeCapability(
                {
                    user_id: userId,
                    session_id: sessionId,
                    mode,
                    tool_name: tool,
                    arguments: pending.arguments ?? {},
                    confirmed: true,
                    tool_policy_overrides: {
                        allow_sensitive_mutations: true,
                    },
                },
                apiKeyOverride,
            );
            if (!res?.ok) {
                const raw = res as unknown as {
                    error?: unknown;
                    tool_result?: { error?: unknown };
                };
                const errMsg =
                    typeof raw.error === "string"
                        ? raw.error
                        : typeof raw.tool_result?.error === "string"
                          ? raw.tool_result.error
                          : "Potwierdzenie nie powiodło się";
                throw new Error(String(errMsg));
            }
            onConfirmed(`Wykonano po potwierdzeniu: ${tool}.`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Błąd potwierdzenia");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="border-t border-[var(--chat-border)] bg-[var(--chat-surface-elevated,transparent)] px-4 py-3">
            <p className="text-sm text-[var(--chat-text)]">
                {pending.message ||
                    `Operacja „${tool}” wymaga Twojego potwierdzenia.`}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => void confirm()}
                    className="rounded-md bg-[var(--chat-accent,#3b82f6)] px-3 py-1.5 text-sm text-white disabled:opacity-50"
                >
                    {busy ? "Wykonuję…" : "Potwierdź"}
                </button>
                <button
                    type="button"
                    disabled={busy}
                    onClick={onDismiss}
                    className="rounded-md border border-[var(--chat-border)] px-3 py-1.5 text-sm text-[var(--chat-text-muted)]"
                >
                    Anuluj
                </button>
                {error ? (
                    <span className="text-xs text-red-400">{error}</span>
                ) : null}
            </div>
        </div>
    );
}
