"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Brain } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HabitsPanel } from "@/features/habits/habits-panel";
import { RelationsPanel } from "@/features/relations/relations-panel";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function PsycheV2Panel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const { data, isLoading, error, refetch } = useQuery({
        queryKey: ["cockpit-psyche-v2", session.userId],
        queryFn: () =>
            apiClient.cockpitPsycheV2(
                session.userId,
                apiKeyOverride || undefined,
            ),
        refetchInterval: 10000,
    });

    if (error) {
        return (
            <EmptyState
                title="Failed to load Psyche V2"
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
                    Loading Psyche V2...
                </p>
            </div>
        );
    }

    if (!data) {
        return (
            <EmptyState
                title="No Psyche V2 data"
                description="Psyche profile not initialized"
                icon={Brain}
            />
        );
    }

    const profile = data.profile ?? {};
    const state = data.state ?? {};
    const policy = data.derived_policy ?? {};

    return (
        <div className="space-y-3">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">Psyche V2 State</CardTitle>
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
                            <Badge variant="secondary">{state.current_mode}</Badge>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Mood:</span>
                            <span className="font-mono">{state.mood?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Energy:</span>
                            <span className="font-mono">{state.energy?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Focus:</span>
                            <span className="font-mono">{state.focus?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Certainty:</span>
                            <span className="font-mono">{state.certainty?.toFixed(2)}</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Profile Traits</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Directness:</span>
                            <span className="font-mono">{profile.core_directness?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Patience:</span>
                            <span className="font-mono">{profile.core_patience?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Curiosity:</span>
                            <span className="font-mono">{profile.core_curiosity?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Caution:</span>
                            <span className="font-mono">{profile.core_caution?.toFixed(2)}</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Relation Stance</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="space-y-1 text-xs">
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Trust:</span>
                            <span className="font-mono">{profile.relation_trust?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Familiarity:</span>
                            <span className="font-mono">{profile.relation_familiarity?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Sync:</span>
                            <span className="font-mono">{profile.relation_sync?.toFixed(2)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Friction:</span>
                            <span className="font-mono">{profile.relation_friction?.toFixed(2) ?? "0.00"}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Warmth:</span>
                            <span className="font-mono">{profile.relation_warmth?.toFixed(2) ?? "0.50"}</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {data.active_habits && data.active_habits.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Active Habits ({data.active_habits.length})</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-1">
                            {data.active_habits.slice(0, 3).map((habit: any, idx: number) => (
                                <div key={idx} className="flex items-center justify-between text-xs">
                                    <span className="text-muted-foreground">{habit.habit_name}:</span>
                                    <Badge variant="secondary" className="text-[10px]">
                                        {(habit.intensity * 100).toFixed(0)}%
                                    </Badge>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            )}

            {Object.keys(policy).length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Derived Policy</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <JsonView title="Policy" value={policy} compact />
                    </CardContent>
                </Card>
            )}

            {data.recent_events && data.recent_events.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Recent Events</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-2">
                            {data.recent_events.map((event: any) => (
                                <div
                                    key={event.id}
                                    className="rounded-md border border-border/60 p-2"
                                >
                                    <div className="flex items-center justify-between">
                                        <Badge variant="outline" className="text-[10px]">
                                            {event.event_type}
                                        </Badge>
                                        <span className="text-[10px] text-muted-foreground">
                                            {new Date(event.created_ts * 1000).toLocaleTimeString()}
                                        </span>
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        {event.reason_text}
                                    </p>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            )}

            <HabitsPanel
                userId={session.userId}
                apiKeyOverride={apiKeyOverride || undefined}
            />
            <RelationsPanel
                userId={session.userId}
                apiKeyOverride={apiKeyOverride || undefined}
            />
        </div>
    );
}
