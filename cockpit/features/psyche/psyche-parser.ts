import {
    PsycheReflectionResult,
    PsycheSentimentResult,
    PsycheStateResult,
} from "@/lib/api/types";

export interface PsycheSignalView {
    mood: number | null;
    energy: number | null;
    focus: number | null;
    temperature: number | null;
    style: string;
    traits: Record<string, unknown>;
}

function asNumber(value: unknown): number | null {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    return null;
}

export function toPsycheSignalView(
    state?: PsycheStateResult,
): PsycheSignalView {
    const safe = state ?? {};
    return {
        mood: asNumber(safe.mood),
        energy: asNumber(safe.energy),
        focus: asNumber(safe.focus),
        temperature: asNumber(safe.temperature),
        style: typeof safe.style === "string" ? safe.style : "—",
        traits:
            safe.traits && typeof safe.traits === "object"
                ? (safe.traits as Record<string, unknown>)
                : {},
    };
}

export function sentimentTone(
    result?: PsycheSentimentResult,
): "positive" | "neutral" | "negative" {
    const value = asNumber(result?.sentiment);
    if (value === null) return "neutral";
    if (value > 0.15) return "positive";
    if (value < -0.15) return "negative";
    return "neutral";
}

export function reflectTopics(result?: PsycheReflectionResult): string[] {
    if (!Array.isArray(result?.topics)) return [];
    return result!.topics.filter((v): v is string => typeof v === "string");
}
