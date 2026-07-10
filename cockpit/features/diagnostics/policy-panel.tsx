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

export function PolicyPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["cockpit-policy", session.userId],
        queryFn: () =>
            apiClient.cockpitPolicy(
                session.userId,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load policy data"
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
                    Loading policy data...
                </p>
            </div>
        );
    }

    if (!data) {
        return (
            <EmptyState
                title="No policy data"
                description="Run agent cycles to generate policy state"
            />
        );
    }

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Policy State</CardTitle>
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
                    <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">User ID:</span>
                        <Badge variant="secondary">
                            {(data as any)?.user_id ?? session.userId}
                        </Badge>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Policy Details</CardTitle>
                </CardHeader>
                <CardContent>
                    <JsonView title="Policy" value={data} />
                </CardContent>
            </Card>
        </div>
    );
}
