import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export function formatJson(value: unknown): string {
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

export function shortId(value: string, left = 6, right = 4): string {
    if (!value) return "";
    if (value.length <= left + right + 3) return value;
    return `${value.slice(0, left)}…${value.slice(-right)}`;
}

/** Zero-pad to 2 digits (deterministic; avoids SSR/client locale/ICU drift). */
function _utc2(n: number): string {
    return String(n).padStart(2, "0");
}

/**
 * Format timestamp for UI. Uses **UTC** numeric fields only so server and browser
 * produce identical strings (prevents Next.js hydration mismatches).
 */
export function formatTs(ts?: number | string | null): string {
    if (ts === null || ts === undefined) return "—";
    const n = typeof ts === "string" ? Number(ts) : ts;
    if (!Number.isFinite(n) || n === 0) return "—";
    const ms = n > 10_000_000_000 ? n : n * 1000;
    const d = new Date(ms);
    const day = _utc2(d.getUTCDate());
    const month = _utc2(d.getUTCMonth() + 1);
    const year = d.getUTCFullYear();
    const h = _utc2(d.getUTCHours());
    const min = _utc2(d.getUTCMinutes());
    const sec = _utc2(d.getUTCSeconds());
    return `${day}.${month}.${year}, ${h}:${min}:${sec}`;
}

export function isNonEmptyArray<T>(arr: T[] | undefined | null): arr is T[] {
    return Array.isArray(arr) && arr.length > 0;
}
