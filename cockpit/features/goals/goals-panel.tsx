"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api/client";
import type {
    GoalCreateInput,
    GoalUpdateInput
} from "@/lib/api/types";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { GoalDetail } from "./goal-detail";
import { GoalForm, type GoalFormMode } from "./goal-form";
import { GoalTraceView } from "./goal-trace-view";
import { GoalsList } from "./goals-list";
import { toTraceViewModel } from "./goals-parser";

export function GoalsPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const queryClient = useQueryClient();
    const [selectedGoalId, setSelectedGoalId] = useState<string | null>(null);
    const [formMode, setFormMode] = useState<GoalFormMode>("create");
    const [formOpen, setFormOpen] = useState(false);

    // Queries
    const goalsQuery = useQuery({
        queryKey: [
            "goals-active",
            session.userId,
            session.mode,
            apiKeyOverride,
        ],
        queryFn: async () => {
            const result = await apiClient.goalListActive(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                },
                apiKeyOverride || undefined,
            );
            return result.goals ?? [];
        },
        refetchInterval: 60_000, // Refresh every 60s
    });

    const traceQuery = useQuery({
        enabled: Boolean(selectedGoalId),
        queryKey: [
            "goal-trace",
            session.userId,
            selectedGoalId,
            apiKeyOverride,
        ],
        queryFn: async () => {
            if (!selectedGoalId) return null;
            const result = await apiClient.goalTrace(
                {
                    user_id: session.userId,
                    goal_id: selectedGoalId,
                },
                apiKeyOverride || undefined,
            );
            return toTraceViewModel(result);
        },
        refetchInterval: 30_000, // Refresh every 30s
    });

    // Mutations
    const createMutation = useMutation({
        mutationFn: async (data: GoalCreateInput) =>
            apiClient.goalCreate(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    ...data,
                },
                apiKeyOverride || undefined,
            ),
        onSuccess: () => {
            setFormOpen(false);
            queryClient.invalidateQueries({ queryKey: ["goals-active"] });
        },
    });

    const updateMutation = useMutation({
        mutationFn: async (data: GoalUpdateInput) =>
            apiClient.goalUpdate(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    ...data,
                },
                apiKeyOverride || undefined,
            ),
        onSuccess: () => {
            setFormOpen(false);
            queryClient.invalidateQueries({ queryKey: ["goals-active"] });
            queryClient.invalidateQueries({
                queryKey: ["goal-trace", session.userId, selectedGoalId],
            });
        },
    });

    const completeMutation = useMutation({
        mutationFn: async (goalId: string) =>
            apiClient.goalComplete(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    goal_id: goalId,
                    reason: "cockpit_complete",
                },
                apiKeyOverride || undefined,
            ),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["goals-active"] });
            queryClient.invalidateQueries({
                queryKey: ["goal-trace", session.userId, selectedGoalId],
            });
        },
    });

    const failMutation = useMutation({
        mutationFn: async (goalId: string) =>
            apiClient.goalFail(
                {
                    user_id: session.userId,
                    session_id: session.id,
                    mode: session.mode,
                    include_debug: session.mode === "debug",
                    goal_id: goalId,
                    reason: "cockpit_fail",
                },
                apiKeyOverride || undefined,
            ),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["goals-active"] });
            queryClient.invalidateQueries({
                queryKey: ["goal-trace", session.userId, selectedGoalId],
            });
        },
    });

    // Derived state
    const goals = useMemo(() => goalsQuery.data ?? [], [goalsQuery.data]);
    const selected = useMemo(
        () => goals.find((g) => g.goal_id === selectedGoalId) ?? null,
        [goals, selectedGoalId],
    );

    const isActionPending =
        completeMutation.isPending ||
        failMutation.isPending ||
        updateMutation.isPending;

    const actionError =
        completeMutation.error || failMutation.error || updateMutation.error;

    // Handlers
    const handleFormOpen = (mode: GoalFormMode) => {
        setFormMode(mode);
        setFormOpen(true);
    };

    const handleFormSubmit = async (
        data: GoalCreateInput | GoalUpdateInput,
    ) => {
        if (formMode === "create") {
            await createMutation.mutateAsync(data as GoalCreateInput);
        } else {
            await updateMutation.mutateAsync(data as GoalUpdateInput);
        }
    };

    const handleReactivate = async () => {
        if (!selected) return;
        await updateMutation.mutateAsync({
            goal_id: selected.goal_id,
            status: "active",
            reason: "cockpit_reactivate",
        });
    };

    const handleComplete = async () => {
        if (!selected) return;
        if (!window.confirm("Zatwierdzić ukończenie tego celu?")) return;
        await completeMutation.mutateAsync(selected.goal_id);
    };

    const handleFail = async () => {
        if (!selected) return;
        if (!window.confirm("Zatwierdzić porażkę tego celu?")) return;
        await failMutation.mutateAsync(selected.goal_id);
    };

    const handleProgressStep = async () => {
        if (!selected) return;
        const newProgress = Math.min(1, (selected.progress ?? 0) + 0.1);
        await updateMutation.mutateAsync({
            goal_id: selected.goal_id,
            progress: newProgress,
            reason: "cockpit_progress_step",
        });
    };

    return (
        <div className="flex flex-col gap-3 h-full">
            {/* Header with action button */}
            <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold">Zarządzanie celami</h2>
                <Button
                    size="sm"
                    onClick={() => handleFormOpen("create")}
                    className="text-sm"
                >
                    + Nowy cel
                </Button>
            </div>

            {/* Main grid: List + Detail + Trace */}
            <div className="flex-1 grid gap-3 lg:grid-cols-3 overflow-hidden">
                {/* Goals List */}
                <div className="lg:col-span-1 overflow-hidden">
                    <GoalsList
                        goals={goals}
                        selectedGoalId={selectedGoalId}
                        onSelectGoal={setSelectedGoalId}
                        isLoading={goalsQuery.isLoading}
                        error={goalsQuery.error}
                    />
                </div>

                {/* Goal Detail */}
                <div className="lg:col-span-1 overflow-hidden">
                    <GoalDetail
                        goal={selected}
                        onReactivate={handleReactivate}
                        onComplete={handleComplete}
                        onFail={handleFail}
                        onProgressStep={handleProgressStep}
                        onEditOpen={() => handleFormOpen("update")}
                        isActionPending={isActionPending}
                        actionError={actionError}
                    />
                </div>

                {/* Goal Trace */}
                <div className="lg:col-span-1 overflow-hidden">
                    <GoalTraceView
                        traceData={traceQuery.data ?? null}
                        isLoading={traceQuery.isLoading}
                        error={traceQuery.error}
                        goalId={selectedGoalId}
                    />
                </div>
            </div>

            {/* Goal Form Dialog */}
            <GoalForm
                mode={formMode}
                goal={selected}
                isOpen={formOpen}
                onOpenChange={setFormOpen}
                onSubmit={handleFormSubmit}
                isPending={createMutation.isPending || updateMutation.isPending}
                error={createMutation.error || updateMutation.error}
            />
        </div>
    );
}
