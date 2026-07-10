export type CockpitSection =
    | "chat"
    | "memory"
    | "psyche"
    | "research"
    | "planner"
    | "reasoning"
    | "goals"
    | "runtime"
    | "capabilities"
    | "system"
    | "agent-control"
    | "overview"
    | "consistency"
    | "reflections"
    | "policy"
    | "simulations"
    | "memory-v2"
    | "psyche-v2"
    | "identity"
    | "contradictions"
    | "procedures"
    | "calibration";

export type ChatMode = "chat" | "agent" | "readonly" | "debug";

export type GroundingMode =
    | "model_only"
    | "tool_verified"
    | "fallback"
    | "unknown_not_verified";

export interface SessionSummary {
    id: string;
    title: string;
    userId: string;
    mode: ChatMode;
    createdAt: number;
    updatedAt: number;
}

export interface CockpitNavItem {
    id: CockpitSection;
    label: string;
    description: string;
}
