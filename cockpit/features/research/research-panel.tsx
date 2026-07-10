"use client";

import { useMutation } from "@tanstack/react-query";
import { Globe, Search } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { CoreLayerNote } from "@/features/shared/core-layer-note";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

import {
    summarizeResearchUrl,
    summarizeWebFetch,
    toResearchViewModel,
} from "./research-parser";

export function ResearchPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const [query, setQuery] = useState("");
    const [researchType, setResearchType] = useState("general");
    const [url, setUrl] = useState("");

    const queryMutation = useMutation({
        mutationFn: (input: { query: string; researchType: string }) =>
            apiClient.researchQuery(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    query: input.query,
                    research_type: input.researchType,
                },
                apiKeyOverride || undefined,
            ),
    });

    const researchUrlMutation = useMutation({
        mutationFn: (targetUrl: string) =>
            apiClient.researchUrl(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    url: targetUrl,
                },
                apiKeyOverride || undefined,
            ),
    });

    const webFetchMutation = useMutation({
        mutationFn: (targetUrl: string) =>
            apiClient.webFetchUrl(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    url: targetUrl,
                },
                apiKeyOverride || undefined,
            ),
    });

    const isLoading =
        queryMutation.isPending ||
        researchUrlMutation.isPending ||
        webFetchMutation.isPending;

    const error =
        (queryMutation.error as Error | null) ||
        (researchUrlMutation.error as Error | null) ||
        (webFetchMutation.error as Error | null);

    const queryVm = useMemo(
        () => toResearchViewModel(queryMutation.data),
        [queryMutation.data],
    );
    const researchUrlVm = useMemo(
        () => summarizeResearchUrl(researchUrlMutation.data),
        [researchUrlMutation.data],
    );
    const webFetchVm = useMemo(
        () => summarizeWebFetch(webFetchMutation.data),
        [webFetchMutation.data],
    );

    const submitQuery = async (e: FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;
        await queryMutation.mutateAsync({
            query: query.trim(),
            researchType,
        });
    };

    const submitResearchUrl = async () => {
        if (!url.trim()) return;
        await researchUrlMutation.mutateAsync(url.trim());
    };

    const submitWebFetch = async () => {
        if (!url.trim()) return;
        await webFetchMutation.mutateAsync(url.trim());
    };

    return (
        <Card className="h-full">
            <CardHeader className="space-y-3">
                <CardTitle>Web/Research Layer</CardTitle>
                <CoreLayerNote
                    icon={Globe}
                    title="Web + research to warstwa bazowa runtime"
                    description="To nie marketplace pluginów. Model stale może sięgać po warstwę web/research — panel pokazuje realne wyniki i ich wpływ na bazę faktów."
                />

                <form
                    onSubmit={submitQuery}
                    className="grid gap-2 lg:grid-cols-[1fr_180px_auto]"
                >
                    <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Query do research.query..."
                    />
                    <Select
                        value={researchType}
                        onValueChange={setResearchType}
                    >
                        <SelectTrigger>
                            <SelectValue placeholder="research type" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="general">general</SelectItem>
                            <SelectItem value="broad">broad</SelectItem>
                            <SelectItem value="deep">deep</SelectItem>
                            <SelectItem value="targeted">targeted</SelectItem>
                        </SelectContent>
                    </Select>
                    <Button type="submit" disabled={isLoading || !query.trim()}>
                        <Search className="mr-1 h-4 w-4" />
                        research.query
                    </Button>
                </form>

                <div className="grid gap-2 lg:grid-cols-[1fr_auto_auto]">
                    <Input
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        placeholder="URL do research.url / web.fetch_url"
                    />
                    <Button
                        variant="outline"
                        disabled={isLoading || !url.trim()}
                        onClick={() => void submitResearchUrl()}
                    >
                        research.url
                    </Button>
                    <Button
                        variant="secondary"
                        disabled={isLoading || !url.trim()}
                        onClick={() => void submitWebFetch()}
                    >
                        web.fetch_url
                    </Button>
                </div>
            </CardHeader>

            <CardContent className="space-y-3">
                {isLoading ? (
                    <p className="text-sm text-muted-foreground">
                        Ładowanie warstwy web/research…
                    </p>
                ) : null}

                {error ? (
                    <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-sm text-red-300">
                        Research/Web layer error: {error.message}
                    </div>
                ) : null}

                {!isLoading &&
                !error &&
                queryVm.totalResults === 0 &&
                !researchUrlMutation.data &&
                !webFetchMutation.data ? (
                    <EmptyState
                        icon={Globe}
                        title="Brak wyników research"
                        description="Uruchom research.query lub podaj URL, żeby zobaczyć realny output warstwy web/research."
                    />
                ) : null}

                <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">
                        results {queryVm.totalResults}
                    </Badge>
                    <Badge variant="outline">facts {queryVm.totalFacts}</Badge>
                    {researchUrlVm.status !== null ? (
                        <Badge variant="secondary">
                            research.url status {researchUrlVm.status}
                        </Badge>
                    ) : null}
                    {webFetchVm.status !== null ? (
                        <Badge variant={webFetchVm.ok ? "success" : "warning"}>
                            web.fetch_url status {webFetchVm.status}
                        </Badge>
                    ) : null}
                </div>

                {!!queryVm.rows.length ? (
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                                research.query results
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {queryVm.rows.slice(0, 12).map((row) => (
                                <div
                                    key={`${row.source}_${row.url}_${row.title}`}
                                    className="rounded border border-border p-2 text-xs"
                                >
                                    <p className="font-semibold">{row.title}</p>
                                    {row.url ? (
                                        <a
                                            href={row.url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="break-all text-primary underline"
                                        >
                                            {row.url}
                                        </a>
                                    ) : null}
                                    <div className="mt-1 flex flex-wrap gap-2 text-muted-foreground">
                                        <span>source {row.source}</span>
                                        <span>
                                            relevance {row.relevance.toFixed(3)}
                                        </span>
                                        <span>facts {row.factsExtracted}</span>
                                    </div>
                                </div>
                            ))}
                        </CardContent>
                    </Card>
                ) : null}

                {researchUrlMutation.data ? (
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                                research.url summary
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2 text-xs">
                            <p>
                                <span className="text-muted-foreground">
                                    url:
                                </span>{" "}
                                {researchUrlVm.url || "—"}
                            </p>
                            <p>
                                <span className="text-muted-foreground">
                                    status:
                                </span>{" "}
                                {researchUrlVm.status ?? "—"}
                            </p>
                            <p>
                                <span className="text-muted-foreground">
                                    bytes:
                                </span>{" "}
                                {researchUrlVm.bytes ?? "—"}
                            </p>
                            {researchUrlVm.preview ? (
                                <p className="rounded border border-border bg-card/40 p-2 text-muted-foreground">
                                    {researchUrlVm.preview}
                                </p>
                            ) : null}
                        </CardContent>
                    </Card>
                ) : null}

                {webFetchMutation.data ? (
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">
                                web.fetch_url output
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2 text-xs">
                            <p>
                                <span className="text-muted-foreground">
                                    url:
                                </span>{" "}
                                {webFetchVm.url || "—"}
                            </p>
                            <p>
                                <span className="text-muted-foreground">
                                    status:
                                </span>{" "}
                                {webFetchVm.status ?? "—"}
                            </p>
                            <p>
                                <span className="text-muted-foreground">
                                    bytes:
                                </span>{" "}
                                {webFetchVm.bytes ?? "—"}
                            </p>
                            {webFetchVm.textPreview ? (
                                <pre className="max-h-72 overflow-auto rounded border border-border bg-card/40 p-2 whitespace-pre-wrap text-muted-foreground">
                                    {webFetchVm.textPreview}
                                </pre>
                            ) : (
                                <p className="text-muted-foreground">
                                    Brak tekstu zwróconego przez web.fetch_url.
                                </p>
                            )}
                        </CardContent>
                    </Card>
                ) : null}

                <JsonView
                    title="research.query payload"
                    value={queryMutation.data ?? {}}
                    compact
                />
                <JsonView
                    title="research.url payload"
                    value={researchUrlMutation.data ?? {}}
                    compact
                />
                <JsonView
                    title="web.fetch_url payload"
                    value={webFetchMutation.data ?? {}}
                    compact
                />
            </CardContent>
        </Card>
    );
}
