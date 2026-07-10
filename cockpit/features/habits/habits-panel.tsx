"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { apiClient } from "@/lib/api/client";

interface Habit {
    habit_name: string;
    habit_type: string;
    intensity: number;
    reinforcement_count: number;
    last_reinforced_ts: number;
}

interface HabitsData {
    user_id: string;
    habits: Habit[];
    total_count: number;
}

export function HabitsPanel({
    userId,
    apiKeyOverride,
}: {
    userId: string;
    apiKeyOverride?: string;
}) {
    const [data, setData] = useState<HabitsData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let mounted = true;

        const fetchData = async () => {
            setLoading(true);
            setError(null);
            try {
                const result = await apiClient.cockpitPsycheV2Habits(
                    userId,
                    apiKeyOverride,
                );
                if (mounted) {
                    setData(result);
                }
            } catch (err: any) {
                if (mounted) {
                    setError(err.message || "Failed to load habits");
                }
            } finally {
                if (mounted) {
                    setLoading(false);
                }
            }
        };

        fetchData();
        return () => {
            mounted = false;
        };
    }, [userId, apiKeyOverride]);

    if (loading) {
        return (
            <div className="p-4 text-muted-foreground">Loading habits...</div>
        );
    }

    if (error) {
        return (
            <div className="p-4 text-destructive">Error: {error}</div>
        );
    }

    if (!data || data.total_count === 0) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Habits</CardTitle>
                    <CardDescription>No active habits detected yet.</CardDescription>
                </CardHeader>
            </Card>
        );
    }

    const getIntensityColor = (intensity: number) => {
        if (intensity >= 0.7) return "bg-red-500";
        if (intensity >= 0.5) return "bg-orange-500";
        if (intensity >= 0.3) return "bg-yellow-500";
        return "bg-gray-500";
    };

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle>Active Habits</CardTitle>
                    <CardDescription>
                        Learned behavioral patterns ({data.total_count} total)
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="space-y-3">
                        {data.habits.map((habit, idx) => (
                            <div
                                key={idx}
                                className="flex items-center justify-between border-b pb-2 last:border-0"
                            >
                                <div className="flex-1">
                                    <div className="font-medium">{habit.habit_name}</div>
                                    <div className="text-sm text-muted-foreground">
                                        {habit.habit_type} · reinforced {habit.reinforcement_count}x
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Badge className={getIntensityColor(habit.intensity)}>
                                        {(habit.intensity * 100).toFixed(0)}%
                                    </Badge>
                                </div>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
