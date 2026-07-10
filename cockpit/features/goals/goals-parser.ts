"use client";

import type {
    GoalRow,
    GoalStatus,
    GoalTrace,
    GoalTraceEvent,
    GoalTraceLink,
} from "@/lib/api/types";

export interface GoalViewModel {
    goal_id: string;
    title: string;
    description: string;
    goal_type: string;
    status: string;
    progress: number;
    priority: number;
    urgency: number;
    importance: number;
    confidence: number;
    updated_at: number;
    created_at: number;
    tags: string[];
    success_criteria: string[];
    failure_criteria: string[];
    metadata: Record<string, unknown>;
}

export interface TraceEventView {
    event_id: string;
    event_type: string;
    ts: number;
    data: Record<string, unknown>;
}

export interface TraceLinkView {
    link_id: string;
    link_type: string;
    entity_type: string;
    entity_id: string;
    ts: number;
    payload: Record<string, unknown>;
}

export interface TraceViewModel {
    ok: boolean;
    goal?: GoalViewModel;
    events: TraceEventView[];
    links: TraceLinkView[];
    error?: string;
}

export interface GoalListItemViewModel {
    goalId: string;
    status: GoalStatus;
    priority: number;
    urgency: number;
    importance: number;
    confidence: number;
    progress: number;
    raw: GoalRow;
}

export interface GoalTraceEventViewModel {
    id: string;
    title: string;
    description: string;
    badges: string[];
    eventType: string;
    ts: number;
    data: Record<string, unknown>;
}

export interface GoalTraceLinkViewModel {
    id: string;
    link_type: string;
    entity_type: string;
    entity_id: string;
    ts: number;
    payload: Record<string, unknown>;
}

export interface GoalTraceViewModel {
    events: GoalTraceEventViewModel[];
    links: GoalTraceLinkViewModel[];
    linkTypeCounts: Array<{
        linkType: string;
        count: number;
    }>;
}

/**
 * Convert raw GoalRow to typed view model.
 */
export function toGoalViewModel(row: GoalRow): GoalViewModel {
    return {
        goal_id: row.goal_id,
        title: row.title ?? "",
        description: row.description ?? "",
        goal_type: row.goal_type ?? "",
        status: row.status ?? "",
        progress:
            typeof row.progress === "number" && Number.isFinite(row.progress)
                ? row.progress
                : 0,
        priority:
            typeof row.priority === "number" && Number.isFinite(row.priority)
                ? row.priority
                : 0.5,
        urgency:
            typeof row.urgency === "number" && Number.isFinite(row.urgency)
                ? row.urgency
                : 0.5,
        importance:
            typeof row.importance === "number" &&
            Number.isFinite(row.importance)
                ? row.importance
                : 0.5,
        confidence:
            typeof row.confidence === "number" &&
            Number.isFinite(row.confidence)
                ? row.confidence
                : 0.5,
        updated_at:
            typeof row.updated_at === "number" &&
            Number.isFinite(row.updated_at)
                ? row.updated_at
                : 0,
        created_at:
            typeof row.created_at === "number" &&
            Number.isFinite(row.created_at)
                ? row.created_at
                : 0,
        tags: Array.isArray(row.tags)
            ? row.tags.filter((t): t is string => typeof t === "string")
            : [],
        success_criteria: Array.isArray(row.success_criteria)
            ? row.success_criteria.filter((t): t is string => typeof t === "string")
            : [],
        failure_criteria: Array.isArray(row.failure_criteria)
            ? row.failure_criteria.filter((t): t is string => typeof t === "string")
            : [],
        metadata:
            row.metadata && typeof row.metadata === "object"
                ? (row.metadata as Record<string, unknown>)
                : {},
    };
}

/**
 * Convert raw GoalTrace response to typed view model.
 */
export function toTraceViewModel(response: Partial<GoalTrace>): TraceViewModel {
    const goal = response.goal
        ? toGoalViewModel(response.goal as GoalRow)
        : undefined;

    const events: TraceEventView[] = (response.events ?? [])
        .filter((e): e is GoalTraceEvent => e !== null && typeof e === "object")
        .map((e) => ({
            event_id:
                typeof e.event_id === "string"
                    ? e.event_id
                    : `event_${Date.now()}_${Math.random()}`,
            event_type:
                typeof e.event_type === "string" ? e.event_type : "unknown",
            ts: typeof e.ts === "number" && Number.isFinite(e.ts) ? e.ts : 0,
            data:
                e.data && typeof e.data === "object"
                    ? (e.data as Record<string, unknown>)
                    : {},
        }));

    const links: TraceLinkView[] = (response.links ?? [])
        .filter((l): l is GoalTraceLink => l !== null && typeof l === "object")
        .map((l) => ({
            link_id:
                typeof l.link_id === "string"
                    ? l.link_id
                    : `link_${Date.now()}_${Math.random()}`,
            link_type:
                typeof l.link_type === "string" ? l.link_type : "unknown",
            entity_type:
                typeof l.entity_type === "string" ? l.entity_type : "unknown",
            entity_id: typeof l.entity_id === "string" ? l.entity_id : "",
            ts: typeof l.ts === "number" && Number.isFinite(l.ts) ? l.ts : 0,
            payload:
                l.payload && typeof l.payload === "object"
                    ? (l.payload as Record<string, unknown>)
                    : {},
        }));

    return {
        ok: response.ok === true,
        goal,
        events,
        links,
        error: response.error,
    };
}

export function toGoalListItemViewModel(row: GoalRow): GoalListItemViewModel {
    const vm = toGoalViewModel(row);
    return {
        goalId: vm.goal_id,
        status: vm.status,
        priority: vm.priority,
        urgency: vm.urgency,
        importance: vm.importance,
        confidence: vm.confidence,
        progress: vm.progress,
        raw: row,
    };
}

export function toGoalTraceViewModel(
    response: Partial<GoalTrace>,
): GoalTraceViewModel {
    const normalized = toTraceViewModel(response);

    const events: GoalTraceEventViewModel[] = normalized.events.map((event) => {
        const badges = Object.keys(event.data);
        const title = event.event_type;
        const description =
            badges.length > 0
                ? `Pola: ${badges.slice(0, 4).join(", ")}${badges.length > 4 ? "…" : ""}`
                : "Brak dodatkowych pól";

        return {
            id: event.event_id,
            title,
            description,
            badges,
            eventType: event.event_type,
            ts: event.ts,
            data: event.data,
        };
    });

    const links: GoalTraceLinkViewModel[] = normalized.links.map((link) => ({
        id: link.link_id,
        link_type: link.link_type,
        entity_type: link.entity_type,
        entity_id: link.entity_id,
        ts: link.ts,
        payload: link.payload,
    }));

    const linkTypeCountsMap = new Map<string, number>();
    for (const link of links) {
        const current = linkTypeCountsMap.get(link.link_type) ?? 0;
        linkTypeCountsMap.set(link.link_type, current + 1);
    }

    const linkTypeCounts = Array.from(linkTypeCountsMap.entries())
        .map(([linkType, count]) => ({ linkType, count }))
        .sort((a, b) => b.count - a.count);

    return {
        events,
        links,
        linkTypeCounts,
    };
}

/**
 * Get status badge color variant.
 */
export function getStatusVariant(
    status: string,
): "default" | "secondary" | "outline" | "success" | "warning" | "danger" {
    switch (status?.toLowerCase()) {
        case "active":
        case "scheduled":
            return "default";
        case "completed":
            return "success";
        case "failed":
        case "error":
        case "expired":
            return "danger";
        case "pending":
        case "waiting":
            return "warning";
        default:
            return "outline";
    }
}

export const goalStatusTone = getStatusVariant;

/**
 * Format goal type to readable label.
 */
export function formatGoalType(type: string): string {
    const map: Record<string, string> = {
        task: "Zadanie",
        information_need: "Potrzeba informacji",
        research_goal: "Cel badawczy",
        maintenance_goal: "Utrzymanie",
        learning_goal: "Nauka",
        user_intent_goal: "Intencja użytkownika",
        system_goal: "System",
        long_term_goal: "Długoterminowy",
    };
    return map[type] || type;
}

/**
 * Get event icon/color by event type.
 */
export function getEventStyle(eventType: string): {
    icon: string;
    color: string;
} {
    const map: Record<string, { icon: string; color: string }> = {
        created: { icon: "✨", color: "text-blue-500" },
        updated: { icon: "📝", color: "text-amber-500" },
        activated: { icon: "▶️", color: "text-green-500" },
        completed: { icon: "✅", color: "text-emerald-600" },
        failed: { icon: "❌", color: "text-red-600" },
        blocked: { icon: "🚫", color: "text-orange-600" },
        scheduled: { icon: "📅", color: "text-purple-500" },
        expired: { icon: "⏰", color: "text-gray-500" },
    };
    return map[eventType] || { icon: "•", color: "text-muted-foreground" };
}
