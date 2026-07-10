"use client";

import { AlertTriangle, Play } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import type { AgentCycleResponse } from "@/lib/api/types";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import {
    getStrategyBadgeVariant,
    normalizeAgentCycle,
} from "./agent-control-parser";

export function AgentControlPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const [operatorInput, setOperatorInput] = useState("");
    const [isRunning, setIsRunning] = useState(false);
    const [lastResult, setLastResult] = useState<
        AgentCycleResponse | undefined
    >(undefined);
    const [lastError, setLastError] = useState<string | null>(null);

    const [loopMaxIters, setLoopMaxIters] = useState(3);
    const [isLoopRunning, setIsLoopRunning] = useState(false);

    const handleRunCycle = async (): Promise<void> => {
        setIsRunning(true);
        setLastError(null);
        setLastResult(undefined);

        try {
            const result = await apiClient.runAgent(
                {
                    user_id: session.id,
                    text:
                        operatorInput.trim() ||
                        `Run one cycle in ${session.mode} mode`,
                    include_debug: session.mode === "debug",
                },
                apiKeyOverride || undefined,
            );

            setLastResult(result);
        } catch (err) {
            setLastError(
                err instanceof Error
                    ? err.message
                    : "Failed to run agent cycle",
            );
        } finally {
            setIsRunning(false);
        }
    };

    const handleRunLoop = async (): Promise<void> => {
        setIsLoopRunning(true);
        setLastError(null);
        setLastResult(undefined);

        try {
            const result = await apiClient.runAgentLoop(
                {
                    user_id: session.id,
                    text:
                        operatorInput.trim() ||
                        `Run loop in ${session.mode} mode`,
                    include_debug: session.mode === "debug",
                    max_iters: loopMaxIters,
                },
                apiKeyOverride || undefined,
            );

            setLastResult(result);
        } catch (err) {
            setLastError(
                err instanceof Error
                    ? err.message
                    : "Failed to run agent loop",
            );
        } finally {
            setIsLoopRunning(false);
        }
    };

    const summary = normalizeAgentCycle(lastResult);

    return (
        <div className="space-y-3">
            {/* Control Section */}
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Run Agent Cycle</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div>
                        <label className="block text-xs font-semibold text-muted-foreground">
                            Operator Intent (optional)
                        </label>
                        <Input
                            value={operatorInput}
                            onChange={(e) => setOperatorInput(e.target.value)}
                            placeholder="Leave empty for default cycle, or enter task/intent..."
                            className="mt-1 text-xs"
                            disabled={isRunning || isLoopRunning}
                        />
                    </div>

                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>
                            Session: <strong>{session.id.slice(0, 8)}</strong> •
                            Mode: <strong>{session.mode}</strong>
                        </span>
                    </div>

                    <div className="flex items-center gap-2">
                        <Button
                            onClick={handleRunCycle}
                            disabled={isRunning || isLoopRunning}
                            className="flex-1"
                            size="sm"
                        >
                            <Play className="mr-1 h-4 w-4" />
                            {isRunning ? "Running…" : "Run Cycle"}
                        </Button>

                        <div className="flex items-center gap-1">
                            <Input
                                type="number"
                                min={1}
                                max={10}
                                value={loopMaxIters}
                                onChange={(e) =>
                                    setLoopMaxIters(Number(e.target.value))
                                }
                                className="h-9 w-14 text-xs"
                                disabled={isRunning || isLoopRunning}
                            />
                            <Button
                                onClick={handleRunLoop}
                                disabled={isRunning || isLoopRunning}
                                variant="secondary"
                                size="sm"
                            >
                                {isLoopRunning ? "Loop Running…" : "Run Loop"}
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Error Display */}
            {lastError ? (
                <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3">
                    <div className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-300" />
                        <div>
                            <p className="text-xs font-semibold text-red-300">
                                Execution Error
                            </p>
                            <p className="mt-1 text-xs text-red-200">
                                {lastError}
                            </p>
                        </div>
                    </div>
                </div>
            ) : null}

            {/* Result Display */}
            {lastResult && !lastError ? (
                <>
                    {/* Status & Strategy */}
                    <Card>
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">
                                    Cycle Result
                                </CardTitle>
                                <Badge
                                    variant={summary.ok ? "success" : "danger"}
                                >
                                    {summary.ok ? "✓ OK" : "✗ Failed"}
                                </Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            <div className="grid gap-2 rounded-md bg-muted/40 p-2">
                                <div className="flex items-center justify-between">
                                    <span className="text-xs text-muted-foreground">
                                        Strategy
                                    </span>
                                    <Badge
                                        variant={
                                            getStrategyBadgeVariant(
                                                summary.strategy,
                                            ) as
                                                | "default"
                                                | "secondary"
                                                | "outline"
                                                | "warning"
                                                | "danger"
                                                | "success"
                                        }
                                    >
                                        {summary.strategy}
                                    </Badge>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    {summary.strategyReason}
                                </p>
                            </div>

                            <div className="grid grid-cols-3 gap-1">
                                {summary.planningUsed ? (
                                    <Badge
                                        variant="outline"
                                        className="text-xs"
                                    >
                                        ↪ Planning
                                    </Badge>
                                ) : null}
                                {summary.reasoningUsed ? (
                                    <Badge
                                        variant="outline"
                                        className="text-xs"
                                    >
                                        🧠 Reasoning
                                    </Badge>
                                ) : null}
                                {summary.goalProgressChanged ? (
                                    <Badge
                                        variant="success"
                                        className="text-xs"
                                    >
                                        ✓ Goal ↗
                                    </Badge>
                                ) : null}
                            </div>

                            {summary.errorCount > 0 ? (
                                <div className="mt-2 rounded-md border border-amber-800/60 bg-amber-950/50 p-2">
                                    <p className="text-xs font-semibold text-amber-300">
                                        {summary.errorCount} Error
                                        {summary.errorCount !== 1 ? "s" : ""}
                                    </p>
                                </div>
                            ) : null}
                        </CardContent>
                    </Card>

                    {/* Selected Goal */}
                    {summary.selectedGoal ? (
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">
                                    Selected Goal
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <JsonView
                                    title="Goal Details"
                                    value={summary.selectedGoal}
                                    compact
                                />
                            </CardContent>
                        </Card>
                    ) : null}

                    {/* Execution Summary */}
                    {Object.keys(summary.executionSummary).length > 0 ? (
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">
                                    Execution Summary
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <JsonView
                                    title="Summary"
                                    value={summary.executionSummary}
                                    compact
                                />
                            </CardContent>
                        </Card>
                    ) : null}

                    {/* Reflection */}
                    {Object.keys(summary.reflection).length > 0 ? (
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">
                                    Reflection
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <JsonView
                                    title="Reflection State"
                                    value={summary.reflection}
                                    compact
                                />
                            </CardContent>
                        </Card>
                    ) : null}

                    {/* Trace */}
                    {Object.keys(summary.trace).length > 0 ? (
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">
                                    Execution Trace
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <JsonView
                                    title="Trace Events"
                                    value={summary.trace}
                                    compact
                                />
                            </CardContent>
                        </Card>
                    ) : null}

                    {/* Raw Payload */}
                    <Card className="shadow-none">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-xs uppercase tracking-wide">
                                Raw Payload
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <JsonView
                                title="Full Response"
                                value={lastResult}
                            />
                        </CardContent>
                    </Card>
                </>
            ) : null}

            {/* Empty State */}
            {!lastResult && !lastError && !isRunning ? (
                <EmptyState
                    title="No cycle executed yet"
                    description="Click 'Run Cycle' to execute an agent iteration"
                />
            ) : null}
        </div>
    );
}
