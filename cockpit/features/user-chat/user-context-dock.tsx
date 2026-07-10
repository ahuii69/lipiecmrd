"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
    Activity,
    Brain,
    CheckCircle2,
    Cpu,
    Database,
    RefreshCw,
    ShieldCheck,
    Sparkles,
    TriangleAlert,
    Wifi,
    WifiOff,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api/client";
import type { MemoryV2SummaryResponse } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function boolLabel(v: unknown): "ok" | "warn" | "off" {
    if (v === true || v === "ok" || v === "healthy" || v === "ready") return "ok";
    if (v === false || v === "error" || v === "failed") return "off";
    return "warn";
}

function statusColor(status: "ok" | "warn" | "off"): string {
    if (status === "ok") return "border-emerald-400/25 bg-emerald-400/10 text-emerald-200";
    if (status === "off") return "border-red-400/25 bg-red-400/10 text-red-200";
    return "border-amber-400/25 bg-amber-400/10 text-amber-100";
}

function shortText(value: unknown, fallback: string): string {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return fallback;
}

function memoryTotal(data?: MemoryV2SummaryResponse): number {
    if (!data) return 0;
    if (typeof data.total_items === "number") return data.total_items;
    const buckets = [data.facts, data.preferences, data.key_settlements, data.procedures_highlight];
    return buckets.reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0);
}

function StatCard({ icon, label, value, tone = "neutral" }: { icon: ReactNode; label: string; value: string; tone?: "neutral" | "ok" | "warn" | "off" }) {
    const toneClass = tone === "neutral" ? "border-white/10 bg-white/[0.035] text-neutral-200" : statusColor(tone);
    return (
        <div className={cn("rounded-2xl border p-3 shadow-sm", toneClass)}>
            <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-current/70">{icon}</span>
                <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
            </div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-current/65">{label}</p>
            <p className="mt-1 truncate text-sm font-semibold text-current">{value}</p>
        </div>
    );
}

export function UserContextDock({ userId, sessionId, apiKeyOverride, liveActivity, messageCount, fileCount }: { userId: string; sessionId: string; apiKeyOverride?: string; liveActivity: string | null; messageCount: number; fileCount: number }) {
    const ping = useQuery({ queryKey: ["chat-shell-ping", apiKeyOverride], queryFn: () => apiClient.runtimePing(apiKeyOverride), retry: 1, refetchInterval: 30_000 });
    const health = useQuery({ queryKey: ["chat-shell-ops-health", apiKeyOverride], queryFn: () => apiClient.opsHealth(apiKeyOverride), retry: 1, refetchInterval: 45_000 });
    const readiness = useQuery({ queryKey: ["chat-shell-ops-ready", apiKeyOverride], queryFn: () => apiClient.opsReady(apiKeyOverride), retry: 1, refetchInterval: 45_000 });
    const capabilities = useQuery({ queryKey: ["chat-shell-ops-capabilities", apiKeyOverride], queryFn: () => apiClient.opsCapabilities(apiKeyOverride), retry: 1, refetchInterval: 45_000 });
    const memory = useQuery({ queryKey: ["chat-shell-memory", userId, apiKeyOverride], queryFn: () => apiClient.getMemoryV2Summary(userId, apiKeyOverride), enabled: Boolean(userId && userId !== "default"), retry: 1, refetchInterval: 60_000 });
    const contextPack = useQuery({
        queryKey: ["chat-shell-memory-context-pack", userId, apiKeyOverride],
        queryFn: () => apiClient.getMemoryContextPack({ user_id: userId, query: "profil użytkownika preferencje procedury najważniejsze fakty", limit: 10, max_chars: 6000, include_graph: true }, apiKeyOverride),
        enabled: Boolean(userId && userId !== "default"),
        retry: 1,
        refetchInterval: 75_000,
    });
    const indexJobs = useQuery({ queryKey: ["chat-shell-memory-index-jobs", userId, apiKeyOverride], queryFn: () => apiClient.getMemoryV2IndexJobs(userId, apiKeyOverride), enabled: Boolean(userId && userId !== "default"), retry: 1, refetchInterval: 75_000 });
    const runtime = useQuery({ queryKey: ["chat-shell-runtime", userId, apiKeyOverride], queryFn: () => apiClient.runtimeStatus(userId, apiKeyOverride), enabled: Boolean(userId && userId !== "default"), retry: 1, refetchInterval: 60_000 });

    const pingTone: "ok" | "warn" | "off" = ping.isError ? "off" : ping.data ? "ok" : "warn";
    const readyRaw = readiness.data as Record<string, unknown> | undefined;
    const readyTone: "ok" | "warn" | "off" = readiness.isError ? "off" : readyRaw?.ready === true ? "ok" : readyRaw ? "off" : "warn";
    const capabilityRaw = capabilities.data as Record<string, unknown> | undefined;
    const capabilityMap = (capabilityRaw?.capabilities && typeof capabilityRaw.capabilities === "object" ? capabilityRaw.capabilities : {}) as Record<string, unknown>;
    const semanticMemoryReady = Boolean(capabilityMap.memory_semantic_index);
    const healthRaw = health.data as Record<string, unknown> | undefined;
    const healthLayers = (healthRaw?.layers && typeof healthRaw.layers === "object" ? healthRaw.layers : {}) as Record<string, Record<string, unknown>>;
    const vectorTone = boolLabel(healthLayers.vector?.status || healthRaw?.vector || healthRaw?.vector_ready || healthRaw?.vector_health || healthRaw?.memory_vector);
    const dbTone = boolLabel(healthLayers.database?.status || healthRaw?.db || healthRaw?.database || healthRaw?.db_ok);
    const indexCounts = indexJobs.data?.counts ?? {};
    const indexPending = Number(indexCounts.pending ?? 0) + Number(indexCounts.stale ?? 0);
    const indexFailed = Number(indexCounts.failed ?? 0);
    const indexTone: "ok" | "warn" | "off" = indexJobs.isError || indexFailed > 0 ? "off" : indexPending > 0 ? "warn" : indexJobs.data ? "ok" : "warn";
    const packedItems = [
        ...(contextPack.data?.preferences ?? []),
        ...(contextPack.data?.facts ?? []),
        ...(contextPack.data?.procedures ?? []),
        ...(contextPack.data?.episodes ?? []),
        ...(contextPack.data?.contradictions ?? []),
        ...(contextPack.data?.other ?? []),
    ];

    return (
        <aside className="hidden h-full w-[22rem] shrink-0 border-l border-white/[0.08] bg-neutral-950/80 px-4 py-4 backdrop-blur-2xl xl:flex xl:flex-col">
            <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                    <p className="text-sm font-bold tracking-tight text-neutral-100">AI-Hub runtime</p>
                    <p className="mt-0.5 text-xs text-neutral-500">realny backend, pamięć i sesja</p>
                </div>
                <Button type="button" variant="ghost" size="icon" className="h-9 w-9 rounded-xl text-neutral-400 hover:bg-white/10 hover:text-white" onClick={() => { void ping.refetch(); void health.refetch(); void readiness.refetch(); void capabilities.refetch(); void memory.refetch(); void contextPack.refetch(); void indexJobs.refetch(); void runtime.refetch(); }} aria-label="Odśwież status">
                    <RefreshCw className="h-4 w-4" />
                </Button>
            </div>
            <div className="grid grid-cols-2 gap-3">
                <StatCard icon={pingTone === "ok" ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />} label="Hub" value={pingTone === "ok" ? "online" : ping.isError ? "offline" : "sprawdzam"} tone={pingTone} />
                <StatCard icon={<ShieldCheck className="h-4 w-4" />} label="Ready" value={readyTone === "ok" ? "gotowy" : readyTone === "off" ? "blokada" : "sprawdzam"} tone={readyTone} />
                <StatCard icon={<Database className="h-4 w-4" />} label="DB" value={dbTone === "ok" ? "gotowa" : dbTone === "off" ? "problem" : "status"} tone={dbTone} />
                <StatCard icon={<Brain className="h-4 w-4" />} label="Memory V2" value={`${memoryTotal(memory.data)} wpisów`} tone={memory.isError ? "off" : memory.data ? "ok" : "warn"} />
                <StatCard icon={<Cpu className="h-4 w-4" />} label="Vector" value={vectorTone === "ok" ? "aktywny" : vectorTone === "off" ? "offline" : semanticMemoryReady ? "fallback" : "degraded"} tone={vectorTone} />
                <StatCard icon={<RefreshCw className="h-4 w-4" />} label="Index jobs" value={indexTone === "ok" ? "czysto" : indexFailed > 0 ? `${indexFailed} fail` : `${indexPending} pending`} tone={indexTone} />
            </div>
            <div className="mt-4 rounded-3xl border border-white/10 bg-white/[0.035] p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-neutral-100"><Activity className="h-4 w-4 text-emerald-300" />Aktywna rozmowa</div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                    <div><dt className="text-xs text-neutral-500">Sesja</dt><dd className="mt-0.5 truncate font-medium text-neutral-200" title={sessionId}>{sessionId}</dd></div>
                    <div><dt className="text-xs text-neutral-500">Profil</dt><dd className="mt-0.5 truncate font-medium text-neutral-200" title={userId}>{userId}</dd></div>
                    <div><dt className="text-xs text-neutral-500">Wiadomości</dt><dd className="mt-0.5 font-medium text-neutral-200">{messageCount}</dd></div>
                    <div><dt className="text-xs text-neutral-500">Pliki w turze</dt><dd className="mt-0.5 font-medium text-neutral-200">{fileCount}</dd></div>
                </dl>
            </div>
            <div className="mt-4 rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-4">
                <div className="flex items-start gap-3"><div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-2 text-emerald-200">{liveActivity ? <Sparkles className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}</div><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-neutral-100">{liveActivity ? "Pracuję" : "Gotowy do rozmowy"}</p><p className="mt-1 text-sm leading-relaxed text-neutral-400">{liveActivity ?? "Streaming, historia sesji, upload, STT i pamięć idą przez backend AI-Hub."}</p></div></div>
            </div>
            <div className="mt-4 min-h-0 flex-1 overflow-hidden rounded-3xl border border-white/10 bg-neutral-900/45 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-neutral-100"><Brain className="h-4 w-4 text-violet-300" />Najważniejsze z pamięci</div>
                {memory.isLoading ? <p className="text-sm text-neutral-500">Ładowanie pamięci…</p> : memory.isError ? <div className="flex gap-2 rounded-2xl border border-red-400/20 bg-red-400/10 p-3 text-sm text-red-100"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />Nie udało się pobrać pamięci.</div> : (
                    <div className="space-y-2 overflow-y-auto pr-1 text-sm [scrollbar-width:thin]">
                        {packedItems.slice(0, 5).map((item, idx) => <div key={String(item.id ?? idx)} className="rounded-2xl border border-white/8 bg-white/[0.035] p-3"><p className="font-medium text-neutral-200">{shortText(item.title || item.memory_type, "Pamięć")}</p><p className="mt-1 line-clamp-3 text-neutral-500">{shortText(item.content, "Brak treści")}</p><p className="mt-2 truncate text-[11px] text-neutral-600">{shortText(item.source, "context-pack")}</p></div>)}
                        {packedItems.length === 0 ? (memory.data?.facts ?? []).slice(0, 3).map((item, idx) => <div key={String(item.id ?? idx)} className="rounded-2xl border border-white/8 bg-white/[0.035] p-3"><p className="font-medium text-neutral-200">{shortText(item.title ?? item.label, "Fakt")}</p><p className="mt-1 line-clamp-3 text-neutral-500">{shortText(item.content, "Brak treści")}</p></div>) : null}
                        {memoryTotal(memory.data) === 0 && packedItems.length === 0 ? <p className="text-sm text-neutral-500">Brak zapisanych wpisów dla tego profilu.</p> : null}
                    </div>
                )}
            </div>
            <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-3 text-xs text-neutral-500">Provider runtime: {shortText((runtime.data as Record<string, unknown> | undefined)?.provider, "backend")}</div>
        </aside>
    );
}
