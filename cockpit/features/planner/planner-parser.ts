"use client";

import type {
    PlannerGraphResult,
    PlannerPreviewResult,
    PlannerPreviewTask,
    TaskGraphNode,
    TaskGraphSerialized,
} from "@/lib/api/types";

export interface PlannerTaskView {
    id: string;
    type: string;
    title: string;
    summary: string;
    priority: number;
    dependsOn: string[];
    payload: Record<string, unknown>;
    status?: string;
    metadata?: Record<string, unknown>;
    hints?: string[];
}

export interface PlannerPreviewView {
    tasks: PlannerTaskView[];
    count: number;
}

export interface PlannerGraphView {
    tasks: PlannerTaskView[];
    summary: Record<string, unknown>;
    graph: TaskGraphSerialized;
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

function extractHints(
    payload: Record<string, unknown>,
    metadata?: Record<string, unknown>,
): string[] {
    const hints: string[] = [];
    const bias = safeString(payload["goal_planning_bias"]);
    if (bias) hints.push(`bias:${bias}`);
    const intensity = safeString(payload["goal_research_intensity"]);
    if (intensity) hints.push(`research:${intensity}`);
    const followups = payload["goal_create_followups"];
    if (typeof followups === "boolean") {
        hints.push(`followups:${followups ? "yes" : "no"}`);
    }
    const phase = metadata ? safeString(metadata["phase"]) : "";
    if (phase) hints.push(`phase:${phase}`);
    return hints;
}

function toPreviewTask(
    task: PlannerPreviewTask,
    index: number,
): PlannerTaskView {
    const payload =
        task.payload && typeof task.payload === "object"
            ? (task.payload as Record<string, unknown>)
            : {};
    const dependsOn = Array.isArray(task.depends_on)
        ? task.depends_on.map((d) => safeString(d)).filter(Boolean)
        : [];
    const type = safeString(task.type) || "task";
    const summary = extractSummary(payload);
    return {
        id: `preview-${index + 1}`,
        type,
        title: type,
        summary,
        priority:
            typeof task.priority === "number" && Number.isFinite(task.priority)
                ? task.priority
                : 50,
        dependsOn,
        payload,
        hints: extractHints(payload),
    };
}

function toGraphTask(node: TaskGraphNode): PlannerTaskView {
    const payload =
        node.payload && typeof node.payload === "object"
            ? (node.payload as Record<string, unknown>)
            : {};
    const dependsOn = Array.isArray(node.depends_on)
        ? node.depends_on.map((d) => safeString(d)).filter(Boolean)
        : [];
    const type = safeString(node.task_type) || "task";
    const summary = extractSummary(payload);
    const metadata =
        node.metadata && typeof node.metadata === "object"
            ? (node.metadata as Record<string, unknown>)
            : undefined;
    return {
        id: safeString(node.task_id) || "task",
        type,
        title: type,
        summary,
        priority:
            typeof node.priority === "number" && Number.isFinite(node.priority)
                ? node.priority
                : 50,
        dependsOn,
        payload,
        status: safeString(node.status) || undefined,
        metadata,
        hints: extractHints(payload, metadata),
    };
}

export function normalizePlannerPreview(
    result: PlannerPreviewResult | null | undefined,
): PlannerPreviewView {
    const tasks = Array.isArray(result?.tasks)
        ? result?.tasks.map((task, index) => toPreviewTask(task, index))
        : [];
    return {
        tasks,
        count:
            typeof result?.count === "number" && Number.isFinite(result.count)
                ? result.count
                : tasks.length,
    };
}

export function normalizePlannerGraph(
    result: PlannerGraphResult | null | undefined,
): PlannerGraphView {
    const graph = (result?.graph ?? {}) as TaskGraphSerialized;
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    return {
        tasks: nodes.map((node) => toGraphTask(node)),
        summary:
            result?.summary && typeof result.summary === "object"
                ? (result.summary as Record<string, unknown>)
                : {},
        graph,
    };
}
