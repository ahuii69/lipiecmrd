import type { SessionState } from "@/lib/store/cockpit-store";

export type SessionGroupKey =
    | "today"
    | "yesterday"
    | "last7"
    | "older";

export const SESSION_GROUP_LABELS: Record<SessionGroupKey, string> = {
    today: "Dzisiaj",
    yesterday: "Wczoraj",
    last7: "Ostatnie 7 dni",
    older: "Starsze",
};

function startOfDay(ts: number): number {
    const d = new Date(ts);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
}

export function groupSessionsByDate(
    sessions: SessionState[],
): { key: SessionGroupKey; label: string; items: SessionState[] }[] {
    const now = Date.now();
    const todayStart = startOfDay(now);
    const yesterdayStart = todayStart - 86_400_000;
    const weekStart = todayStart - 6 * 86_400_000;

    const buckets: Record<SessionGroupKey, SessionState[]> = {
        today: [],
        yesterday: [],
        last7: [],
        older: [],
    };

    for (const s of sessions) {
        const t = s.updatedAt || s.createdAt;
        if (t >= todayStart) buckets.today.push(s);
        else if (t >= yesterdayStart) buckets.yesterday.push(s);
        else if (t >= weekStart) buckets.last7.push(s);
        else buckets.older.push(s);
    }

    return (["today", "yesterday", "last7", "older"] as const)
        .filter((k) => buckets[k].length > 0)
        .map((k) => ({
            key: k,
            label: SESSION_GROUP_LABELS[k],
            items: buckets[k].sort((a, b) => b.updatedAt - a.updatedAt),
        }));
}
