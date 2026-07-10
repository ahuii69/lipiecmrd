"use client";

import type { AgentCycleResponse } from "@/lib/api/types";

export interface AgentControlSummary {
    ok: boolean;
    strategy: string;
    strategyReason: string;
    planningUsed: boolean;
    reasoningUsed: boolean;
    selectedGoal: Record<string, unknown> | null;
    goalProgressChanged: boolean;
    executionSummary: Record<string, unknown>;
    trace: Record<string, unknown>;
    reflection: Record<string, unknown>;
    errorCount: number;
    errors: Array<Record<string, unknown>>;
}

function safeString(value: unknown): string {
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
    return "";
}

function safeBoolean(value: unknown): boolean {
    return value === true;
}

function safeRecord(value: unknown): Record<string, unknown> {
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        return value as Record<string, unknown>;
    }
    return {};
}

function safeArray(value: unknown): Array<Record<string, unknown>> {
    if (Array.isArray(value)) {
        return value.map((item) =>
            typeof item === "object" && item !== null
                ? (item as Record<string, unknown>)
                : ({} as Record<string, unknown>),
        );
    }
    return [];
}

export function normalizeAgentCycle(
    data: AgentCycleResponse | undefined,
): AgentControlSummary {
    if (!data) {
        return {
            ok: false,
            strategy: "",
            strategyReason: "",
            planningUsed: false,
            reasoningUsed: false,
            selectedGoal: null,
            goalProgressChanged: false,
            executionSummary: {} as Record<string, unknown>,
            trace: {} as Record<string, unknown>,
            reflection: {} as Record<string, unknown>,
            errorCount: 0,
            errors: [],
        };
    }

    const errors = safeArray(data.errors);

    return {
        ok: safeBoolean(data.ok),
        strategy: safeString(data.strategy),
        strategyReason: safeString(data.strategy_reason),
        planningUsed: safeBoolean(data.planning_used),
        reasoningUsed: safeBoolean(data.reasoning_used),
        selectedGoal:
            data.selected_goal &&
            typeof data.selected_goal === "object" &&
            !Array.isArray(data.selected_goal)
                ? (data.selected_goal as Record<string, unknown>)
                : null,
        goalProgressChanged: safeBoolean(data.goal_progress_changed),
        executionSummary: safeRecord(data.execution_summary),
        trace: safeRecord(data.trace),
        reflection: safeRecord(data.reflection),
        errorCount: errors.length,
        errors,
    };
}

export function getStrategyColor(strategy: string): string {
    const s = strategy.toLowerCase();
    if (s.includes("linear")) return "bg-blue-950";
    if (s.includes("parallel")) return "bg-purple-950";
    if (s.includes("adaptive")) return "bg-cyan-950";
    if (s.includes("agentic")) return "bg-orange-950";
    return "bg-slate-900";
}

export function getStrategyBadgeVariant(strategy: string): string {
    const s = strategy.toLowerCase();
    if (s.includes("linear")) return "default";
    if (s.includes("parallel")) return "secondary";
    if (s.includes("adaptive")) return "outline";
    if (s.includes("agentic")) return "warning";
    return "default";
}
