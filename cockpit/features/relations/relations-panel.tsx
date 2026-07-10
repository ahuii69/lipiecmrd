"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { apiClient } from "@/lib/api/client";

interface RelationsData {
    user_id: string;
    trust: number;
    friction: number;
    warmth: number;
    directness_tolerance: number;
    collaboration_confidence: number;
    familiarity: number;
    sync: number;
}

export function RelationsPanel({
    userId,
    apiKeyOverride,
}: {
    userId: string;
    apiKeyOverride?: string;
}) {
    const [data, setData] = useState<RelationsData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let mounted = true;

        const fetchData = async () => {
            setLoading(true);
            setError(null);
            try {
                const result = await apiClient.cockpitPsycheV2Relations(
                    userId,
                    apiKeyOverride,
                );
                if (mounted) {
                    setData(result);
                }
            } catch (err: any) {
                if (mounted) {
                    setError(err.message || "Failed to load relations");
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
            <div className="p-4 text-muted-foreground">Loading relations...</div>
        );
    }

    if (error) {
        return (
            <div className="p-4 text-destructive">Error: {error}</div>
        );
    }

    if (!data) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Relations</CardTitle>
                    <CardDescription>No relation data available.</CardDescription>
                </CardHeader>
            </Card>
        );
    }

    const metrics = [
        { label: "Trust", value: data.trust, key: "trust" },
        { label: "Warmth", value: data.warmth, key: "warmth" },
        { label: "Friction", value: data.friction, key: "friction", inverse: true },
        { label: "Familiarity", value: data.familiarity, key: "familiarity" },
        { label: "Sync", value: data.sync, key: "sync" },
        { label: "Directness Tolerance", value: data.directness_tolerance, key: "directness_tolerance" },
        { label: "Collaboration Confidence", value: data.collaboration_confidence, key: "collaboration_confidence" },
    ];

    return (
        <Card>
            <CardHeader>
                <CardTitle>Relation Dynamics</CardTitle>
                <CardDescription>User-agent relationship metrics</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="space-y-4">
                    {metrics.map((metric) => (
                        <div key={metric.key}>
                            <div className="flex items-center justify-between mb-1">
                                <span className="text-sm font-medium">{metric.label}</span>
                                <span className="text-sm text-muted-foreground">
                                    {(metric.value * 100).toFixed(0)}%
                                </span>
                            </div>
                            <Progress
                                value={metric.value * 100}
                                className={
                                    metric.inverse
                                        ? metric.value > 0.5
                                            ? "bg-red-200 [&>div]:bg-red-500"
                                            : ""
                                        : metric.value > 0.7
                                            ? "bg-green-200 [&>div]:bg-green-500"
                                            : ""
                                }
                            />
                        </div>
                    ))}
                </div>
            </CardContent>
        </Card>
    );
}
