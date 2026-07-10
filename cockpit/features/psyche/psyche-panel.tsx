"use client";

import { useMutation } from "@tanstack/react-query";
import { Brain, HeartPulse } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { CoreLayerNote } from "@/features/shared/core-layer-note";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { PsycheStateResult } from "@/lib/api/types";
import { useCockpitStore } from "@/lib/store/cockpit-store";

import {
    reflectTopics,
    sentimentTone,
    toPsycheSignalView,
} from "./psyche-parser";

function toneBadge(tone: ReturnType<typeof sentimentTone>): {
    label: string;
    variant: "secondary" | "success" | "danger";
} {
    if (tone === "positive") return { label: "positive", variant: "success" };
    if (tone === "negative") return { label: "negative", variant: "danger" };
    return { label: "neutral", variant: "secondary" };
}

export function PsychePanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const [reflectQuery, setReflectQuery] = useState("");
    const [reflectLimit, setReflectLimit] = useState(10);
    const [sentimentText, setSentimentText] = useState("");
    const [evolveText, setEvolveText] = useState("");
    const [evolveRole, setEvolveRole] = useState<
        "user" | "assistant" | "system"
    >("user");

    const reflectMutation = useMutation({
        mutationFn: (input: { query: string; limit: number }) =>
            apiClient.psycheReflect(
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

    const sentimentMutation = useMutation({
        mutationFn: (text: string) =>
            apiClient.psycheAnalyzeSentiment(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    text,
                },
                apiKeyOverride || undefined,
            ),
    });

    const evolveMutation = useMutation({
        mutationFn: (input: {
            text: string;
            role: "user" | "assistant" | "system";
        }) =>
            apiClient.psycheEvolveState(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    text: input.text,
                    role: input.role,
                },
                apiKeyOverride || undefined,
            ),
    });

    const error =
        (reflectMutation.error as Error | null) ||
        (sentimentMutation.error as Error | null) ||
        (evolveMutation.error as Error | null);

    const latestState = useMemo(() => {
        if (evolveMutation.data) return evolveMutation.data;
        const state = reflectMutation.data?.state;
        if (state && typeof state === "object")
            return state as PsycheStateResult;
        return undefined;
    }, [evolveMutation.data, reflectMutation.data]);

    const signals = toPsycheSignalView(latestState);
    const tone = sentimentTone(sentimentMutation.data);
    const toneChip = toneBadge(tone);
    const topics = reflectTopics(reflectMutation.data);

    const submitReflect = async (e: FormEvent) => {
        e.preventDefault();
        await reflectMutation.mutateAsync({
            query: reflectQuery.trim(),
            limit: reflectLimit,
        });
    };

    const submitSentiment = async (e: FormEvent) => {
        e.preventDefault();
        if (!sentimentText.trim()) return;
        await sentimentMutation.mutateAsync(sentimentText.trim());
    };

    const submitEvolve = async (e: FormEvent) => {
        e.preventDefault();
        if (!evolveText.trim()) return;
        await evolveMutation.mutateAsync({
            text: evolveText.trim(),
            role: evolveRole,
        });
    };

    const isLoading =
        reflectMutation.isPending ||
        sentimentMutation.isPending ||
        evolveMutation.isPending;

    return (
        <Card className="h-full">
            <CardHeader className="space-y-3">
                <CardTitle>Cognitive State</CardTitle>
                <CoreLayerNote
                    icon={Brain}
                    title="Psyche to warstwa bazowa runtime"
                    description="Sentiment, reflect i evolve wpływają na tok działania modelu pod maską. Panel daje operatorowi transparentny wgląd i kontrolę nad realnym sygnałem stanu."
                />
            </CardHeader>

            <CardContent className="space-y-3">
                <div className="grid gap-3 lg:grid-cols-2">
                    <form
                        onSubmit={submitReflect}
                        className="space-y-2 rounded border border-border p-3"
                    >
                        <p className="text-sm font-semibold">psyche.reflect</p>
                        <Input
                            value={reflectQuery}
                            onChange={(e) => setReflectQuery(e.target.value)}
                            placeholder="Opcjonalne query do refleksji..."
                        />
                        <div className="grid grid-cols-[120px_1fr] gap-2">
                            <Input
                                type="number"
                                min={1}
                                max={50}
                                value={reflectLimit}
                                onChange={(e) =>
                                    setReflectLimit(
                                        Math.max(
                                            1,
                                            Math.min(
                                                50,
                                                Number(e.target.value) || 1,
                                            ),
                                        ),
                                    )
                                }
                            />
                            <Button type="submit" disabled={isLoading}>
                                Uruchom reflect
                            </Button>
                        </div>
                    </form>

                    <form
                        onSubmit={submitSentiment}
                        className="space-y-2 rounded border border-border p-3"
                    >
                        <p className="text-sm font-semibold">
                            psyche.analyze_sentiment
                        </p>
                        <Textarea
                            value={sentimentText}
                            onChange={(e) => setSentimentText(e.target.value)}
                            className="min-h-[96px]"
                            placeholder="Tekst do analizy sentymentu..."
                        />
                        <Button
                            type="submit"
                            disabled={isLoading || !sentimentText.trim()}
                        >
                            Analizuj sentyment
                        </Button>
                    </form>
                </div>

                <form
                    onSubmit={submitEvolve}
                    className="space-y-2 rounded border border-border p-3"
                >
                    <p className="text-sm font-semibold">psyche.evolve_state</p>
                    <div className="grid gap-2 lg:grid-cols-[1fr_180px_auto]">
                        <Textarea
                            value={evolveText}
                            onChange={(e) => setEvolveText(e.target.value)}
                            className="min-h-[84px]"
                            placeholder="Sygnał tekstowy do aktualizacji stanu psyche..."
                        />
                        <Select
                            value={evolveRole}
                            onValueChange={(v: string) =>
                                setEvolveRole(
                                    v as "user" | "assistant" | "system",
                                )
                            }
                        >
                            <SelectTrigger>
                                <SelectValue placeholder="role" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="user">user</SelectItem>
                                <SelectItem value="assistant">
                                    assistant
                                </SelectItem>
                                <SelectItem value="system">system</SelectItem>
                            </SelectContent>
                        </Select>
                        <Button
                            type="submit"
                            disabled={isLoading || !evolveText.trim()}
                        >
                            Evolve state
                        </Button>
                    </div>
                </form>

                {isLoading ? (
                    <p className="text-sm text-muted-foreground">
                        Przetwarzanie sygnałów cognitive state…
                    </p>
                ) : null}

                {error ? (
                    <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-sm text-red-300">
                        Psyche layer error: {error.message}
                    </div>
                ) : null}

                {!latestState &&
                !reflectMutation.data &&
                !sentimentMutation.data &&
                !isLoading ? (
                    <EmptyState
                        icon={HeartPulse}
                        title="Brak danych stanu"
                        description="Uruchom reflect, sentiment albo evolve, aby zobaczyć realny stan warstwy cognitive."
                    />
                ) : null}

                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                    <Badge variant="outline">
                        mood {signals.mood?.toFixed(3) ?? "—"}
                    </Badge>
                    <Badge variant="outline">
                        energy {signals.energy?.toFixed(3) ?? "—"}
                    </Badge>
                    <Badge variant="outline">
                        focus {signals.focus?.toFixed(3) ?? "—"}
                    </Badge>
                    <Badge variant="outline">
                        temp {signals.temperature?.toFixed(3) ?? "—"}
                    </Badge>
                    <Badge variant="secondary">style {signals.style}</Badge>
                </div>

                {sentimentMutation.data ? (
                    <div className="rounded border border-border p-2 text-xs">
                        <div className="mb-1 flex items-center gap-2">
                            <Badge variant={toneChip.variant}>
                                {toneChip.label}
                            </Badge>
                            <Badge variant="outline">
                                score{" "}
                                {Number(
                                    sentimentMutation.data.sentiment ?? 0,
                                ).toFixed(3)}
                            </Badge>
                            <Badge variant="outline">
                                conf{" "}
                                {Number(
                                    sentimentMutation.data.confidence ?? 0,
                                ).toFixed(3)}
                            </Badge>
                        </div>
                        <JsonView
                            title="sentiment.meta"
                            value={sentimentMutation.data.meta ?? {}}
                            compact
                        />
                    </div>
                ) : null}

                {reflectMutation.data ? (
                    <div className="rounded border border-border p-2 text-xs">
                        <p className="mb-1 text-sm font-semibold">Reflection</p>
                        <p className="mb-2 text-muted-foreground">
                            {typeof reflectMutation.data.reflection === "string"
                                ? reflectMutation.data.reflection
                                : "Brak tekstu refleksji"}
                        </p>
                        <div className="flex flex-wrap gap-1">
                            {topics.length ? (
                                topics.map((topic) => (
                                    <Badge key={topic} variant="secondary">
                                        {topic}
                                    </Badge>
                                ))
                            ) : (
                                <Badge variant="outline">Brak topiców</Badge>
                            )}
                        </div>
                    </div>
                ) : null}

                {latestState ? (
                    <JsonView
                        title="psyche.state"
                        value={latestState}
                        compact
                    />
                ) : null}
                <JsonView
                    title="psyche.reflect"
                    value={reflectMutation.data ?? {}}
                    compact
                />
                <JsonView
                    title="psyche.analyze_sentiment"
                    value={sentimentMutation.data ?? {}}
                    compact
                />
                <JsonView
                    title="psyche.evolve_state"
                    value={evolveMutation.data ?? {}}
                    compact
                />
            </CardContent>
        </Card>
    );
}
