"use client";

import { useQuery } from "@tanstack/react-query";
import {
    Activity,
    AlertTriangle,
    CheckCircle2,
    Database,
    FileJson,
    Zap,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import {
    getHealthColor,
    normalizeCognitiveHealth,
    normalizeSystemHealth,
    normalizeSystemPing
} from "./system-parser";

export function SystemPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const ping = useQuery({
        queryKey: ["sys-ping", apiKeyOverride],
        queryFn: () => apiClient.runtimePing(apiKeyOverride || undefined),
    });

    const sysHealth = useQuery({
        queryKey: ["sys-health", session.userId, apiKeyOverride],
        queryFn: () =>
            apiClient.systemHealth(session.userId, apiKeyOverride || undefined),
    });

    const cognitive = useQuery({
        queryKey: ["cognitive-health", apiKeyOverride],
        queryFn: () => apiClient.cognitiveHealth(apiKeyOverride || undefined),
    });

    const schemaHealth = useQuery({
        queryKey: ["cockpit-schema-health", apiKeyOverride],
        queryFn: () =>
            apiClient.cockpitSchemaHealth(apiKeyOverride || undefined),
    });

    const pingView = normalizeSystemPing(ping.data);
    const healthView = normalizeSystemHealth(sysHealth.data);
    const cognitiveView = normalizeCognitiveHealth(cognitive.data);

    return (
        <div className="space-y-3">
            {/* System Ping */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>System Ping</span>
                        <Badge variant={pingView.ok ? "default" : "danger"}>
                            {pingView.ok ? "OK" : "FAIL"}
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    <div className="flex items-center gap-3">
                        <Activity className="h-4 w-4 text-blue-500" />
                        <span className="text-muted-foreground">App:</span>
                        <Badge variant="outline">{pingView.app}</Badge>
                    </div>
                    <div className="flex items-center gap-3">
                        <Zap className="h-4 w-4 text-yellow-600" />
                        <span className="text-muted-foreground">
                            Timestamp:
                        </span>
                        <Badge variant="outline">
                            {new Date(
                                pingView.timestamp * 1000,
                            ).toLocaleString()}
                        </Badge>
                    </div>
                </CardContent>
            </Card>

            {/* System Health (User) */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>System Health</span>
                        <Badge variant="secondary">
                            User {healthView.user_id.slice(0, 8)}
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid gap-2 sm:grid-cols-3">
                        <HealthMetric
                            icon={
                                <Database className="h-4 w-4 text-blue-500" />
                            }
                            label="Short-Term Memory"
                            value={healthView.short_term_memory}
                            unit="messages"
                        />
                        <HealthMetric
                            icon={
                                <FileJson className="h-4 w-4 text-purple-500" />
                            }
                            label="Episodic Memory"
                            value={healthView.episodic_memory}
                            unit="nodes"
                        />
                        <HealthMetric
                            icon={
                                <CheckCircle2 className="h-4 w-4 text-green-600" />
                            }
                            label="Semantic Memory"
                            value={healthView.semantic_memory}
                            unit="nodes"
                        />
                    </div>
                </CardContent>
            </Card>

            {/* Cognitive Health */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>Cognitive Health</span>
                        <Badge
                            variant={
                                cognitiveView.status === "ok"
                                    ? "default"
                                    : cognitiveView.status === "warning"
                                      ? "warning"
                                      : "danger"
                            }
                            className={getHealthColor(cognitiveView.status)}
                        >
                            {cognitiveView.status.toUpperCase()}
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    {cognitiveView.alerts.length > 0 && (
                        <div className="space-y-1">
                            {cognitiveView.alerts.map((alert, idx) => (
                                <div
                                    key={idx}
                                    className="flex items-start gap-2 rounded-md border border-yellow-800/60 bg-yellow-950/50 p-2"
                                >
                                    <AlertTriangle className="h-4 w-4 flex-none text-yellow-400" />
                                    <span className="text-xs text-yellow-200">
                                        {alert}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="grid gap-2 sm:grid-cols-3">
                        <div
                            className={`rounded-lg border border-border p-3 ${
                                cognitiveView.dbHealthy
                                    ? "bg-green-950/20"
                                    : "bg-red-950/20"
                            }`}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Database
                                </span>
                                <Badge
                                    variant={
                                        cognitiveView.dbHealthy
                                            ? "success"
                                            : "danger"
                                    }
                                    className="text-xs"
                                >
                                    {cognitiveView.dbHealthy
                                        ? "Healthy"
                                        : "Issue"}
                                </Badge>
                            </div>
                        </div>
                        <div
                            className={`rounded-lg border border-border p-3 ${
                                cognitiveView.graphHealthy
                                    ? "bg-green-950/20"
                                    : "bg-red-950/20"
                            }`}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    Graph
                                </span>
                                <Badge
                                    variant={
                                        cognitiveView.graphHealthy
                                            ? "success"
                                            : "danger"
                                    }
                                    className="text-xs"
                                >
                                    {cognitiveView.graphHealthy
                                        ? "OK"
                                        : "Error"}
                                </Badge>
                            </div>
                        </div>
                        <div
                            className={`rounded-lg border border-border p-3 ${
                                !cognitiveView.gcIssues
                                    ? "bg-green-950/20"
                                    : "bg-yellow-950/20"
                            }`}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-semibold text-muted-foreground">
                                    GC Status
                                </span>
                                <Badge
                                    variant={
                                        !cognitiveView.gcIssues
                                            ? "success"
                                            : "warning"
                                    }
                                    className="text-xs"
                                >
                                    {!cognitiveView.gcIssues ? "OK" : "⚠"}
                                </Badge>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Active stack schema (SQLite) */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                        <span>Active stack schema</span>
                        <Badge
                            variant={
                                schemaHealth.data?.ok === true
                                    ? "default"
                                    : schemaHealth.isError
                                      ? "danger"
                                      : "secondary"
                            }
                        >
                            {schemaHealth.isLoading
                                ? "…"
                                : schemaHealth.data?.ok === true
                                  ? "OK"
                                  : schemaHealth.data?.note
                                    ? "N/A"
                                    : "CHECK"}
                        </Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    {schemaHealth.isError && (
                        <p className="text-destructive">
                            {schemaHealth.error instanceof Error
                                ? schemaHealth.error.message
                                : "Schema health request failed"}
                        </p>
                    )}
                    <JsonView
                        title="/cockpit/schema-health"
                        value={schemaHealth.data ?? {}}
                        compact
                    />
                </CardContent>
            </Card>

            {/* Raw Payloads */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Raw Payloads</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                    <JsonView
                        title="/system/ping"
                        value={ping.data ?? {}}
                        compact
                    />
                    <Separator className="my-2" />
                    <JsonView
                        title={`/system/health/${session.userId}`}
                        value={sysHealth.data ?? {}}
                        compact
                    />
                    <Separator className="my-2" />
                    <JsonView
                        title="/cognitive/health"
                        value={cognitive.data ?? {}}
                        compact
                    />
                </CardContent>
            </Card>
        </div>
    );
}

function HealthMetric({
    icon,
    label,
    value,
    unit,
}: {
    icon: React.ReactNode;
    label: string;
    value: number;
    unit: string;
}) {
    return (
        <div className="rounded-lg border border-border bg-secondary/20 p-3">
            <div className="flex items-start justify-between gap-2">
                <div className="flex-1">
                    <div className="flex items-center gap-1.5">
                        <div className="flex-none">{icon}</div>
                        <span className="text-xs font-semibold text-muted-foreground">
                            {label}
                        </span>
                    </div>
                    <div className="mt-1 text-sm font-mono font-bold">
                        {value}
                    </div>
                </div>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">{unit}</div>
        </div>
    );
}
