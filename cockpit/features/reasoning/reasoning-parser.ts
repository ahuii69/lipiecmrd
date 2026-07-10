"use client";

import type {
    ReasoningPreviewResult,
    TaskGraphNode,
    TaskGraphSerialized,
} from "@/lib/api/types";

export interface ReasoningTaskView {
    id: string;
    type: string;
    summary: string;
    priority: number;
    dependsOn: string[];
    payload: Record<string, unknown>;
    status?: string;
    metadata?: Record<string, unknown>;
}

export interface ReasoningPreviewView {
    previewOnly: boolean;
    plannerSummary: Record<string, unknown>;
    tasks: ReasoningTaskView[];
    graph: TaskGraphSerialized;
    warnings: string[];
    confidence?: number;
    score?: number;
    meta?: Record<string, unknown>;
    debug?: Record<string, unknown>;
}

function safeString(value: unknown): string {
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
    }
    return "";
}

function extractSummary(payload: Record<string, unknown>): string {
    const keys = [
        "message",
        "query",
        "text",
        "goal_id",
        "goal_type",
        "intent",
        "action",
        "tool",
    ];
    for (const key of keys) {
        const val = payload[key];
        const str = safeString(val);
        if (str) return str;
    }
    return "";
}

function toTask(node: TaskGraphNode): ReasoningTaskView {
    const payload =
        node.payload && typeof node.payload === "object"
            ? (node.payload as Record<string, unknown>)
            : {};
    const dependsOn = Array.isArray(node.depends_on)
        ? node.depends_on.map((d) => safeString(d)).filter(Boolean)
        : [];
    const metadata =
        node.metadata && typeof node.metadata === "object"
            ? (node.metadata as Record<string, unknown>)
            : undefined;
    return {
        id: safeString(node.task_id) || "task",
        type: safeString(node.task_type) || "task",
        summary: extractSummary(payload),
        priority:
            typeof node.priority === "number" && Number.isFinite(node.priority)
                ? node.priority
                : 50,
        dependsOn,
        payload,
        status: safeString(node.status) || undefined,
        metadata,
    };
}

export function normalizeReasoningPreview(
    result: ReasoningPreviewResult | null | undefined,
): ReasoningPreviewView {
    const graph = (result?.graph ?? {}) as TaskGraphSerialized;
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];

    return {
        previewOnly: result?.preview_only ?? true,
        plannerSummary:
            result?.planner_summary &&
            typeof result.planner_summary === "object"
                ? (result.planner_summary as Record<string, unknown>)
                : {},
        tasks: nodes.map((node) => toTask(node)),
        graph,
        warnings: Array.isArray(result?.warnings)
            ? result?.warnings.map((w) => safeString(w)).filter(Boolean)
            : [],
        confidence:
            typeof result?.confidence === "number" &&
            Number.isFinite(result.confidence)
                ? result.confidence
                : undefined,
        score:
            typeof result?.score === "number" && Number.isFinite(result.score)
                ? result.score
                : undefined,
        meta:
            result?.meta && typeof result.meta === "object"
                ? (result.meta as Record<string, unknown>)
                : undefined,
        debug:
            result?.debug && typeof result.debug === "object"
                ? (result.debug as Record<string, unknown>)
                : undefined,
    };
}
