"use client";

import type { AgentCycleResponse, AgentStatusResponse } from "@/lib/api/types";

export interface RuntimeStatusSummary {
    ok: boolean;
    mode: string;
    strategy: string;
    planning_used: boolean;
    reasoning_used: boolean;
    goal_selected: boolean;
    goal_progress: boolean;
    error_count: number;
    event_count?: number;
}

export interface ExecutionEvent {
    type: string;
    timestamp?: number;
    detail?: string;
}

export interface RuntimeTraceView {
    events: ExecutionEvent[];
    event_count: number;
    strategy?: string;
    reasoning_used?: boolean;
    planning_used?: boolean;
}

export interface AgentStateView {
    cycle: number;
    completed_goals: number;
    active_goals: number;
    memory_kb?: number;
}

function safeString(value: unknown): string {
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
    if (typeof value === "boolean") return value ? "true" : "false";
    return "";
}

function safeNumber(value: unknown): number {
    if (typeof value === "number") return value;
    if (typeof value === "string") return parseInt(value, 10) || 0;
    return 0;
}

export function normalizeRuntimeStatus(
    data: Record<string, unknown> | undefined,
): RuntimeStatusSummary {
    if (!data) {
        return {
            ok: false,
            mode: "unknown",
            strategy: "none",
            planning_used: false,
            reasoning_used: false,
            goal_selected: false,
            goal_progress: false,
            error_count: 0,
        };
    }

    const cycle = data as AgentCycleResponse & Record<string, unknown>;

    const errors = Array.isArray(cycle.errors) ? cycle.errors : [];
    const selectedGoal = cycle.selected_goal ?? null;

    const traceRecord =
        cycle.trace && typeof cycle.trace === "object"
            ? (cycle.trace as Record<string, unknown>)
            : {};
    const traceEvents = Array.isArray(traceRecord.events)
        ? traceRecord.events
        : [];

    return {
        ok: cycle.ok === true,
        mode: safeString(cycle.mode),
        strategy: safeString(cycle.strategy),
        planning_used: cycle.planning_used === true,
        reasoning_used: cycle.reasoning_used === true,
        goal_selected: selectedGoal !== null && selectedGoal !== undefined,
        goal_progress: cycle.goal_progress_changed === true,
        error_count: errors.length,
        event_count: traceEvents.length,
    };
}

export function normalizeRuntimeTrace(
    data: Record<string, unknown> | undefined,
): RuntimeTraceView {
    if (!data) {
        return {
            events: [],
            event_count: 0,
        };
    }

    const events = Array.isArray(data.events)
        ? (data.events as unknown[])
              .map((e: unknown) => {
                  if (typeof e === "object" && e !== null) {
                      const evt = e as Record<string, unknown>;
                      return {
                          type: safeString(evt.type || evt.name || "event"),
                          timestamp: safeNumber(evt.timestamp || evt.ts || 0),
                          detail: safeString(evt.detail || evt.message || ""),
                      };
                  }
                  return { type: "event", timestamp: 0, detail: "" };
              })
              .slice(0, 20)
        : [];

    return {
        events,
        event_count: events.length,
        strategy: safeString(data.strategy),
        reasoning_used:
            typeof data.reasoning_used === "boolean"
                ? data.reasoning_used
                : false,
        planning_used:
            typeof data.planning_used === "boolean"
                ? data.planning_used
                : false,
    };
}

export function normalizeAgentState(
    data: (Record<string, unknown> | AgentStatusResponse) | undefined,
): AgentStateView {
    if (!data) {
        return {
            cycle: 0,
            completed_goals: 0,
            active_goals: 0,
        };
    }

    const state = (data.state as Record<string, unknown>) ?? {};

    return {
        cycle: safeNumber(state.cycle ?? 0),
        completed_goals: safeNumber(state.completed_goals ?? 0),
        active_goals: safeNumber(state.active_goals ?? 0),
        memory_kb: safeNumber(state.memory_kb ?? 0),
    };
}
