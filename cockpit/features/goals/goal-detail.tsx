"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import type { GoalRow } from "@/lib/api/types";
import { formatTs } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Loader2, XCircle } from "lucide-react";
import {
    formatGoalType,
    getStatusVariant,
    toGoalViewModel,
} from "./goals-parser";

interface GoalDetailProps {
    goal: GoalRow | null;
    onReactivate: () => void;
    onComplete: () => void;
    onFail: () => void;
    onProgressStep: () => void;
    onEditOpen: () => void;
    isActionPending: boolean;
    actionError: Error | null;
}

export function GoalDetail({
    goal,
    onReactivate,
    onComplete,
    onFail,
    onProgressStep,
    onEditOpen,
    isActionPending,
    actionError,
}: GoalDetailProps) {
    if (!goal) {
        return (
            <Card className="h-full flex flex-col">
                <CardHeader>
                    <CardTitle className="text-base">Szczegóły celu</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 flex items-center justify-center">
                    <EmptyState
                        title="Wybierz cel"
                        description="Po wyborze zobaczysz pełne szczegóły, trace i akcje."
                    />
                </CardContent>
            </Card>
        );
    }

    const vm = toGoalViewModel(goal);

    return (
        <Card className="h-full flex flex-col">
            <CardHeader>
                <CardTitle className="text-base">Szczegóły celu</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 overflow-auto space-y-3">
                {actionError ? (
                    <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300 flex items-start gap-2">
                        <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                        <div>{actionError.message}</div>
                    </div>
                ) : null}

                {/* Goal Header */}
                <div className="rounded border border-border p-3 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                        <div>
                            <p className="text-sm font-semibold line-clamp-2">
                                {vm.title}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1 line-clamp-3">
                                {vm.description}
                            </p>
                        </div>
                        <Badge variant={getStatusVariant(vm.status)}>
                            {vm.status}
                        </Badge>
                    </div>
                </div>

                {/* Signals Badges */}
                <div className="rounded border border-border p-3 space-y-2">
                    <p className="text-xs font-semibold text-muted-foreground">
                        Sygnały
                    </p>
                    <div className="flex flex-wrap gap-1">
                        <Badge variant="outline" className="text-xs">
                            type: {formatGoalType(vm.goal_type)}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            progress: {(vm.progress * 100).toFixed(0)}%
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            priority: {vm.priority.toFixed(2)}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            urgency: {vm.urgency.toFixed(2)}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            importance: {vm.importance.toFixed(2)}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            confidence: {vm.confidence.toFixed(2)}
                        </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground pt-1">
                        <p>Utworzony: {formatTs(vm.created_at)}</p>
                        <p>Zaktualizowany: {formatTs(vm.updated_at)}</p>
                    </div>
                </div>

                {/* Tags & Criteria */}
                {vm.tags.length > 0 ||
                vm.success_criteria.length > 0 ||
                vm.failure_criteria.length > 0 ? (
                    <div className="rounded border border-border p-3 space-y-2">
                        {vm.tags.length > 0 ? (
                            <div>
                                <p className="text-xs font-semibold text-muted-foreground mb-1">
                                    Tagi
                                </p>
                                <div className="flex flex-wrap gap-1">
                                    {vm.tags.map((tag) => (
                                        <Badge
                                            key={tag}
                                            variant="secondary"
                                            className="text-xs"
                                        >
                                            {tag}
                                        </Badge>
                                    ))}
                                </div>
                            </div>
                        ) : null}
                        {vm.success_criteria.length > 0 ? (
                            <div>
                                <p className="text-xs font-semibold text-muted-foreground mb-1">
                                    Kryteria sukcesu
                                </p>
                                <ul className="text-xs space-y-1">
                                    {vm.success_criteria.map((c, i) => (
                                        <li
                                            key={i}
                                            className="flex items-start gap-2"
                                        >
                                            <CheckCircle2 className="w-3 h-3 mt-0.5 text-emerald-600 flex-shrink-0" />
                                            <span>{c}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}
                        {vm.failure_criteria.length > 0 ? (
                            <div>
                                <p className="text-xs font-semibold text-muted-foreground mb-1">
                                    Kryteria porażki
                                </p>
                                <ul className="text-xs space-y-1">
                                    {vm.failure_criteria.map((c, i) => (
                                        <li
                                            key={i}
                                            className="flex items-start gap-2"
                                        >
                                            <AlertCircle className="w-3 h-3 mt-0.5 text-red-600 flex-shrink-0" />
                                            <span>{c}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}
                    </div>
                ) : null}

                {/* Actions */}
                <div className="grid grid-cols-2 gap-2">
                    <Button
                        variant="secondary"
                        size="sm"
                        onClick={onReactivate}
                        disabled={isActionPending}
                        className="text-xs"
                    >
                        {isActionPending ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                        ) : null}
                        Aktywuj
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={onEditOpen}
                        disabled={isActionPending}
                        className="text-xs"
                    >
                        Edytuj
                    </Button>
                    <Button
                        variant="default"
                        size="sm"
                        onClick={onComplete}
                        disabled={isActionPending}
                        className="text-xs"
                    >
                        {isActionPending ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                        ) : null}
                        Ukończ
                    </Button>
                    <Button
                        variant="destructive"
                        size="sm"
                        onClick={onFail}
                        disabled={isActionPending}
                        className="text-xs"
                    >
                        {isActionPending ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                        ) : null}
                        Porażka
                    </Button>
                </div>

                {vm.metadata && Object.keys(vm.metadata).length > 0 ? (
                    <JsonView title="Metadata" value={vm.metadata} />
                ) : null}
            </CardContent>
        </Card>
    );
}
