"use client";

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useMemo } from "react";

import { useChatUiStore, type ChatDrawerTab } from "@/features/chat/chat-ui-store";
import { formatMemoryErrorMessage } from "@/lib/api/hub-auth-errors";
import { apiClient } from "@/lib/api/client";
import type {
    MemoryContextPackItem,
    MemoryV2SummaryItem,
    MemoryV2SummaryResponse,
} from "@/lib/api/types";
import type { ChatUIMessage } from "@/lib/store/cockpit-store";
import { cn } from "@/lib/utils";

const TABS: { id: ChatDrawerTab; label: string }[] = [
    { id: "pamiec", label: "Pamięć" },
    { id: "zrodla", label: "Źródła" },
    { id: "szczegoly", label: "Szczegóły" },
];

function shortText(value: unknown, fallback: string): string {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return fallback;
}

function memoryTotal(data?: MemoryV2SummaryResponse): number {
    if (!data) return 0;
    if (typeof data.total_items === "number") return data.total_items;
    const buckets = [
        data.facts,
        data.preferences,
        data.key_settlements,
        data.procedures_highlight,
    ];
    return buckets.reduce(
        (sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0),
        0,
    );
}

function extractSources(messages: ChatUIMessage[]): string[] {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
        const m = messages[i];
        if (m.role !== "assistant") continue;
        const chips = m.contextChips ?? [];
        const src = chips.filter(
            (c) =>
                /web|source|źród|research|http/i.test(c) || c.startsWith("http"),
        );
        if (src.length) return src;
    }
    return [];
}

export function ChatDrawer({
    userId,
    apiKeyOverride,
    messages,
}: {
    userId: string;
    apiKeyOverride?: string;
    messages: ChatUIMessage[];
}) {
    const { drawerOpen, setDrawerOpen, drawerTab, setDrawerTab } =
        useChatUiStore();
    const enabled = drawerOpen && Boolean(userId) && userId !== "default";

    const memory = useQuery({
        queryKey: ["chat-drawer-memory", userId, apiKeyOverride],
        queryFn: () => apiClient.getMemoryV2Summary(userId, apiKeyOverride),
        enabled: enabled && drawerTab === "pamiec",
        retry: 1,
    });
    const contextPack = useQuery({
        queryKey: ["chat-drawer-pack", userId, apiKeyOverride],
        queryFn: () =>
            apiClient.getMemoryContextPack(
                {
                    user_id: userId,
                    query: "profil użytkownika preferencje fakty",
                    limit: 8,
                    max_chars: 4000,
                    include_graph: false,
                },
                apiKeyOverride,
            ),
        enabled: enabled && drawerTab === "pamiec",
        retry: 1,
    });

    const packedItems = useMemo(
        () =>
            [
                ...(contextPack.data?.preferences ?? []),
                ...(contextPack.data?.facts ?? []),
                ...(contextPack.data?.procedures ?? []),
                ...(contextPack.data?.episodes ?? []),
                ...(contextPack.data?.other ?? []),
            ].slice(0, 8),
        [contextPack.data],
    );

    const sources = useMemo(() => extractSources(messages), [messages]);
    const lastAssistant = useMemo(() => {
        for (let i = messages.length - 1; i >= 0; i -= 1) {
            if (messages[i].role === "assistant") return messages[i];
        }
        return null;
    }, [messages]);

    if (!drawerOpen) return null;

    return (
        <>
            <button
                type="button"
                className="fixed inset-0 z-40 bg-black/50"
                aria-label="Zamknij panel"
                onClick={() => setDrawerOpen(false)}
            />
            <aside
                className="chat-drawer fixed inset-y-0 right-0 z-50 flex w-full max-w-[380px] flex-col border-l border-[var(--chat-border)] bg-[#0D0F12] pt-[env(safe-area-inset-top)]"
                data-testid="memory-drawer"
                role="dialog"
                aria-label="Panel boczny"
            >
                <div className="flex items-center justify-between border-b border-[var(--chat-border)] px-4 py-3">
                    <p className="text-sm font-medium text-[var(--chat-text)]">
                        {TABS.find((t) => t.id === drawerTab)?.label}
                    </p>
                    <button
                        type="button"
                        className="flex h-10 w-10 items-center justify-center text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]"
                        onClick={() => setDrawerOpen(false)}
                        aria-label="Zamknij"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="flex border-b border-[var(--chat-border)]">
                    {TABS.map(({ id, label }) => (
                        <button
                            key={id}
                            type="button"
                            onClick={() => setDrawerTab(id)}
                            className={cn(
                                "flex-1 py-2.5 text-xs font-medium transition",
                                drawerTab === id
                                    ? "border-b-2 border-[var(--chat-accent)] text-[var(--chat-text)]"
                                    : "text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]",
                            )}
                        >
                            {label}
                        </button>
                    ))}
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto p-4 [scrollbar-width:thin]">
                    {drawerTab === "pamiec" ? (
                        <MemoryPane
                            loading={memory.isLoading}
                            error={memory.isError ? memory.error : null}
                            empty={
                                !memory.isLoading &&
                                !memory.isError &&
                                memoryTotal(memory.data) === 0 &&
                                packedItems.length === 0
                            }
                            items={packedItems}
                            facts={(memory.data?.facts ?? []).slice(0, 5)}
                        />
                    ) : null}

                    {drawerTab === "zrodla" ? (
                        sources.length === 0 ? (
                            <p className="text-sm leading-relaxed text-[var(--chat-text-muted)]">
                                Źródła pojawią się po odpowiedzi z wyszukiwaniem
                                web/research.
                            </p>
                        ) : (
                            <ul className="space-y-3">
                                {sources.map((src) => (
                                    <li
                                        key={src}
                                        className="border-b border-[var(--chat-border)] pb-3 last:border-0"
                                    >
                                        <p className="text-sm font-medium text-[var(--chat-text)]">
                                            {src.startsWith("http")
                                                ? tryDomain(src)
                                                : src}
                                        </p>
                                        {src.startsWith("http") ? (
                                            <a
                                                href={src}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="mt-1 block truncate text-xs text-[var(--chat-accent-alt)] hover:underline"
                                            >
                                                {src}
                                            </a>
                                        ) : (
                                            <p className="mt-1 text-xs text-[var(--chat-text-muted)]">
                                                {src}
                                            </p>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )
                    ) : null}

                    {drawerTab === "szczegoly" ? (
                        <DetailsPane message={lastAssistant} />
                    ) : null}
                </div>
            </aside>
        </>
    );
}

function tryDomain(url: string): string {
    try {
        return new URL(url).hostname;
    } catch {
        return url.slice(0, 48);
    }
}

function MemoryPane({
    loading,
    error,
    empty,
    items,
    facts,
}: {
    loading: boolean;
    error: unknown;
    empty: boolean;
    items: MemoryContextPackItem[];
    facts: MemoryV2SummaryItem[];
}) {
    if (loading) {
        return (
            <p className="text-sm text-[var(--chat-text-muted)]">
                Ładowanie pamięci…
            </p>
        );
    }
    if (error) {
        console.error("[chat-drawer-memory]", error);
        return (
            <p className="text-sm text-[var(--chat-text-muted)]">
                {formatMemoryErrorMessage(error)}
            </p>
        );
    }
    if (empty) {
        return (
            <p className="text-sm leading-relaxed text-[var(--chat-text-muted)]">
                Pamięć jest jeszcze pusta. Będzie się budować w trakcie rozmowy.
            </p>
        );
    }
    return (
        <div className="space-y-3">
            {items.map((item, idx) => (
                <div key={String(item.id ?? idx)}>
                    <p className="text-sm font-medium text-[var(--chat-text)]">
                        {shortText(item.title || item.memory_type, "Pamięć")}
                    </p>
                    <p className="mt-1 line-clamp-4 text-xs text-[var(--chat-text-muted)]">
                        {shortText(item.content, "")}
                    </p>
                </div>
            ))}
            {items.length === 0
                ? facts.map((item, idx) => (
                      <div key={String(item.id ?? idx)}>
                          <p className="text-sm font-medium text-[var(--chat-text)]">
                              {shortText(item.title ?? item.label, "Fakt")}
                          </p>
                          <p className="mt-1 line-clamp-4 text-xs text-[var(--chat-text-muted)]">
                              {shortText(item.content, "")}
                          </p>
                      </div>
                  ))
                : null}
        </div>
    );
}

function DetailsPane({ message }: { message: ChatUIMessage | null }) {
    if (!message) {
        return (
            <p className="text-sm text-[var(--chat-text-muted)]">
                Wyślij wiadomość, aby zobaczyć szczegóły ostatniej odpowiedzi.
            </p>
        );
    }

    const diag = message.diagnostics;
    const rows: { label: string; value: string }[] = [];

    if (message.attachmentsSummary) {
        rows.push({ label: "Załączniki", value: message.attachmentsSummary });
    }
    if (diag?.model) rows.push({ label: "Model", value: String(diag.model) });
    if (diag?.provider) rows.push({ label: "Provider", value: String(diag.provider) });
    const trace = diag?.trace as Record<string, unknown> | undefined;
    const durationMs = trace?.duration_ms;
    if (typeof durationMs === "number") {
        rows.push({
            label: "Latency",
            value: `${Math.round(durationMs)} ms`,
        });
    }
    if (trace?.selected_strategy) {
        rows.push({
            label: "Strategia",
            value: String(trace.selected_strategy),
        });
    }
    const usage = diag?.usage as Record<string, unknown> | undefined;
    if (typeof usage?.total_tokens === "number") {
        rows.push({ label: "Tokeny", value: String(usage.total_tokens) });
    }

    if (rows.length === 0) {
        return (
            <p className="text-sm text-[var(--chat-text-muted)]">
                Brak dodatkowych metryk dla tej odpowiedzi.
            </p>
        );
    }

    return (
        <dl className="space-y-3">
            {rows.map(({ label, value }) => (
                <div key={label}>
                    <dt className="text-[10px] uppercase tracking-wider text-[var(--chat-text-muted)]">
                        {label}
                    </dt>
                    <dd className="mt-0.5 text-sm text-[var(--chat-text)]">{value}</dd>
                </div>
            ))}
        </dl>
    );
}
