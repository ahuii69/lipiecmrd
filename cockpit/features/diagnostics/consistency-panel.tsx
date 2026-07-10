"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function ConsistencyPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["cockpit-consistency", session.userId],
        queryFn: () =>
            apiClient.cockpitConsistency(
                session.userId,
                20,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load consistency data"
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
                    Loading consistency checks...
                </p>
            </div>
        );
    }

    if (!data) {
        return (
            <EmptyState
                title="No consistency data"
                description="Run agent cycles to generate consistency checks"
            />
        );
    }

    const checks = data.checks ?? [];
    const stats = data.stats ?? {};

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">
                            Consistency Checks
                        </CardTitle>
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
                            <span className="text-muted-foreground">
                                User ID:
                            </span>
                            <Badge variant="secondary">{data.user_id}</Badge>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">
                                Checks:
                            </span>
                            <span className="font-mono">{checks.length}</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {Object.keys(stats).length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Stats</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <JsonView title="Statistics" value={stats} compact />
                    </CardContent>
                </Card>
            )}

            {checks.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Recent Checks</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <JsonView title="Checks" value={checks} />
                    </CardContent>
                </Card>
            )}

            {checks.length === 0 && (
                <EmptyState
                    title="No checks available"
                    description="Run agent cycles to generate consistency checks"
                />
            )}
        </div>
    );
}
