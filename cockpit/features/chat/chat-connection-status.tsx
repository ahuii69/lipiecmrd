"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** Mała kropka + tekst — spec §2B (bez kafli READY/DB). */
export function ChatConnectionStatus({
    apiKeyOverride,
}: {
    apiKeyOverride?: string;
}) {
    const ping = useQuery({
        queryKey: ["chat-connection-ping", apiKeyOverride],
        queryFn: () => apiClient.runtimePing(apiKeyOverride),
        retry: 1,
        refetchInterval: 30_000,
    });
    const online = Boolean(ping.data) && !ping.isError;
    const label = online
        ? "Połączono"
        : ping.isLoading
          ? "Łączenie…"
          : "Offline";

    return (
        <span
            className="inline-flex items-center gap-2 text-xs text-[var(--chat-text-muted)]"
            data-testid="connection-status"
        >
            <span
                className={cn(
                    "h-2 w-2 shrink-0 rounded-full",
                    online
                        ? "bg-[var(--chat-accent)]"
                        : ping.isLoading
                          ? "bg-zinc-500 animate-pulse"
                          : "bg-amber-500/80",
                )}
                aria-hidden
            />
            <span className="hidden sm:inline">{label}</span>
        </span>
    );
}
