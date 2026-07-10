"use client";

import { useMutation } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import {
    normalizeReasoningPreview,
    type ReasoningTaskView,
} from "./reasoning-parser";

function ReasoningTaskCard({ task }: { task: ReasoningTaskView }) {
    const payloadEntries = Object.entries(task.payload ?? {}).slice(0, 3);

    return (
        <Card className="shadow-none">
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-xs font-semibold">
                        {task.type}
                    </CardTitle>
                    <Badge variant="outline" className="text-xs">
                        prio {task.priority}
                    </Badge>
                </div>
                <p className="text-xs text-muted-foreground break-all">
                    {task.id}
                </p>
                {task.summary ? (
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                        {task.summary}
                    </p>
                ) : null}
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
                <div>
                    <p className="mb-1 font-semibold">Zależności:</p>
                    {task.dependsOn.length ? (
                        <div className="flex flex-wrap gap-1">
                            {task.dependsOn.map((dep) => (
                                <Badge key={dep} variant="secondary">
                                    {dep}
                                </Badge>
                            ))}
                        </div>
                    ) : (
                        <p className="text-muted-foreground">
                            Brak zależności.
                        </p>
                    )}
                </div>
                {payloadEntries.length ? (
                    <div className="text-muted-foreground">
                        {payloadEntries.map(([key, value]) => (
                            <p key={key}>
                                {key}: {String(value)}
                            </p>
                        ))}
                    </div>
                ) : (
                    <p className="text-muted-foreground">Brak payloadu.</p>
                )}
            </CardContent>
        </Card>
    );
}

export function ReasoningPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
    const [text, setText] = useState(
        "Przeanalizuj co wiemy o celach usera i zaproponuj kolejne kroki.",
    );

    const reasoningMutation = useMutation({
        mutationFn: async (message: string) =>
            apiClient.reasoningRunPreview(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    text: message,
                },
                apiKeyOverride || undefined,
            ),
    });

    const previewView = useMemo(
        () => normalizeReasoningPreview(reasoningMutation.data),
        [reasoningMutation.data],
    );

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        if (!text.trim()) return;
        await reasoningMutation.mutateAsync(text.trim());
    };

    return (
        <Card className="h-full">
            <CardHeader>
                <CardTitle>Reasoning — preview-only</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <form onSubmit={submit} className="space-y-2">
                    <Textarea
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        className="min-h-[110px]"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge variant="warning">
                            Preview only — bez pełnej egzekucji
                        </Badge>
                        <Button
                            type="submit"
                            disabled={
                                reasoningMutation.isPending || !text.trim()
                            }
                        >
                            {reasoningMutation.isPending
                                ? "Buduję preview…"
                                : "Uruchom preview"}
                        </Button>
                    </div>
                </form>

                {reasoningMutation.isError ? (
                    <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-sm text-red-300">
                        Reasoning preview nie działa:{" "}
                        {(reasoningMutation.error as Error).message}
                    </div>
                ) : null}

                {!reasoningMutation.isPending && !reasoningMutation.data ? (
                    <EmptyState
                        title="Brak preview"
                        description="To panel wglądu. Wpisz zapytanie, by zobaczyć kroki rozumowania przed egzekucją."
                    />
                ) : null}

                {reasoningMutation.data ? (
                    <div className="space-y-3">
                        <Separator />

                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">
                                preview_only: {String(previewView.previewOnly)}
                            </Badge>
                            {previewView.confidence !== undefined ? (
                                <Badge variant="outline">
                                    confidence:{" "}
                                    {previewView.confidence.toFixed(2)}
                                </Badge>
                            ) : null}
                            {previewView.score !== undefined ? (
                                <Badge variant="outline">
                                    score: {previewView.score.toFixed(2)}
                                </Badge>
                            ) : null}
                        </div>

                        {Object.keys(previewView.plannerSummary).length ? (
                            <div className="rounded-md border border-border p-2 text-xs">
                                <p className="font-semibold mb-1">Summary</p>
                                <div className="space-y-1 text-muted-foreground">
                                    {Object.entries(
                                        previewView.plannerSummary,
                                    ).map(([key, value]) => (
                                        <p key={key}>
                                            {key}: {String(value)}
                                        </p>
                                    ))}
                                </div>
                            </div>
                        ) : null}

                        {previewView.warnings.length ? (
                            <div className="rounded-md border border-amber-700/50 bg-amber-950/40 p-2 text-xs text-amber-200">
                                <p className="font-semibold mb-1">
                                    Ostrzeżenia
                                </p>
                                <ul className="list-disc pl-4 space-y-1">
                                    {previewView.warnings.map((w, idx) => (
                                        <li key={`${w}-${idx}`}>{w}</li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}

                        {previewView.tasks.length ? (
                            <div className="grid gap-2 lg:grid-cols-2">
                                {previewView.tasks.map((task) => (
                                    <ReasoningTaskCard
                                        key={task.id}
                                        task={task}
                                    />
                                ))}
                            </div>
                        ) : (
                            <EmptyState
                                title="Brak kroków"
                                description="Backend nie zwrócił listy kroków do pokazania."
                            />
                        )}

                        <JsonView
                            title="Raw reasoning preview"
                            value={reasoningMutation.data}
                            compact
                        />
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
