"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { unwrapCapabilityResult } from "@/lib/api/tool-result";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import {
    normalizeAgentState,
    normalizeRuntimeStatus,
    normalizeRuntimeTrace,
    type ExecutionEvent,
} from "./runtime-parser";

export function RuntimePanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const statusQuery = useQuery({
        queryKey: [
            "runtime-status-cap",
            session.userId,
            session.mode,
            apiKeyOverride,
        ],
        queryFn: async () => {
            const out = await apiClient.executeCapability(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    tool_name: "runtime.status",
                    arguments: {},
                },
                apiKeyOverride || undefined,
            );
            return unwrapCapabilityResult<Record<string, unknown>>(out);
        },
    });

    const traceQuery = useQuery({
        queryKey: [
            "runtime-trace-cap",
            session.userId,
            session.mode,
            apiKeyOverride,
        ],
        queryFn: async () => {
            const out = await apiClient.executeCapability(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    tool_name: "runtime.trace_last_cycle",
                    arguments: { limit: 15 },
                },
                apiKeyOverride || undefined,
            );
            return unwrapCapabilityResult<Record<string, unknown>>(out);
        },
    });

    const agentStateQuery = useQuery({
        queryKey: ["runtime-agent-state", session.userId, apiKeyOverride],
        queryFn: () =>
            apiClient.runtimeStatus(
                session.userId,
                apiKeyOverride || undefined,
            ),
    });

    const capsQuery = useQuery({
        queryKey: ["runtime-caps", session.mode, apiKeyOverride],
        queryFn: () =>
            apiClient.capabilities(
                session.mode,
                session.mode === "debug",
                apiKeyOverride || undefined,
            ),
    });

    const statusSummary = normalizeRuntimeStatus(statusQuery.data);
    const traceView = normalizeRuntimeTrace(traceQuery.data);
    const agentState = normalizeAgentState(agentStateQuery.data);

    return (
        <div className="space-y-3">
            {/* Status Summary */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>Runtime Status</span>
                        <Badge
                            variant={statusSummary.ok ? "default" : "danger"}
                        >
                            {statusSummary.ok ? "Online" : "Offline"}
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <div className="rounded-lg border border-border bg-secondary/20 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Strategy
                                </span>
                                <Badge variant="outline" className="text-xs">
                                    {statusSummary.strategy}
                                </Badge>
                            </div>
                        </div>
                        <div className="rounded-lg border border-border bg-secondary/20 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Planning
                                </span>
                                <Badge
                                    variant={
                                        statusSummary.planning_used
                                            ? "default"
                                            : "secondary"
                                    }
                                    className="text-xs"
                                >
                                    {statusSummary.planning_used ? "Used" : "—"}
                                </Badge>
                            </div>
                        </div>
                        <div className="rounded-lg border border-border bg-secondary/20 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Reasoning
                                </span>
                                <Badge
                                    variant={
                                        statusSummary.reasoning_used
                                            ? "default"
                                            : "secondary"
                                    }
                                    className="text-xs"
                                >
                                    {statusSummary.reasoning_used
                                        ? "used"
                                        : "—"}
                                </Badge>
                            </div>
                        </div>
                        <div className="rounded-lg border border-border bg-secondary/20 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Goal Progress
                                </span>
                                <Badge
                                    variant={
                                        statusSummary.goal_progress
                                            ? "success"
                                            : "secondary"
                                    }
                                    className="text-xs"
                                >
                                    {statusSummary.goal_progress ? "✓" : "—"}
                                </Badge>
                            </div>
                        </div>
                    </div>

                    {statusSummary.error_count > 0 && (
                        <div className="flex items-center gap-2 rounded-md border border-red-800/60 bg-red-950/50 p-2 py-3">
                            <AlertTriangle className="h-4 w-4 text-red-400" />
                            <span className="text-xs text-red-300">
                                {statusSummary.error_count} error
                                {statusSummary.error_count > 1 ? "s" : ""}
                            </span>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Agent State */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Agent State</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    <div className="grid gap-2 sm:grid-cols-3">
                        <div className="flex items-center gap-2">
                            <Clock className="h-4 w-4 text-muted-foreground" />
                            <span className="text-muted-foreground">
                                Cycle:
                            </span>
                            <span className="font-mono font-semibold">
                                {agentState.cycle}
                            </span>
                        </div>
                        <div className="flex items-center gap-2">
                            <CheckCircle2 className="h-4 w-4 text-green-600" />
                            <span className="text-muted-foreground">
                                Completed:
                            </span>
                            <span className="font-mono font-semibold">
                                {agentState.completed_goals}
                            </span>
                        </div>
                        <div className="flex items-center gap-2">
                            <Zap className="h-4 w-4 text-yellow-600" />
                            <span className="text-muted-foreground">
                                Active:
                            </span>
                            <span className="font-mono font-semibold">
                                {agentState.active_goals}
                            </span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Trace Events */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>Last Cycle Trace</span>
                        <Badge variant="outline">
                            {traceView.event_count} events
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {traceView.event_count === 0 ? (
                        <EmptyState
                            title="No trace events"
                            description="No execution events recorded."
                        />
                    ) : (
                        <div className="space-y-1">
                            {traceView.events.map((evt, idx) => (
                                <EventRow key={idx} event={evt} />
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Session Meta */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Session Metadata</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    <div className="flex flex-wrap gap-1.5">
                        <Badge variant="secondary">mode:{session.mode}</Badge>
                        <Badge variant="outline">
                            caps:{capsQuery.data?.count ?? 0}
                        </Badge>
                        <Badge variant="outline">
                            user:{session.userId.slice(0, 8)}
                        </Badge>
                        <Badge variant="outline">
                            session:{session.id.slice(0, 8)}
                        </Badge>
                    </div>
                </CardContent>
            </Card>

            {/* Raw Payload */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Raw Payloads</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    <JsonView
                        title="runtime.status"
                        value={statusQuery.data ?? {}}
                        compact
                    />
                    <Separator className="my-2" />
                    <JsonView
                        title="runtime.trace_last_cycle"
                        value={traceQuery.data ?? {}}
                        compact
                    />
                    <Separator className="my-2" />
                    <JsonView
                        title="agent/status"
                        value={agentStateQuery.data ?? {}}
                        compact
                    />
                </CardContent>
            </Card>
        </div>
    );
}

function EventRow({ event }: { event: ExecutionEvent }) {
    return (
        <div className="flex items-start gap-3 rounded border border-border/40 bg-secondary/10 p-2 py-2.5">
            <div className="flex-none text-xs font-mono text-muted-foreground">
                {event.timestamp
                    ? new Date(event.timestamp).toLocaleTimeString()
                    : "—"}
            </div>
            <div className="flex-1">
                <div className="text-xs font-semibold">{event.type}</div>
                {event.detail && (
                    <div className="text-xs text-muted-foreground">
                        {event.detail}
                    </div>
                )}
            </div>
        </div>
    );
}
