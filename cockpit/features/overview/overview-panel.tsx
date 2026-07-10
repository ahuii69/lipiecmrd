"use client";

import { useQuery } from "@tanstack/react-query";
import {
    Activity,
    AlertTriangle,
    BarChart3,
    CheckCircle2,
    Database,
    ExternalLink,
    Layers,
    RefreshCw,
    Target,
    XCircle,
} from "lucide-react";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { EmptyState } from "@/features/shared/empty-state";
import { GroundingBadge } from "@/features/shared/grounding-badge";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import type { CockpitSection } from "@/lib/types/ui";

import {
    toEtap9bcStatus,
    toOverviewViewModel,
    type OverviewSignal,
    type OverviewWarning,
} from "./overview-parser";

const STATUS_VARIANT: Record<
    string,
    "secondary" | "success" | "warning" | "danger"
> = {
    "model-only": "secondary",
    "tool-verified": "success",
    fallback: "warning",
    "tool-failed": "danger",
    error: "danger",
};

function WarningsBanner({ warnings }: { warnings: OverviewWarning[] }) {
    if (warnings.length === 0) return null;
    return (
        <div className="flex flex-col gap-1.5 rounded-lg border border-red-700/60 bg-red-950/40 px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-red-400">
                Ostrzeżenia ({warnings.length})
            </p>
            {warnings.map((w) => (
                <div key={w.code} className="flex items-center gap-2 text-xs">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-400" />
                    <Badge
                        variant={w.severity === "high" ? "danger" : "warning"}
                        className="shrink-0"
                    >
                        {w.code}
                    </Badge>
                    <span className="text-red-200">{w.message}</span>
                </div>
            ))}
        </div>
    );
}

function SignalRow({ sig }: { sig: OverviewSignal }) {
    return (
        <div className="flex items-center gap-2 text-xs">
            {sig.active ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
            ) : (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
            )}
            <span
                className={
                    sig.active ? "text-foreground" : "text-muted-foreground"
                }
            >
                {sig.label}
            </span>
            {sig.detail && (
                <Badge
                    variant={sig.detail === "failed" ? "danger" : "secondary"}
                    className="ml-auto"
                >
                    {sig.detail}
                </Badge>
            )}
        </div>
    );
}

function QuickLinks({
    setSection,
}: {
    setSection: (s: CockpitSection) => void;
}) {
    const links: Array<{ id: CockpitSection; label: string }> = [
        { id: "memory", label: "Memory" },
        { id: "psyche", label: "Psyche" },
        { id: "research", label: "Research" },
        { id: "goals", label: "Goals" },
        { id: "runtime", label: "Runtime" },
        { id: "capabilities", label: "Capabilities" },
    ];
    return (
        <div className="flex flex-wrap gap-2">
            {links.map((l) => (
                <Button
                    key={l.id}
                    variant="outline"
                    size="sm"
                    onClick={() => setSection(l.id)}
                    className="flex items-center gap-1.5"
                >
                    <ExternalLink className="h-3 w-3" />
                    {l.label}
                </Button>
            ))}
        </div>
    );
}

export function OverviewPanel() {
    const { sessions, activeSessionId, apiKeyOverride, setSection } =
        useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const lastDiagnostics = useMemo(() => {
        const msgs = session?.messages ?? [];
        for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === "assistant" && msgs[i].diagnostics) {
                return msgs[i].diagnostics;
            }
        }
        return undefined;
    }, [session?.messages]);

    const healthQuery = useQuery({
        queryKey: ["overview-health", session.userId, apiKeyOverride],
        queryFn: () =>
            apiClient.systemHealth(session.userId, apiKeyOverride || undefined),
        refetchInterval: 30_000,
    });

    const goalsQuery = useQuery({
        queryKey: [
            "overview-goals",
            session.userId,
            session.mode,
            apiKeyOverride,
        ],
        queryFn: () =>
            apiClient.goalListActive(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                },
                apiKeyOverride || undefined,
            ),
        refetchInterval: 30_000,
    });

    const cockpitOverviewQuery = useQuery({
        queryKey: ["overview-cockpit", session.userId, apiKeyOverride],
        queryFn: () =>
            apiClient.cockpitOverview(
                session.userId,
                20,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 60_000,
    });

    const etap9bcStatus = toEtap9bcStatus(
        cockpitOverviewQuery.data ?? undefined,
    );

    const vm = toOverviewViewModel(
        lastDiagnostics,
        healthQuery.data ?? undefined,
        goalsQuery.data ?? undefined,
    );

    return (
        <ScrollArea className="h-full">
            <div className="flex flex-col gap-4 p-4">
                {/* Header */}
                <div className="flex items-center justify-between">
                    <div>
                        <h2 className="text-base font-bold tracking-tight">
                            Decision Center
                        </h2>
                        <p className="text-xs text-muted-foreground">
                            Scalony widok operatora — ostatni turn, sygnały
                            runtime, memory i goals
                        </p>
                    </div>
                    <Badge variant="secondary">overview</Badge>
                </div>

                <WarningsBanner warnings={vm.warnings} />

                {!vm.hasData && (
                    <EmptyState
                        icon={Activity}
                        title="Brak danych z ostatniego turnu"
                        description="Wyślij wiadomość w sekcji Czat, aby zobaczyć sygnały operacyjne."
                    />
                )}

                {vm.hasData && (
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        {/* Last turn */}
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="flex items-center gap-2 text-sm">
                                    <Activity className="h-4 w-4 text-primary" />
                                    Ostatni turn
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-col gap-2 text-xs">
                                <div className="flex flex-wrap gap-2">
                                    <GroundingBadge mode={vm.grounding} />
                                    <Badge
                                        variant={
                                            STATUS_VARIANT[vm.status] ??
                                            "secondary"
                                        }
                                    >
                                        {vm.status}
                                    </Badge>
                                    {vm.toolsFailed > 0 && (
                                        <Badge variant="danger">
                                            {vm.toolsFailed} tool fail
                                        </Badge>
                                    )}
                                    {vm.errorsCount > 0 && (
                                        <Badge variant="danger">
                                            {vm.errorsCount} error(s)
                                        </Badge>
                                    )}
                                </div>
                                <Separator />
                                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                                    <span className="text-muted-foreground">
                                        Provider
                                    </span>
                                    <span className="truncate font-mono">
                                        {vm.provider}
                                    </span>
                                    <span className="text-muted-foreground">
                                        Model
                                    </span>
                                    <span className="truncate font-mono">
                                        {vm.model}
                                    </span>
                                    <span className="text-muted-foreground">
                                        Tokeny
                                    </span>
                                    <span className="font-mono">
                                        {vm.totalTokens.toLocaleString()}
                                    </span>
                                    <span className="text-muted-foreground">
                                        Czas
                                    </span>
                                    <span className="font-mono">
                                        {vm.durationMs != null
                                            ? `${vm.durationMs.toLocaleString()} ms`
                                            : "—"}
                                    </span>
                                    <span className="text-muted-foreground">
                                        Tools
                                    </span>
                                    <span className="font-mono">
                                        {vm.toolsSucceeded}/
                                        {vm.toolsAttempted} ok
                                    </span>
                                </div>
                                {vm.lastResponsePreview && (
                                    <>
                                        <Separator />
                                        <p className="line-clamp-3 text-muted-foreground">
                                            {vm.lastResponsePreview}
                                        </p>
                                    </>
                                )}
                            </CardContent>
                        </Card>

                        {/* Runtime Signals */}
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="flex items-center gap-2 text-sm">
                                    <BarChart3 className="h-4 w-4 text-primary" />
                                    Sygnały runtime
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="flex flex-col gap-1.5">
                                    {vm.signals.map((sig) => (
                                        <SignalRow
                                            key={sig.label}
                                            sig={sig}
                                        />
                                    ))}
                                </div>
                            </CardContent>
                        </Card>

                        {/* Memory Layer */}
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="flex items-center gap-2 text-sm">
                                    <Database className="h-4 w-4 text-primary" />
                                    Memory layer
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-col gap-2 text-xs">
                                {healthQuery.isLoading ? (
                                    <div className="flex items-center gap-2 text-muted-foreground">
                                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                        <span>Ładowanie…</span>
                                    </div>
                                ) : healthQuery.isError ? (
                                    <p className="text-red-400">
                                        Błąd danych health
                                    </p>
                                ) : (
                                    <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                                        <span className="text-muted-foreground">
                                            STM
                                        </span>
                                        <span className="font-mono">
                                            {vm.stmCount}
                                        </span>
                                        <span className="text-muted-foreground">
                                            Epizodyczna
                                        </span>
                                        <span className="font-mono">
                                            {vm.episodicCount}
                                        </span>
                                        <span className="text-muted-foreground">
                                            Semantyczna
                                        </span>
                                        <span className="font-mono">
                                            {vm.semanticCount}
                                        </span>
                                    </div>
                                )}
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setSection("memory")}
                                    className="mt-1 flex items-center gap-1.5 self-start"
                                >
                                    <ExternalLink className="h-3 w-3" />
                                    Otwórz Memory
                                </Button>
                            </CardContent>
                        </Card>

                        {/* Active Goal */}
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="flex items-center gap-2 text-sm">
                                    <Target className="h-4 w-4 text-primary" />
                                    Aktywny cel
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-col gap-2 text-xs">
                                {vm.selectedGoalTitle ? (
                                    <div className="flex flex-col gap-1.5">
                                        <p className="font-medium">
                                            {vm.selectedGoalTitle}
                                        </p>
                                        <div className="flex items-center gap-2">
                                            {vm.selectedGoalStatus && (
                                                <Badge variant="secondary">
                                                    {vm.selectedGoalStatus}
                                                </Badge>
                                            )}
                                            {vm.selectedGoalProgress != null && (
                                                <span className="text-muted-foreground">
                                                    {Math.round(
                                                        vm.selectedGoalProgress *
                                                            100,
                                                    )}
                                                    %
                                                </span>
                                            )}
                                            {vm.selectedGoalUrgency != null && (
                                                <span className="font-mono text-amber-400">
                                                    urgency:{" "}
                                                    {vm.selectedGoalUrgency.toFixed(
                                                        2,
                                                    )}
                                                </span>
                                            )}
                                        </div>
                                        {vm.selectedGoalProgress != null && (
                                            <div className="h-1.5 w-full rounded-full bg-muted">
                                                <div
                                                    className="h-full rounded-full bg-emerald-500"
                                                    style={{
                                                        width: `${Math.round(vm.selectedGoalProgress * 100)}%`,
                                                    }}
                                                />
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <p className="text-muted-foreground">
                                        Brak celu z ostatniego turnu
                                    </p>
                                )}
                                <div className="flex items-center gap-2 text-muted-foreground">
                                    {goalsQuery.isLoading && (
                                        <RefreshCw className="h-3 w-3 animate-spin" />
                                    )}
                                    <span>
                                        Aktywne cele:{" "}
                                        <span className="font-mono text-foreground">
                                            {vm.activeGoalsCount}
                                        </span>
                                    </span>
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setSection("goals")}
                                    className="mt-1 flex items-center gap-1.5 self-start"
                                >
                                    <ExternalLink className="h-3 w-3" />
                                    Otwórz Goals
                                </Button>
                            </CardContent>
                        </Card>
                    </div>
                )}

                {/* ETAP9BC Status card */}
                {etap9bcStatus && (
                    <Card>
                        <CardHeader className="pb-1 pt-3">
                            <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                <Layers className="h-3.5 w-3.5" />
                                ETAP9BC — Reasoning Layer
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="pb-3">
                            <div className="grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2 lg:grid-cols-4">
                                <div>
                                    <p className="text-muted-foreground">
                                        Consistency checks
                                    </p>
                                    <p className="font-mono text-sm font-semibold">
                                        {etap9bcStatus.consistencyTotal}
                                        {etap9bcStatus.consistencyConflicts >
                                            0 && (
                                            <span className="ml-1 text-amber-400">
                                                ({etap9bcStatus.consistencyConflicts}{" "}
                                                konfliktów)
                                            </span>
                                        )}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        Reflections
                                    </p>
                                    <p className="font-mono text-sm font-semibold">
                                        {etap9bcStatus.reflectionCount}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        Policy profile
                                    </p>
                                    <p className="font-mono text-sm font-semibold">
                                        {etap9bcStatus.policyName ?? (
                                            <span className="text-muted-foreground/60">
                                                —
                                            </span>
                                        )}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        Best simulation action
                                    </p>
                                    <p className="font-mono text-sm font-semibold">
                                        {etap9bcStatus.simulationBestAction ?? (
                                            <span className="text-muted-foreground/60">
                                                —
                                            </span>
                                        )}
                                    </p>
                                </div>
                            </div>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setSection("runtime")}
                                className="mt-2 flex items-center gap-1.5 self-start"
                            >
                                <ExternalLink className="h-3 w-3" />
                                Runtime Details
                            </Button>
                        </CardContent>
                    </Card>
                )}

                <Separator />

                {/* Quick links */}
                <div className="flex flex-col gap-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Szybkie przejście
                    </p>
                    <QuickLinks setSection={setSection} />
                </div>
            </div>
        </ScrollArea>
    );
}
