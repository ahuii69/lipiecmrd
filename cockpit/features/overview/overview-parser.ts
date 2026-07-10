import { parseDiagnosticsSummary } from "@/features/chat/diagnostics/diagnostics-parser";
import type {
    ChatTurnResponse,
    CockpitOverviewResult,
    GoalListResult,
    SystemHealthResponse,
} from "@/lib/api/types";

export interface OverviewSignal {
    label: string;
    active: boolean;
    detail?: string;
}

export interface OverviewWarning {
    code: string;
    message: string;
    severity: "high" | "medium" | "low";
}

export interface OverviewViewModel {
    hasData: boolean;
    // Last turn
    lastResponsePreview: string;
    grounding: string;
    status: string;
    provider: string;
    model: string;
    durationMs: number | null;
    totalTokens: number;
    toolsRequested: number;
    toolsAttempted: number;
    toolsSucceeded: number;
    toolsFailed: number;
    errorsCount: number;
    // Runtime signals (boolean flags)
    signals: OverviewSignal[];
    // Active goal forwarded from trace
    selectedGoalTitle: string | null;
    selectedGoalStatus: string | null;
    selectedGoalProgress: number | null;
    selectedGoalUrgency: number | null;
    // Memory layer counts (from system health)
    stmCount: number;
    episodicCount: number;
    semanticCount: number;
    // Active goals count (from goal.list_active)
    activeGoalsCount: number;
    // Derived warnings
    warnings: OverviewWarning[];
}

export function toOverviewViewModel(
    lastDiagnostics: ChatTurnResponse | undefined,
    health: SystemHealthResponse | undefined,
    goalList: GoalListResult | undefined,
): OverviewViewModel {
    const diag = parseDiagnosticsSummary(lastDiagnostics);
    const trace =
        (lastDiagnostics?.trace as Record<string, unknown> | undefined) ?? {};
    const hasData = !!lastDiagnostics;

    const rawText = lastDiagnostics?.response_text ?? "";
    const lastResponsePreview =
        rawText.length > 180 ? rawText.slice(0, 180) + "…" : rawText;

    const selGoal = trace.selected_goal as
        | Record<string, unknown>
        | null
        | undefined;
    const selectedGoalTitle =
        typeof selGoal?.title === "string" ? selGoal.title : null;
    const selectedGoalStatus =
        typeof selGoal?.status === "string" ? selGoal.status : null;
    const selectedGoalProgress =
        typeof selGoal?.progress === "number" ? selGoal.progress : null;
    const selectedGoalUrgency =
        typeof selGoal?.urgency === "number" ? selGoal.urgency : null;

    const signals: OverviewSignal[] = [
        { label: "Memory lookup", active: diag.memoryLookup },
        { label: "Psyche snapshot", active: diag.psycheSnapshot },
        {
            label: "Web triggered",
            active: diag.webTriggered,
            detail: diag.webTool ?? undefined,
        },
        {
            label: "Writeback",
            active: diag.writebackAttempted,
            detail: diag.writebackSucceeded
                ? "ok"
                : diag.writebackAttempted
                  ? "failed"
                  : undefined,
        },
        { label: "Consistency check", active: diag.consistencyCheckRan },
        { label: "Reflection", active: diag.reflectionRan },
        {
            label: "Simulation",
            active: diag.simulationRan,
            detail: diag.simulationBestAction ?? undefined,
        },
        ...(diag.selectedStrategy && diag.selectedStrategy !== "instant"
            ? [
                  {
                      label: "Strategy",
                      active: true,
                      detail: diag.selectedStrategy,
                  } satisfies OverviewSignal,
              ]
            : []),
        ...(diag.degraded
            ? [
                  {
                      label: "Degraded",
                      active: true,
                  } satisfies OverviewSignal,
              ]
            : []),
    ];

    const stmCount =
        typeof health?.stm_messages === "number" ? health.stm_messages : 0;
    const episodicCount =
        typeof health?.episodic_nodes === "number" ? health.episodic_nodes : 0;
    const semanticCount =
        typeof health?.semantic_nodes === "number" ? health.semantic_nodes : 0;

    const goals = Array.isArray(goalList?.goals) ? goalList!.goals! : [];
    const activeGoalsCount = goals.length;

    const warnings: OverviewWarning[] = [];
    if (diag.writebackFailed) {
        warnings.push({
            code: "writeback_failed",
            message: "Writeback nie zapisał — doświadczenie utracone",
            severity: "high",
        });
    }
    if (trace.degraded === true) {
        warnings.push({
            code: "degraded",
            message: "Runtime w trybie zdegradowanym",
            severity: "high",
        });
    }
    if (diag.fallback) {
        warnings.push({
            code: "fallback",
            message: "Odpowiedź model-only, bez weryfikacji narzędziowej",
            severity: "medium",
        });
    }
    if (diag.toolsFailed > 0) {
        warnings.push({
            code: "tool_failures",
            message: `${diag.toolsFailed} narzędzie(a) nie powiodło się`,
            severity: "medium",
        });
    }
    if (diag.errorsCount > 0) {
        warnings.push({
            code: "errors",
            message: `${diag.errorsCount} błąd(y) w odpowiedzi`,
            severity: "high",
        });
    }
    if (diag.contradictionsFound > 0) {
        warnings.push({
            code: "contradictions",
            message: `Wykryto sprzeczność z poprzednim kontekstem`,
            severity: "medium",
        });
    }

    return {
        hasData,
        lastResponsePreview,
        grounding: diag.groundingMode,
        status: diag.status,
        provider: diag.provider,
        model: diag.model,
        durationMs: diag.durationMs,
        totalTokens: diag.usage.totalTokens,
        toolsRequested: diag.toolsRequested,
        toolsAttempted: diag.toolsAttempted,
        toolsSucceeded: diag.toolsSucceeded,
        toolsFailed: diag.toolsFailed,
        errorsCount: diag.errorsCount,
        signals,
        selectedGoalTitle,
        selectedGoalStatus,
        selectedGoalProgress,
        selectedGoalUrgency,
        stmCount,
        episodicCount,
        semanticCount,
        activeGoalsCount,
        warnings,
    };
}

// ─── ETAP9BC Status ──────────────────────────────────────────────────────────

export interface Etap9bcStatus {
    consistencyTotal: number;
    consistencyConflicts: number;
    reflectionCount: number;
    policyName: string | null;
    simulationCount: number;
    simulationBestAction: string | null;
}

export function toEtap9bcStatus(
    data: CockpitOverviewResult | undefined,
): Etap9bcStatus | null {
    if (!data) return null;

    const stats = data.consistency?.stats ?? {};
    const consistencyTotal =
        typeof stats.total === "number" ? stats.total : 0;
    const consistencyConflicts =
        typeof stats.conflicts === "number"
            ? stats.conflicts
            : typeof stats.conflict_count === "number"
              ? stats.conflict_count
              : 0;

    const reflectionCount =
        typeof data.reflections?.count === "number"
            ? data.reflections.count
            : Array.isArray(data.reflections?.recent)
              ? data.reflections.recent.length
              : 0;

    const policyRaw = data.policy ?? {};
    const policyName =
        typeof policyRaw.profile_name === "string" && policyRaw.profile_name
            ? policyRaw.profile_name
            : typeof policyRaw.policy_profile_name === "string" &&
                policyRaw.policy_profile_name
              ? policyRaw.policy_profile_name
              : null;

    const simulationCount =
        typeof data.simulations?.count === "number"
            ? data.simulations.count
            : Array.isArray(data.simulations?.recent)
              ? data.simulations.recent.length
              : 0;

    const simulationBestAction =
        typeof data.simulations?.best_action === "string" &&
        data.simulations.best_action
            ? data.simulations.best_action
            : null;

    return {
        consistencyTotal,
        consistencyConflicts,
        reflectionCount,
        policyName,
        simulationCount,
        simulationBestAction,
    };
}
