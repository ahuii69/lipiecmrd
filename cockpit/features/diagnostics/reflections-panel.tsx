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

export function ReflectionsPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["cockpit-reflections", session.userId],
        queryFn: () =>
            apiClient.cockpitReflections(
                session.userId,
                20,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load reflections"
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
                    Loading reflections...
                </p>
            </div>
        );
    }

    if (!data) {
        return (
            <EmptyState
                title="No reflections data"
                description="Run agent cycles to generate reflections"
            />
        );
    }

    const reflections = data.reflections ?? [];

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Reflections</CardTitle>
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
                                Count:
                            </span>
                            <span className="font-mono">
                                {reflections.length}
                            </span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {reflections.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">
                            Recent Reflections
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <JsonView title="Reflections" value={reflections} />
                    </CardContent>
                </Card>
            )}

            {reflections.length === 0 && (
                <EmptyState
                    title="No reflections available"
                    description="Run agent cycles to generate reflections"
                />
            )}
        </div>
    );
}
