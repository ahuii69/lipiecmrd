"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function ContradictionsPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["memory-v2-contradictions", session.userId],
        queryFn: () =>
            apiClient.getMemoryV2Contradictions(
                session.userId,
                50,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load contradictions"
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
                    Loading contradictions...
                </p>
            </div>
        );
    }

    const contradictions = Array.isArray(data) ? data : [];

    if (contradictions.length === 0) {
        return (
            <EmptyState
                title="No contradictions"
                description="All memories are consistent"
                icon={AlertTriangle}
            />
        );
    }

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Memory Contradictions</CardTitle>
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
                                Total:
                            </span>
                            <Badge variant="danger">{contradictions.length}</Badge>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <div className="space-y-2">
                {contradictions.map((item: any) => (
                    <Card key={item.id}>
                        <CardContent className="pt-3">
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <p className="text-xs font-medium">{item.title}</p>
                                    <Badge
                                        variant={
                                            item.contradiction_state === "conflicted"
                                                ? "danger"
                                                : "outline"
                                        }
                                        className="text-[10px]"
                                    >
                                        {item.contradiction_state}
                                    </Badge>
                                </div>
                                <p className="text-[10px] text-muted-foreground">
                                    Type: {item.memory_type} | Confidence: {item.confidence_score?.toFixed(2)}
                                </p>
                                <p className="text-xs text-muted-foreground">{item.summary}</p>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>
        </div>
    );
}
