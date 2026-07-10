import { ChatTurnResponse, ToolCallResult } from "@/lib/api/types";

export type AssistantTruthStatus =
    | "model-only"
    | "fallback"
    | "tool-verified"
    | "tool-failed"
    | "error";

export interface DiagnosticsSummary {
    status: AssistantTruthStatus;
    groundingMode: string;
    fallback: boolean;
    toolsRequested: number;
    toolsAttempted: number;
    toolsSucceeded: number;
    toolsFailed: number;
    toolExecutionOutcome: "none" | "success" | "partial" | "failed";
    provider: string;
    model: string;
    usage: {
        promptTokens: number;
        completionTokens: number;
        totalTokens: number;
    };
    errorsCount: number;
    durationMs: number | null;
    // Runtime signals from trace
    memoryLookup: boolean;
    psycheSnapshot: boolean;
    webTriggered: boolean;
    webTool: string | null;
    writebackAttempted: boolean;
    writebackSucceeded: boolean;
    writebackFailed: boolean;
    consistencyCheckRan: boolean;
    reflectionRan: boolean;
    simulationRan: boolean;
    simulationBestAction: string | null;
    selectedStrategy: string | null;
    degraded: boolean;
    contradictionsFound: number;
}

function numberFromTraceField(
    trace: Record<string, unknown>,
    key: string,
): number {
    const value = trace[key];
    if (typeof value === "number" && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === "string") {
        const n = Number(value);
        if (Number.isFinite(n)) return n;
    }
    return 0;
}

function countSucceeded(results: ToolCallResult[]): number {
    return results.filter((r) => r.ok === true).length;
}

function countFailed(results: ToolCallResult[]): number {
    return results.filter((r) => r.ok !== true || Boolean(r.error)).length;
}

export function parseDiagnosticsSummary(
    diagnostics: ChatTurnResponse | undefined,
    messageError?: string,
): DiagnosticsSummary {
    const safeTrace =
        (diagnostics?.trace as Record<string, unknown> | undefined) ?? {};
    const safeToolCalls = diagnostics?.tool_calls ?? [];
    const safeToolResults = diagnostics?.tool_results ?? [];

    const groundingMode =
        (typeof safeTrace.response_grounding_mode === "string"
            ? safeTrace.response_grounding_mode
            : "") || "unknown_not_verified";
    const fallback =
        Boolean(safeTrace.used_fallback) || groundingMode === "fallback";

    const traceRequested = numberFromTraceField(
        safeTrace,
        "tool_calls_requested",
    );
    const traceAttempted = numberFromTraceField(
        safeTrace,
        "tool_calls_executed",
    );
    const traceSucceeded = numberFromTraceField(
        safeTrace,
        "tool_calls_successful",
    );
    const traceFailed = numberFromTraceField(safeTrace, "tool_failures");

    const toolsRequested = Math.max(traceRequested, safeToolCalls.length);
    const toolsAttempted = Math.max(traceAttempted, safeToolResults.length);
    const toolsSucceeded = Math.max(
        traceSucceeded,
        countSucceeded(safeToolResults),
    );
    const toolsFailed = Math.max(traceFailed, countFailed(safeToolResults));

    const usage = {
        promptTokens: diagnostics?.usage?.prompt_tokens ?? 0,
        completionTokens: diagnostics?.usage?.completion_tokens ?? 0,
        totalTokens: diagnostics?.usage?.total_tokens ?? 0,
    };

    const errorsCount = diagnostics?.errors?.length ?? 0;

    let toolExecutionOutcome: DiagnosticsSummary["toolExecutionOutcome"] =
        "none";
    if (toolsAttempted > 0) {
        if (toolsSucceeded > 0 && toolsFailed === 0) {
            toolExecutionOutcome = "success";
        } else if (toolsSucceeded > 0 && toolsFailed > 0) {
            toolExecutionOutcome = "partial";
        } else {
            toolExecutionOutcome = "failed";
        }
    }

    let status: AssistantTruthStatus = "model-only";
    if (messageError) {
        status = "error";
    } else if (fallback) {
        status = "fallback";
    } else if (toolExecutionOutcome === "success") {
        status = "tool-verified";
    } else if (
        toolExecutionOutcome === "partial" ||
        toolExecutionOutcome === "failed"
    ) {
        status = "tool-failed";
    } else {
        status = "model-only";
    }

    // Parse runtime signals from trace
    const durationMs =
        typeof safeTrace.duration_ms === "number" ? safeTrace.duration_ms : null;
    const memoryLookup = Boolean(safeTrace.memory_lookup_happened);
    const psycheSnapshot = Boolean(safeTrace.psyche_snapshot_happened);
    const webTriggered = Boolean(safeTrace.controlled_web_triggered);
    const webTool =
        typeof safeTrace.web_tool === "string" ? safeTrace.web_tool : null;
    const writebackAttempted = Boolean(
        safeTrace.experience_write_back_attempted,
    );
    const writebackSucceeded = Boolean(
        safeTrace.experience_write_back_succeeded,
    );
    const writebackFailed =
        writebackAttempted && !writebackSucceeded;
    const consistencyCheckRan = Boolean(safeTrace.consistency_check_ran);
    const reflectionRan = Boolean(safeTrace.reflection_ran);
    const simulationRan = Boolean(safeTrace.simulation_ran);
    const simulationBestAction =
        typeof safeTrace.simulation_best_action === "string"
            ? safeTrace.simulation_best_action
            : null;
    const selectedStrategy =
        typeof safeTrace.selected_strategy === "string"
            ? safeTrace.selected_strategy
            : null;
    const degraded = Boolean(safeTrace.degraded);
    const contradictionsFound = numberFromTraceField(
        safeTrace,
        "contradictions_found",
    );

    return {
        status,
        groundingMode,
        fallback,
        toolsRequested,
        toolsAttempted,
        toolsSucceeded,
        toolsFailed,
        toolExecutionOutcome,
        provider: diagnostics?.provider || "—",
        model: diagnostics?.model || "—",
        usage,
        errorsCount,
        durationMs,
        memoryLookup,
        psycheSnapshot,
        webTriggered,
        webTool,
        writebackAttempted,
        writebackSucceeded,
        writebackFailed,
        consistencyCheckRan,
        reflectionRan,
        simulationRan,
        simulationBestAction,
        selectedStrategy,
        degraded,
        contradictionsFound,
    };
}
