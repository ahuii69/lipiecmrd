"use client";

import { useMutation } from "@tanstack/react-query";
import { BrainCircuit, Database, Layers, Search, Sparkles } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { CoreLayerNote } from "@/features/shared/core-layer-note";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { MemoryContextResult } from "@/lib/api/types";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

import { toMemoryViewModel } from "./memory-parser";

function rowText(row: Record<string, unknown>): string {
    const { content } = row;
    if (typeof content === "string" && content.trim()) return content;
    return JSON.stringify(row);
}

function rowId(row: Record<string, unknown>, idx: number): string {
    const { id } = row;
    if (typeof id === "string" && id.trim()) return id;
    return `row_${idx}`;
}

function rowTs(row: Record<string, unknown>): number | null {
    const { ts } = row;
    if (typeof ts === "number" && Number.isFinite(ts)) return ts * 1000;
    return null;
}

export function MemoryPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const [query, setQuery] = useState("");
    const [limit, setLimit] = useState(10);
    const [v2ExplainQuery, setV2ExplainQuery] = useState("");
    const [forgetThreshold, setForgetThreshold] = useState(0.15);

    const searchMutation = useMutation({
        mutationFn: (input: { query: string; limit: number }) =>
            apiClient.memorySearch(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    query: input.query,
                    limit: input.limit,
                },
                apiKeyOverride || undefined,
            ),
    });

    const v2ExplainMutation = useMutation({
        mutationFn: () =>
            apiClient.getMemoryV2RetrievalExplain(
                session.userId,
                {
                    query: v2ExplainQuery.trim() || undefined,
                    top_n: 10,
                },
                apiKeyOverride || undefined,
            ),
    });

    const v2ForgetMutation = useMutation({
        mutationFn: () =>
            apiClient.runMemoryV2ForgettingSweep(
                session.userId,
                { threshold: forgetThreshold },
                apiKeyOverride || undefined,
            ),
    });

    const v2SummaryMutation = useMutation({
        mutationFn: () =>
            apiClient.getMemoryV2Summary(
                session.userId,
                apiKeyOverride || undefined,
            ),
    });

    const contextMutation = useMutation({
        mutationFn: (input: { query: string; limit: number }) =>
            apiClient.memoryGetContext(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    query: input.query,
                    limit: input.limit,
                },
                apiKeyOverride || undefined,
            ),
    });

    const memoryData: MemoryContextResult | undefined =
        contextMutation.data ?? searchMutation.data;

    const vm = useMemo(() => toMemoryViewModel(memoryData), [memoryData]);

    const isLoading = searchMutation.isPending || contextMutation.isPending;
    const error =
        (searchMutation.error as Error | null) ||
        (contextMutation.error as Error | null);

    const submitSearch = async (e: FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;
        await searchMutation.mutateAsync({ query: query.trim(), limit });
    };

    const submitContext = async () => {
        await contextMutation.mutateAsync({ query: query.trim(), limit });
    };

    return (
        <Card className="h-full">
            <CardHeader className="space-y-3">
                <CardTitle>Context Memory</CardTitle>
                <CoreLayerNote
                    icon={Database}
                    title="Memory to warstwa bazowa runtime"
                    description="AI-Hub stale korzysta z kontekstu STM + episodic + semantic. Ten panel pokazuje skutki i pozwala operatorowi je przejrzeć bez grzebania w surowych logach."
                />
                <form
                    onSubmit={submitSearch}
                    className="grid gap-2 md:grid-cols-[1fr_120px_auto_auto]"
                >
                    <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Zapytanie kontekstowe do warstwy pamięci..."
                    />
                    <Input
                        type="number"
                        min={1}
                        max={50}
                        value={limit}
                        onChange={(e) =>
                            setLimit(
                                Math.max(
                                    1,
                                    Math.min(50, Number(e.target.value) || 1),
                                ),
                            )
                        }
                    />
                    <Button type="submit" disabled={isLoading || !query.trim()}>
                        <Search className="mr-1 h-4 w-4" />
                        memory.search
                    </Button>
                    <Button
                        type="button"
                        variant="outline"
                        disabled={isLoading}
                        onClick={() => void submitContext()}
                    >
                        memory.get_context
                    </Button>
                </form>
            </CardHeader>

            <CardContent className="space-y-3">
                {isLoading ? (
                    <p className="text-sm text-muted-foreground">
                        Ładowanie warstwy pamięci…
                    </p>
                ) : null}

                {error ? (
                    <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-sm text-red-300">
                        Memory layer error: {error.message}
                    </div>
                ) : null}

                {!isLoading && !error && vm.total === 0 ? (
                    <EmptyState
                        icon={BrainCircuit}
                        title="Brak trafień kontekstowych"
                        description="To nie wyłącza memory runtime. Oznacza tylko, że dla tego zapytania backend nie zwrócił epizodów/faktów."
                    />
                ) : null}

                <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
                    <Badge variant="secondary">total {vm.total}</Badge>
                    <Badge variant="outline">stm {vm.stmCount}</Badge>
                    <Badge variant="outline">episodes {vm.episodicCount}</Badge>
                    <Badge variant="outline">facts {vm.semanticCount}</Badge>
                    <Badge variant="outline">dense {vm.denseCount}</Badge>
                    <Badge variant="outline">graph {vm.graphCount}</Badge>
                </div>

                {!!vm.episodicCount ? (
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                                Episodes (L1)
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {vm.episodic.slice(0, 8).map((row, idx) => (
                                <div
                                    key={rowId(row, idx)}
                                    className="rounded border border-border p-2 text-xs"
                                >
                                    <p className="line-clamp-3">
                                        {rowText(row)}
                                    </p>
                                    <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                                        {typeof row.score === "number" ? (
                                            <span>
                                                score {row.score.toFixed(3)}
                                            </span>
                                        ) : null}
                                        {rowTs(row) ? (
                                            <span>{formatTs(rowTs(row)!)}</span>
                                        ) : null}
                                    </div>
                                </div>
                            ))}
                        </CardContent>
                    </Card>
                ) : null}

                {!!vm.semanticCount ? (
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                                Facts (L2)
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {vm.semantic.slice(0, 8).map((row, idx) => (
                                <div
                                    key={rowId(row, idx)}
                                    className="rounded border border-border p-2 text-xs"
                                >
                                    <p className="line-clamp-3">
                                        {rowText(row)}
                                    </p>
                                    <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                                        {typeof row.score === "number" ? (
                                            <span>
                                                score {row.score.toFixed(3)}
                                            </span>
                                        ) : null}
                                        {rowTs(row) ? (
                                            <span>{formatTs(rowTs(row)!)}</span>
                                        ) : null}
                                    </div>
                                </div>
                            ))}
                        </CardContent>
                    </Card>
                ) : null}

                {!!vm.stmCount ? (
                    <JsonView title="STM context" value={vm.stm} compact />
                ) : null}
                {!!vm.denseCount ? (
                    <JsonView title="Dense hits" value={vm.denseHits} compact />
                ) : null}
                {!!vm.graphCount ? (
                    <JsonView title="Graph hits" value={vm.graphHits} compact />
                ) : null}
                {memoryData ? (
                    <JsonView
                        title="Memory payload"
                        value={memoryData}
                        compact
                    />
                ) : null}

                <Card className="shadow-none border-dashed border-white/10">
                    <CardHeader className="space-y-2 pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm">
                            <Layers className="h-4 w-4" />
                            Memory V2 — bezpośredni HTTP (przez BFF)
                        </CardTitle>
                        <p className="text-xs text-muted-foreground">
                            Te wywołania idą na <code className="text-[11px]">/api/aihub/…</code> i
                            muszą być na allowliście proxy — retrieval-explain, forgetting, summary
                            V2.
                        </p>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="flex flex-wrap gap-2">
                            <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                disabled={v2SummaryMutation.isPending}
                                onClick={() => void v2SummaryMutation.mutateAsync()}
                            >
                                GET summary
                            </Button>
                            <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                disabled={v2ForgetMutation.isPending}
                                onClick={() => void v2ForgetMutation.mutateAsync()}
                            >
                                POST forgetting sweep
                            </Button>
                        </div>
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                            <Input
                                className="sm:max-w-md"
                                value={v2ExplainQuery}
                                onChange={(e) => setV2ExplainQuery(e.target.value)}
                                placeholder="Opcjonalne query do retrieval-explain…"
                            />
                            <Button
                                type="button"
                                size="sm"
                                variant="default"
                                disabled={v2ExplainMutation.isPending}
                                onClick={() => void v2ExplainMutation.mutateAsync()}
                            >
                                <Sparkles className="mr-1 h-3.5 w-3.5" />
                                GET retrieval-explain
                            </Button>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span>Próg forgetting:</span>
                            <Input
                                type="number"
                                step={0.05}
                                min={0.05}
                                max={0.95}
                                className="h-8 w-24"
                                value={forgetThreshold}
                                onChange={(e) =>
                                    setForgetThreshold(
                                        Math.min(
                                            0.95,
                                            Math.max(
                                                0.05,
                                                Number(e.target.value) || 0.15,
                                            ),
                                        ),
                                    )
                                }
                            />
                        </div>
                        {v2SummaryMutation.data ? (
                            <JsonView
                                title="Memory V2 summary"
                                value={v2SummaryMutation.data}
                                compact
                            />
                        ) : null}
                        {v2ForgetMutation.data ? (
                            <JsonView
                                title="Forgetting sweep"
                                value={v2ForgetMutation.data}
                                compact
                            />
                        ) : null}
                        {v2ExplainMutation.data ? (
                            <JsonView
                                title="Retrieval explain"
                                value={v2ExplainMutation.data}
                                compact
                            />
                        ) : null}
                        {(v2SummaryMutation.error ||
                            v2ForgetMutation.error ||
                            v2ExplainMutation.error) ? (
                            <div className="rounded-md border border-amber-800/50 bg-amber-950/40 p-2 text-xs text-amber-200">
                                {String(
                                    (v2ExplainMutation.error ||
                                        v2ForgetMutation.error ||
                                        v2SummaryMutation.error) as Error,
                                )}
                            </div>
                        ) : null}
                    </CardContent>
                </Card>
            </CardContent>
        </Card>
    );
}
