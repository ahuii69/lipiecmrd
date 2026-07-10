"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, User } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function IdentityPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["cockpit-identity", session.userId],
        queryFn: () =>
            apiClient.cockpitIdentity(
                session.userId,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load Identity"
                description={
                    error instanceof Error ? error.message : "Unknown error"
                }
                icon={AlertCircle}
            />
        );
    }

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center">
                <p className="text-sm text-muted-foreground">
                    Loading Identity...
                </p>
            </div>
        );
    }

    if (!data) {
        return (
            <EmptyState
                title="No Identity data"
                description="Identity bridge not available"
                icon={User}
            />
        );
    }

    const topPreferences = data.top_preferences ?? [];
    const topProcedures = data.top_procedures ?? [];

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Identity Overview</CardTitle>
                        <Button
                            onClick={() => refetch()}
                            variant="outline"
                            size="sm"
                        >
                            Refresh
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Mode:</span>
                            <Badge variant="secondary">{data.behavior_mode}</Badge>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Trust:</span>
                            <span className="font-mono">{data.relation_trust?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Familiarity:</span>
                            <span className="font-mono">{data.relation_familiarity?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Contradictions:</span>
                            <Badge variant={data.active_contradictions_count > 0 ? "danger" : "secondary"}>
                                {data.active_contradictions_count}
                            </Badge>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Memory Items:</span>
                            <span className="font-mono">{data.memory_v2_total}</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {topPreferences.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Top Preferences</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-1">
                            {topPreferences.map((pref: any) => (
                                <div key={pref.id} className="text-xs">
                                    <span className="text-muted-foreground">• {pref.title}</span>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            )}

            {topProcedures.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Strongest Procedures</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-1">
                            {topProcedures.map((proc: any) => (
                                <div key={proc.id} className="text-xs">
                                    <p className="font-medium">{proc.name}</p>
                                    <p className="text-[10px] text-muted-foreground">
                                        Success: {(proc.success_rate * 100).toFixed(0)}%
                                    </p>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            )}

            {data.autobio_summary && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Autobiographical Summary</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-xs text-muted-foreground">{data.autobio_summary}</p>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
