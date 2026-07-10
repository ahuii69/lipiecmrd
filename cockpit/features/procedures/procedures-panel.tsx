"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, GitBranch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function ProceduresPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["memory-v2-procedures", session.userId],
        queryFn: () =>
            apiClient.getMemoryV2Procedures(
                session.userId,
                20,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load procedures"
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
                    Loading procedures...
                </p>
            </div>
        );
    }

    const procedures = Array.isArray(data) ? data : [];

    if (procedures.length === 0) {
        return (
            <EmptyState
                title="No procedures learned"
                description="Execute tasks to build procedural memory"
                icon={GitBranch}
            />
        );
    }

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Learned Procedures</CardTitle>
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
                        <span className="text-muted-foreground">Total:</span>
                        <Badge variant="secondary">{procedures.length}</Badge>
                    </div>
                </CardContent>
            </Card>

            <div className="space-y-2">
                {procedures.map((proc: any) => (
                    <Card key={proc.id}>
                        <CardContent className="pt-3">
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <p className="text-xs font-medium">{proc.name}</p>
                                    <Badge variant="outline" className="text-[10px]">
                                        {proc.recommended_strategy}
                                    </Badge>
                                </div>
                                <div className="grid grid-cols-2 gap-2 text-[10px]">
                                    <div className="flex items-center justify-between">
                                        <span className="text-muted-foreground">Success:</span>
                                        <span className="font-mono text-green-600">
                                            {(proc.success_rate * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-muted-foreground">Failure:</span>
                                        <span className="font-mono text-red-600">
                                            {(proc.failure_rate * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-muted-foreground">Confidence:</span>
                                        <span className="font-mono">{proc.confidence_score?.toFixed(2)}</span>
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-muted-foreground">Evidence:</span>
                                        <span className="font-mono">{proc.evidence_count}</span>
                                    </div>
                                </div>
                                <p className="text-[10px] text-muted-foreground">
                                    Trigger: {proc.trigger_pattern}
                                </p>
                                {proc.recommended_tools && proc.recommended_tools.length > 0 && (
                                    <div className="flex flex-wrap gap-1">
                                        {proc.recommended_tools.map((tool: string, idx: number) => (
                                            <Badge key={idx} variant="secondary" className="text-[9px]">
                                                {tool}
                                            </Badge>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>
        </div>
    );
}
