"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import type { GoalRow } from "@/lib/api/types";
import { getStatusVariant } from "./goals-parser";

interface GoalsListProps {
    goals: GoalRow[];
    selectedGoalId: string | null;
    onSelectGoal: (goalId: string) => void;
    isLoading: boolean;
    error: Error | null;
}

export function GoalsList({
    goals,
    selectedGoalId,
    onSelectGoal,
    isLoading,
    error,
}: GoalsListProps) {
    return (
        <Card className="h-full flex flex-col">
            <CardHeader>
                <CardTitle className="text-base">Cele aktywne</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 overflow-auto space-y-2">
                {isLoading ? (
                    <p className="text-sm text-muted-foreground animate-pulse">
                        Ładowanie celów…
                    </p>
                ) : null}

                {error ? (
                    <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300">
                        {error.message}
                    </div>
                ) : null}

                {!goals.length && !isLoading ? (
                    <EmptyState
                        title="Brak aktywnych celów"
                        description="Dodaj nowy cel lub odpal runtime, który wygeneruje cel kontekstowo."
                    />
                ) : null}

                {goals.map((goal) => (
                    <button
                        key={goal.goal_id}
                        type="button"
                        className={`w-full rounded-md border p-2 text-left text-xs transition-colors ${
                            selectedGoalId === goal.goal_id
                                ? "border-primary bg-primary/10"
                                : "border-border bg-card/40 hover:bg-card/60"
                        }`}
                        onClick={() => onSelectGoal(goal.goal_id)}
                    >
                        <div className="mb-1 flex items-center justify-between gap-2">
                            <p className="font-semibold line-clamp-1">
                                {goal.title}
                            </p>
                            <Badge
                                variant={getStatusVariant(goal.status ?? "")}
                                className="text-xs"
                            >
                                {goal.status ?? ""}
                            </Badge>
                        </div>
                        <p className="line-clamp-2 text-muted-foreground text-xs mb-2">
                            {goal.description}
                        </p>
                        <div className="flex flex-wrap gap-1">
                            <Badge variant="secondary" className="text-xs">
                                {goal.goal_type}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                                {((goal.progress ?? 0) * 100).toFixed(0)}%
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                                p{(goal.priority ?? 0).toFixed(1)}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                                u{(goal.urgency ?? 0).toFixed(1)}
                            </Badge>
                        </div>
                    </button>
                ))}
            </CardContent>
        </Card>
    );
}
