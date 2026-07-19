"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useMemo, useState } from "react";

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

function extractHttpUrls(sources: string[]): string[] {
    const out: string[] = [];
    for (const s of sources) {
        if (s.startsWith("http")) {
            out.push(s);
            continue;
        }
        const m = s.match(/https?:\/\/[^\s]+/i);
        if (m) out.push(m[0]);
    }
    return Array.from(new Set(out));
}

export function ChatDrawer({
    userId,
    sessionId,
    apiKeyOverride,
    messages,
}: {
    userId: string;
    sessionId: string;
    apiKeyOverride?: string;
    messages: ChatUIMessage[];
}) {
    const { drawerOpen, setDrawerOpen, drawerTab, setDrawerTab } =
        useChatUiStore();
    const enabled = drawerOpen && Boolean(userId) && userId !== "default";
    const queryClient = useQueryClient();

    const [searchQ, setSearchQ] = useState("");
    const [searchHits, setSearchHits] = useState<
        Array<{ title?: string; content?: string; id?: string }>
    >([]);
    const [searchErr, setSearchErr] = useState<string | null>(null);
    const [actionMsg, setActionMsg] = useState<string | null>(null);

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
    const httpSources = useMemo(() => extractHttpUrls(sources), [sources]);
    const lastAssistant = useMemo(() => {
        for (let i = messages.length - 1; i >= 0; i -= 1) {
            if (messages[i].role === "assistant") return messages[i];
        }
        return null;
    }, [messages]);

    const searchMutation = useMutation({
        mutationFn: async (query: string) => {
            const r = await apiClient.memorySearch(
                {
                    user_id: userId,
                    session_id: sessionId,
                    mode: "chat",
                    query,
                    limit: 8,
                },
                apiKeyOverride,
            );
            return r;
        },
        onSuccess: (data) => {
            setSearchErr(null);
            const semantic = Array.isArray(
                (data as { semantic?: unknown })?.semantic,
            )
                ? ((data as { semantic: Array<Record<string, unknown>> }).semantic)
                : [];
            const episodic = Array.isArray(
                (data as { episodic?: unknown })?.episodic,
            )
                ? ((data as { episodic: Array<Record<string, unknown>> }).episodic)
                : [];
            const hits = [...semantic, ...episodic].slice(0, 8).map((row) => ({
                id: String(row.id ?? ""),
                title: String(row.title ?? row.layer ?? "Wynik"),
                content: String(row.content ?? row.text ?? ""),
            }));
            setSearchHits(hits);
            if (hits.length === 0) {
                setActionMsg("Brak trafień w pamięci dla tego zapytania.");
            } else {
                setActionMsg(`Znaleziono ${hits.length} wpis(ów).`);
            }
        },
        onError: (err) => {
            setSearchErr(formatMemoryErrorMessage(err));
            setSearchHits([]);
        },
    });

    const ingestMutation = useMutation({
        mutationFn: async (url: string) => {
            return apiClient.executeCapability(
                {
                    user_id: userId,
                    session_id: sessionId,
                    mode: "chat",
                    tool_name: "web.ingest_url",
                    arguments: { url, session_id: sessionId },
                },
                apiKeyOverride,
            );
        },
        onSuccess: () => {
            setActionMsg("Źródło zapisane do pamięci.");
            void queryClient.invalidateQueries({
                queryKey: ["chat-drawer-memory", userId],
            });
            void queryClient.invalidateQueries({
                queryKey: ["chat-drawer-pack", userId],
            });
        },
        onError: (err) => {
            setActionMsg(formatMemoryErrorMessage(err));
        },
    });

    const rememberMutation = useMutation({
        mutationFn: async (fact: string) => {
            return apiClient.executeCapability(
                {
                    user_id: userId,
                    session_id: sessionId,
                    mode: "chat",
                    tool_name: "memory.add_fact",
                    arguments: {
                        fact,
                        tags: ["drawer", "explicit"],
                        meta: { source: "chat_drawer" },
                    },
                },
                apiKeyOverride,
            );
        },
        onSuccess: () => {
            setActionMsg("Fakt zapisany w pamięci.");
            void queryClient.invalidateQueries({
                queryKey: ["chat-drawer-memory", userId],
            });
            void queryClient.invalidateQueries({
                queryKey: ["chat-drawer-pack", userId],
            });
        },
        onError: (err) => {
            setActionMsg(formatMemoryErrorMessage(err));
        },
    });

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
                    {actionMsg ? (
                        <p className="mb-3 text-xs text-[var(--chat-accent-alt)]">
                            {actionMsg}
                        </p>
                    ) : null}

                    {drawerTab === "pamiec" ? (
                        <MemoryPane
                            loading={memory.isLoading}
                            error={memory.isError ? memory.error : null}
                            empty={
                                !memory.isLoading &&
                                !memory.isError &&
                                memoryTotal(memory.data) === 0 &&
                                packedItems.length === 0 &&
                                searchHits.length === 0
                            }
                            items={packedItems}
                            facts={(memory.data?.facts ?? []).slice(0, 5)}
                            searchQ={searchQ}
                            onSearchQ={setSearchQ}
                            onSearch={() => {
                                const q = searchQ.trim();
                                if (q.length < 2) {
                                    setActionMsg("Wpisz co najmniej 2 znaki.");
                                    return;
                                }
                                searchMutation.mutate(q);
                            }}
                            searchBusy={searchMutation.isPending}
                            searchHits={searchHits}
                            searchErr={searchErr}
                            onRememberFact={(fact) => {
                                const f = fact.trim();
                                if (f.length < 3) return;
                                rememberMutation.mutate(f);
                            }}
                            rememberBusy={rememberMutation.isPending}
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
                                {sources.map((src) => {
                                    const url = src.startsWith("http")
                                        ? src
                                        : extractHttpUrls([src])[0];
                                    return (
                                        <li
                                            key={src}
                                            className="border-b border-[var(--chat-border)] pb-3 last:border-0"
                                        >
                                            <p className="text-sm font-medium text-[var(--chat-text)]">
                                                {src.startsWith("http")
                                                    ? tryDomain(src)
                                                    : src}
                                            </p>
                                            {url ? (
                                                <a
                                                    href={url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="mt-1 block truncate text-xs text-[var(--chat-accent-alt)] hover:underline"
                                                >
                                                    {url}
                                                </a>
                                            ) : (
                                                <p className="mt-1 text-xs text-[var(--chat-text-muted)]">
                                                    {src}
                                                </p>
                                            )}
                                            {url ? (
                                                <button
                                                    type="button"
                                                    className="mt-2 text-xs font-medium text-[var(--chat-accent)] hover:underline disabled:opacity-50"
                                                    disabled={ingestMutation.isPending}
                                                    onClick={() =>
                                                        ingestMutation.mutate(url)
                                                    }
                                                >
                                                    {ingestMutation.isPending
                                                        ? "Zapisuję…"
                                                        : "Zapisz do pamięci"}
                                                </button>
                                            ) : null}
                                        </li>
                                    );
                                })}
                                {httpSources.length > 1 ? (
                                    <li>
                                        <button
                                            type="button"
                                            className="text-xs font-medium text-[var(--chat-accent)] hover:underline disabled:opacity-50"
                                            disabled={ingestMutation.isPending}
                                            onClick={() => {
                                                for (const u of httpSources.slice(
                                                    0,
                                                    3,
                                                )) {
                                                    ingestMutation.mutate(u);
                                                }
                                            }}
                                        >
                                            Zapisz wszystkie URL (max 3)
                                        </button>
                                    </li>
                                ) : null}
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
    searchQ,
    onSearchQ,
    onSearch,
    searchBusy,
    searchHits,
    searchErr,
    onRememberFact,
    rememberBusy,
}: {
    loading: boolean;
    error: unknown;
    empty: boolean;
    items: MemoryContextPackItem[];
    facts: MemoryV2SummaryItem[];
    searchQ: string;
    onSearchQ: (v: string) => void;
    onSearch: () => void;
    searchBusy: boolean;
    searchHits: Array<{ title?: string; content?: string; id?: string }>;
    searchErr: string | null;
    onRememberFact: (fact: string) => void;
    rememberBusy: boolean;
}) {
    const [factDraft, setFactDraft] = useState("");

    return (
        <div className="space-y-4">
            <form
                className="space-y-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    onSearch();
                }}
            >
                <label className="block text-[10px] uppercase tracking-wider text-[var(--chat-text-muted)]">
                    Szukaj w pamięci
                </label>
                <div className="flex gap-2">
                    <input
                        value={searchQ}
                        onChange={(e) => onSearchQ(e.target.value)}
                        placeholder="np. preferencje, fakty…"
                        className="min-w-0 flex-1 rounded border border-[var(--chat-border)] bg-[#12151a] px-2 py-1.5 text-sm text-[var(--chat-text)] placeholder:text-[var(--chat-text-muted)]"
                    />
                    <button
                        type="submit"
                        disabled={searchBusy}
                        className="shrink-0 px-2 text-xs font-medium text-[var(--chat-accent)] hover:underline disabled:opacity-50"
                    >
                        {searchBusy ? "…" : "Szukaj"}
                    </button>
                </div>
                {searchErr ? (
                    <p className="text-xs text-[var(--chat-text-muted)]">{searchErr}</p>
                ) : null}
            </form>

            {searchHits.length > 0 ? (
                <div className="space-y-2">
                    <p className="text-[10px] uppercase tracking-wider text-[var(--chat-text-muted)]">
                        Wyniki wyszukiwania
                    </p>
                    {searchHits.map((hit, idx) => (
                        <div key={String(hit.id ?? idx)}>
                            <p className="text-sm font-medium text-[var(--chat-text)]">
                                {shortText(hit.title, "Wynik")}
                            </p>
                            <p className="mt-1 line-clamp-3 text-xs text-[var(--chat-text-muted)]">
                                {shortText(hit.content, "")}
                            </p>
                        </div>
                    ))}
                </div>
            ) : null}

            <form
                className="space-y-2 border-t border-[var(--chat-border)] pt-3"
                onSubmit={(e) => {
                    e.preventDefault();
                    onRememberFact(factDraft);
                    setFactDraft("");
                }}
            >
                <label className="block text-[10px] uppercase tracking-wider text-[var(--chat-text-muted)]">
                    Zapamiętaj fakt
                </label>
                <textarea
                    value={factDraft}
                    onChange={(e) => setFactDraft(e.target.value)}
                    rows={2}
                    placeholder="Krótki fakt do L2 / Memory V2…"
                    className="w-full rounded border border-[var(--chat-border)] bg-[#12151a] px-2 py-1.5 text-sm text-[var(--chat-text)] placeholder:text-[var(--chat-text-muted)]"
                />
                <button
                    type="submit"
                    disabled={rememberBusy || factDraft.trim().length < 3}
                    className="text-xs font-medium text-[var(--chat-accent)] hover:underline disabled:opacity-50"
                >
                    {rememberBusy ? "Zapisuję…" : "Zapisz fakt"}
                </button>
            </form>

            {loading ? (
                <p className="text-sm text-[var(--chat-text-muted)]">
                    Ładowanie pamięci…
                </p>
            ) : null}
            {error ? (
                <p className="text-sm text-[var(--chat-text-muted)]">
                    {formatMemoryErrorMessage(error)}
                </p>
            ) : null}
            {!loading && !error && empty ? (
                <p className="text-sm leading-relaxed text-[var(--chat-text-muted)]">
                    Pamięć jest jeszcze pusta. Będzie się budować w trakcie rozmowy
                    — albo zapisz fakt powyżej.
                </p>
            ) : null}
            {!loading && !error && !empty ? (
                <div className="space-y-3 border-t border-[var(--chat-border)] pt-3">
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
            ) : null}
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
