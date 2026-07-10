"use client";

import type {
    CognitiveHealthResponse,
    RuntimePingResponse,
    SystemHealthResponse,
} from "@/lib/api/types";

export interface HealthCheckView {
    ok: boolean;
    message: string;
    alerts: string[];
    metrics: Record<string, number | string>;
}

export interface SystemPingView {
    ok: boolean;
    timestamp: number;
    app: string;
}

export interface SystemHealthView {
    user_id: string;
    short_term_memory: number;
    episodic_memory: number;
    semantic_memory: number;
    timestamp?: number;
}

export interface CognitiveHealthView {
    status: "ok" | "warning" | "error" | "unknown";
    alerts: string[];
    dbHealthy: boolean;
    graphHealthy: boolean;
    gcIssues: boolean;
    details: Record<string, unknown>;
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

export function normalizeSystemPing(
    data: RuntimePingResponse | undefined,
): SystemPingView {
    return {
        ok: data?.ok === true,
        timestamp: safeNumber(data?.ts ?? 0),
        app: safeString(data?.app ?? "aihub"),
    };
}

export function normalizeSystemHealth(
    data: SystemHealthResponse | undefined,
): SystemHealthView {
    return {
        user_id: safeString(data?.user_id ?? "unknown"),
        short_term_memory: safeNumber(data?.stm_messages ?? 0),
        episodic_memory: safeNumber(data?.episodic_nodes ?? 0),
        semantic_memory: safeNumber(data?.semantic_nodes ?? 0),
        timestamp: safeNumber(data?.ts ?? 0),
    };
}

export function normalizeCognitiveHealth(
    data: CognitiveHealthResponse | undefined,
): CognitiveHealthView {
    if (!data) {
        return {
            status: "unknown",
            alerts: [],
            dbHealthy: false,
            graphHealthy: false,
            gcIssues: false,
            details: {},
        };
    }

    const statusStr = safeString(data.status ?? "unknown").toLowerCase();
    let status: "ok" | "warning" | "error" | "unknown" = "unknown";
    if (statusStr === "ok" || statusStr === "healthy") {
        status = "ok";
    } else if (statusStr === "warning") {
        status = "warning";
    } else if (statusStr === "error" || statusStr === "unhealthy") {
        status = "error";
    }

    const alerts = Array.isArray(data.alerts)
        ? data.alerts.map((a) => safeString(a))
        : [];

    const health = (data.health as Record<string, unknown>) ?? {};
    const dbSchema = (data.db_schema as Record<string, unknown>) ?? {};
    const gcStats = (data.gc_stats as Record<string, unknown>) ?? {};
    const graphStats = (data.graph_stats as Record<string, unknown>) ?? {};

    const dbHealthy = health.db_ok === true || dbSchema.ok === true || false;
    const graphHealthy =
        graphStats.healthy === true || health.graph_ok === true || false;
    const gcIssues =
        gcStats.warning === true || gcStats.error === true || false;

    return {
        status,
        alerts,
        dbHealthy,
        graphHealthy,
        gcIssues,
        details: {
            ...health,
            db_schema: dbSchema,
            gc_stats: gcStats,
            graph_stats: graphStats,
        },
    };
}

export function getHealthColor(status: string): string {
    if (status === "ok") return "text-green-600";
    if (status === "warning") return "text-yellow-600";
    if (status === "error") return "text-red-600";
    return "text-gray-600";
}

export function getHealthBgColor(status: string): string {
    if (status === "ok") return "bg-green-950";
    if (status === "warning") return "bg-yellow-950";
    if (status === "error") return "bg-red-950";
    return "bg-gray-950";
}
