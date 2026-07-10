"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { apiClient } from "@/lib/api/client";
import { AlertTriangle, CheckCircle2, Info, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

interface LongHorizonStability {
    stability_tier_counts?: Record<string, number>;
    actionable_contradictions?: string[];
    transient_contradiction_hints?: string[];
    procedure_confidence_raw?: number;
    procedure_confidence_effective?: number;
    self_consistency_notes_memory?: string[];
    psyche?: Record<string, unknown>;
    habits?: Array<{
        habit_name: string;
        intensity: number;
        reinforcement_count: number;
    }>;
    self_consistency?: {
        decision: string;
        reasons: string[];
        caution_scale: number;
        procedure_conf_scale: number;
        pressure_scale: number;
        structuredness_scale: number;
    };
    psyche_runtime_after_consistency?: Record<string, unknown>;
}

interface CalibrationData {
    user_id: string;
    query: string;
    active_thresholds: Record<string, number>;
    applied_behavior_rules: Array<{
        rule: string;
        trigger: string;
        impact: string;
    }>;
    promoted_memory_items: Array<{
        type: string;
        items: any[];
    }>;
    psyche_biases: Record<string, number | string>;
    memory_context_loaded: boolean;
    psyche_context_loaded: boolean;
    contradiction_count: number;
    procedure_confidence: number;
    long_horizon_stability?: LongHorizonStability;
}

export function CalibrationPanel({ userId }: { userId: string }) {
    const [data, setData] = useState<CalibrationData | null>(null);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");

    useEffect(() => {
        fetchCalibration();
    }, [userId]);

    const fetchCalibration = async () => {
        try {
            setLoading(true);
            const result = await apiClient.cockpitCalibration(userId, query);
            setData(result);
        } catch (err) {
            console.error("Failed to fetch calibration:", err);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center p-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (!data) {
        return (
            <div className="p-4 text-sm text-muted-foreground">
                No calibration data available
            </div>
        );
    }

    return (
        <div className="space-y-4 p-4">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Info className="h-5 w-5" />
                        Calibration Debug — Runtime Behavior
                    </CardTitle>
                    <CardDescription>
                        Active thresholds, applied rules, and behavior signals for {userId}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {/* Context Status */}
                    <div className="flex gap-4">
                        <Badge variant={data.memory_context_loaded ? "default" : "secondary"}>
                            Memory: {data.memory_context_loaded ? "LOADED" : "NOT LOADED"}
                        </Badge>
                        <Badge variant={data.psyche_context_loaded ? "default" : "secondary"}>
                            Psyche: {data.psyche_context_loaded ? "LOADED" : "NOT LOADED"}
                        </Badge>
                        <Badge variant={data.contradiction_count > 0 ? "danger" : "outline"}>
                            Contradictions: {data.contradiction_count}
                        </Badge>
                        <Badge variant="outline">
                            Proc Conf: {(data.procedure_confidence * 100).toFixed(0)}%
                        </Badge>
                    </div>

                    <Separator />

                    {/* Active Thresholds */}
                    <div>
                        <h3 className="font-semibold text-sm mb-2">Active Thresholds</h3>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            {Object.entries(data.active_thresholds).map(([key, value]) => (
                                <div key={key} className="flex justify-between">
                                    <span className="text-muted-foreground">{key}:</span>
                                    <span className="font-mono">{value.toFixed(2)}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    <Separator />

                    {/* Applied Behavior Rules */}
                    <div>
                        <h3 className="font-semibold text-sm mb-2">
                            Applied Behavior Rules ({data.applied_behavior_rules.length})
                        </h3>
                        {data.applied_behavior_rules.length === 0 ? (
                            <p className="text-xs text-muted-foreground">No rules triggered for current state</p>
                        ) : (
                            <div className="space-y-2">
                                {data.applied_behavior_rules.map((rule, idx) => (
                                    <Card key={idx} className="border-l-4 border-l-green-500">
                                        <CardContent className="p-3 space-y-1">
                                            <div className="flex items-center gap-2">
                                                <CheckCircle2 className="h-4 w-4 text-green-600" />
                                                <span className="font-semibold text-sm">{rule.rule}</span>
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Trigger: {rule.trigger}
                                            </p>
                                            <p className="text-xs">
                                                Impact: {rule.impact}
                                            </p>
                                        </CardContent>
                                    </Card>
                                ))}
                            </div>
                        )}
                    </div>

                    <Separator />

                    {/* Psyche Biases */}
                    <div>
                        <h3 className="font-semibold text-sm mb-2">Psyche Biases (Runtime)</h3>
                        <div className="grid grid-cols-3 gap-2 text-xs">
                            {Object.entries(data.psyche_biases).map(([key, value]) => (
                                <div key={key} className="flex flex-col">
                                    <span className="text-muted-foreground">{key}</span>
                                    <span className="font-mono text-sm">
                                        {typeof value === "number" ? value.toFixed(2) : value}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>

                    <Separator />

                    {data.long_horizon_stability && (
                        <>
                            <Separator />
                            <div>
                                <h3 className="font-semibold text-sm mb-2 flex items-center gap-2">
                                    <AlertTriangle className="h-4 w-4" />
                                    Long-horizon stability &amp; self-consistency
                                </h3>
                                <div className="space-y-3 text-xs">
                                    {data.long_horizon_stability.self_consistency && (
                                        <div className="rounded-md border p-3 bg-muted/30">
                                            <div className="font-medium mb-1">Consistency decision</div>
                                            <Badge variant="outline" className="mb-2">
                                                {data.long_horizon_stability.self_consistency.decision}
                                            </Badge>
                                            {data.long_horizon_stability.self_consistency.reasons.length > 0 && (
                                                <ul className="list-disc pl-4 space-y-0.5 text-muted-foreground">
                                                    {data.long_horizon_stability.self_consistency.reasons.map(
                                                        (r, i) => (
                                                            <li key={i}>{r}</li>
                                                        ),
                                                    )}
                                                </ul>
                                            )}
                                            <div className="grid grid-cols-2 gap-2 mt-2 font-mono">
                                                <span>caution×{data.long_horizon_stability.self_consistency.caution_scale.toFixed(3)}</span>
                                                <span>proc×{data.long_horizon_stability.self_consistency.procedure_conf_scale.toFixed(3)}</span>
                                                <span>pressure×{data.long_horizon_stability.self_consistency.pressure_scale.toFixed(3)}</span>
                                                <span>struct×{data.long_horizon_stability.self_consistency.structuredness_scale.toFixed(3)}</span>
                                            </div>
                                        </div>
                                    )}
                                    {data.long_horizon_stability.stability_tier_counts && (
                                        <div>
                                            <span className="text-muted-foreground">Retrieval tier counts: </span>
                                            {Object.entries(data.long_horizon_stability.stability_tier_counts).map(
                                                ([k, v]) => (
                                                    <Badge key={k} variant="secondary" className="mr-1">
                                                        {k}: {v}
                                                    </Badge>
                                                ),
                                            )}
                                        </div>
                                    )}
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                        <div>
                                            <div className="text-muted-foreground mb-1">Actionable contradictions</div>
                                            <ul className="list-disc pl-4">
                                                {(data.long_horizon_stability.actionable_contradictions || []).map(
                                                    (x, i) => (
                                                        <li key={i}>{x}</li>
                                                    ),
                                                )}
                                                {(data.long_horizon_stability.actionable_contradictions || [])
                                                    .length === 0 && (
                                                    <li className="text-muted-foreground">none</li>
                                                )}
                                            </ul>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground mb-1">Transient contradiction hints</div>
                                            <ul className="list-disc pl-4">
                                                {(
                                                    data.long_horizon_stability.transient_contradiction_hints || []
                                                ).map((x, i) => (
                                                    <li key={i}>{x}</li>
                                                ))}
                                                {(data.long_horizon_stability.transient_contradiction_hints || [])
                                                    .length === 0 && (
                                                    <li className="text-muted-foreground">none</li>
                                                )}
                                            </ul>
                                        </div>
                                    </div>
                                    {(data.long_horizon_stability.procedure_confidence_raw !== undefined ||
                                        data.long_horizon_stability.procedure_confidence_effective !== undefined) && (
                                        <div className="font-mono">
                                            Procedure conf raw{" "}
                                            {((data.long_horizon_stability.procedure_confidence_raw || 0) * 100).toFixed(0)}%
                                            → effective{" "}
                                            {(
                                                (data.long_horizon_stability.procedure_confidence_effective || 0) * 100
                                            ).toFixed(0)}
                                            %
                                        </div>
                                    )}
                                    {data.long_horizon_stability.psyche && (
                                        <div className="rounded-md border p-2 space-y-1">
                                            <div className="font-medium">Psyche (DB snapshot)</div>
                                            <pre className="text-[10px] overflow-x-auto whitespace-pre-wrap break-all">
                                                {JSON.stringify(data.long_horizon_stability.psyche, null, 2)}
                                            </pre>
                                        </div>
                                    )}
                                    {data.long_horizon_stability.habits && data.long_horizon_stability.habits.length > 0 && (
                                        <div>
                                            <div className="text-muted-foreground mb-1">Habits (stability via reinforcement)</div>
                                            <ul className="space-y-1">
                                                {data.long_horizon_stability.habits.map((h) => (
                                                    <li key={h.habit_name}>
                                                        {h.habit_name}: intensity {h.intensity.toFixed(2)}, n=
                                                        {h.reinforcement_count}
                                                    </li>
                                                ))}
                                            </ul>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </>
                    )}

                    {/* Promoted Memory Items */}
                    <div>
                        <h3 className="font-semibold text-sm mb-2">Promoted Memory Items (In Prompt Context)</h3>
                        {data.promoted_memory_items.map((category, idx) => (
                            <div key={idx} className="mb-3">
                                <Badge variant="outline" className="mb-1">
                                    {category.type} ({category.items.length})
                                </Badge>
                                {category.items.length === 0 ? (
                                    <p className="text-xs text-muted-foreground ml-2">None</p>
                                ) : (
                                    <ul className="text-xs space-y-1 ml-2">
                                        {category.items.map((item: any, i: number) => (
                                            <li key={i} className="truncate">
                                                • {item.title || item.name}
                                                {item.stability_tier && (
                                                    <Badge variant="outline" className="ml-1 text-[10px]">
                                                        {item.stability_tier}
                                                    </Badge>
                                                )}
                                                {item.confidence !== undefined && item.confidence !== null && (
                                                    <span className="text-muted-foreground ml-1">
                                                        (conf: {Number(item.confidence).toFixed(2)}
                                                        {item.confidence_raw !== undefined
                                                            ? ` raw ${Number(item.confidence_raw).toFixed(2)}`
                                                            : ""}
                                                        )
                                                    </span>
                                                )}
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
