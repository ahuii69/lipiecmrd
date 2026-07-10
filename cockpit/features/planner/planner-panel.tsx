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
    normalizePlannerGraph,
    normalizePlannerPreview,
    type PlannerTaskView,
} from "./planner-parser";

function TaskCard({ task }: { task: PlannerTaskView }) {
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
                {task.hints && task.hints.length ? (
                    <div>
                        <p className="mb-1 font-semibold">Hinty:</p>
                        <div className="flex flex-wrap gap-1">
                            {task.hints.map((hint, idx) => (
                                <Badge key={`${hint}-${idx}`} variant="outline">
                                    {hint}
                                </Badge>
                            ))}
                        </div>
                    </div>
                ) : null}
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

export function PlannerPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
    const [text, setText] = useState(
        "Co pamiętasz o AI-Hub i jakie kolejne kroki zaplanować?",
    );

    const previewMutation = useMutation({
        mutationFn: async (message: string) =>
            apiClient.plannerPreview(
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

    const graphMutation = useMutation({
        mutationFn: async (message: string) =>
            apiClient.plannerBuildTaskGraph(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    text: message,
                    include_context: true,
                },
                apiKeyOverride || undefined,
            ),
    });

    const previewView = useMemo(
        () => normalizePlannerPreview(previewMutation.data),
        [previewMutation.data],
    );
    const graphView = useMemo(
        () => normalizePlannerGraph(graphMutation.data),
        [graphMutation.data],
    );

    const submitGraph = async (e: FormEvent) => {
        e.preventDefault();
        if (!text.trim()) return;
        await graphMutation.mutateAsync(text.trim());
    };

    const runPreview = async () => {
        if (!text.trim()) return;
        await previewMutation.mutateAsync(text.trim());
    };

    return (
        <Card className="h-full">
            <CardHeader>
                <CardTitle>Planner — preview + task graph</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <form onSubmit={submitGraph} className="space-y-2">
                    <Textarea
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        className="min-h-[120px]"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <Badge variant="warning">
                                Preview — bez egzekucji runtime
                            </Badge>
                            <Badge variant="outline">context: on</Badge>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="secondary"
                                onClick={runPreview}
                                disabled={
                                    previewMutation.isPending || !text.trim()
                                }
                            >
                                {previewMutation.isPending
                                    ? "Generuję preview…"
                                    : "Planner preview"}
                            </Button>
                            <Button
                                type="submit"
                                disabled={
                                    graphMutation.isPending || !text.trim()
                                }
                            >
                                {graphMutation.isPending
                                    ? "Buduję graf…"
                                    : "Build task graph"}
                            </Button>
                        </div>
                    </div>
                </form>

                <Separator />

                <div className="grid gap-4 lg:grid-cols-2">
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">
                                    Planner preview
                                </CardTitle>
                                <Badge variant="outline" className="text-xs">
                                    {previewView.count} tasks
                                </Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {previewMutation.isError ? (
                                <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-xs text-red-300">
                                    Preview nie wyszedł:{" "}
                                    {(previewMutation.error as Error).message}
                                </div>
                            ) : null}

                            {!previewMutation.isPending &&
                            !previewView.tasks.length ? (
                                <EmptyState
                                    title="Brak preview"
                                    description="Uruchom planner.preview, aby zobaczyć listę kroków."
                                />
                            ) : null}

                            {previewView.tasks.length ? (
                                <div className="grid gap-2">
                                    {previewView.tasks.map((task) => (
                                        <TaskCard key={task.id} task={task} />
                                    ))}
                                </div>
                            ) : null}

                            {previewMutation.data ? (
                                <JsonView
                                    title="Raw preview payload"
                                    value={previewMutation.data}
                                    compact
                                />
                            ) : null}
                        </CardContent>
                    </Card>

                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">
                                    Task graph
                                </CardTitle>
                                <Badge variant="outline" className="text-xs">
                                    {graphView.tasks.length} nodes
                                </Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {graphMutation.isError ? (
                                <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-xs text-red-300">
                                    Build graph nie wyszedł:{" "}
                                    {(graphMutation.error as Error).message}
                                </div>
                            ) : null}

                            {!graphMutation.isPending &&
                            !graphView.tasks.length ? (
                                <EmptyState
                                    title="Brak grafu"
                                    description="Uruchom planner.build_task_graph, aby zobaczyć pełny graf zależności."
                                />
                            ) : null}

                            {graphView.tasks.length ? (
                                <div className="grid gap-2">
                                    {graphView.tasks.map((task) => (
                                        <TaskCard key={task.id} task={task} />
                                    ))}
                                </div>
                            ) : null}

                            {Object.keys(graphView.summary).length ? (
                                <div className="rounded-md border border-border p-2 text-xs">
                                    <p className="font-semibold mb-1">
                                        Summary
                                    </p>
                                    <div className="space-y-1 text-muted-foreground">
                                        {Object.entries(graphView.summary).map(
                                            ([key, value]) => (
                                                <p key={key}>
                                                    {key}: {String(value)}
                                                </p>
                                            ),
                                        )}
                                    </div>
                                </div>
                            ) : null}

                            {graphMutation.data ? (
                                <JsonView
                                    title="Raw graph payload"
                                    value={graphMutation.data}
                                    compact
                                />
                            ) : null}
                        </CardContent>
                    </Card>
                </div>
            </CardContent>
        </Card>
    );
}
