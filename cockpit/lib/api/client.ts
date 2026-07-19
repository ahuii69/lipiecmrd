import { unwrapCapabilityResult } from "@/lib/api/tool-result";
import type {
    AgentCycleResponse,
    AgentStatusResponse,
    CapabilityDescriptor,
    CapabilityExecuteResponse,
    CockpitOverviewResult,
    GoalCreateInput,
    GoalListResult,
    GoalTrace,
    GoalUpdateInput,
    MemoryContextPackResponse,
    MemoryContextResult,
    MemoryV2ForgettingSweepResponse,
    MemoryV2IndexJobsResponse,
    MemoryV2RetrievalExplanationResponse,
    MemoryV2SummaryResponse,
    PlannerGraphResult,
    PlannerPreviewResult,
    PsycheReflectionResult,
    PsycheSentimentResult,
    PsycheStateResult,
    ReasoningPreviewResult,
    ResearchQueryResult,
    ResearchUrlResult,
    SystemHealthResponse,
    ToolMode,
    WebFetchResult
} from "@/lib/api/types";

export class ApiClientError extends Error {
    readonly status: number;
    readonly body?: unknown;

    constructor(message: string, status: number, body?: unknown) {
        super(message);
        this.name = "ApiClientError";
        this.status = status;
        this.body = body;
    }
}

type SessionCtx = {
    user_id: string;
    session_id: string;
    mode: ToolMode;
    include_debug?: boolean;
};

export function buildAihubProxyUrl(
    path: string,
    query?: Record<string, string | boolean | number | undefined>,
): string {
    const p = path.startsWith("/") ? path : `/${path}`;
    const qs = new URLSearchParams();
    if (query) {
        for (const [k, v] of Object.entries(query)) {
            if (v === undefined) continue;
            qs.set(k, String(v));
        }
    }
    const q = qs.toString();
    return `/api/aihub${p}${q ? `?${q}` : ""}`;
}

async function hubRequest(
    method: string,
    path: string,
    opts?: {
        body?: unknown;
        apiKeyOverride?: string;
        signal?: AbortSignal;
    },
): Promise<Response> {
    const headers: Record<string, string> = { accept: "application/json" };
    if (opts?.body !== undefined) {
        headers["content-type"] = "application/json";
    }
    const url = buildAihubProxyUrl(path);
    return fetch(url, {
        method,
        headers,
        body: opts?.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: opts?.signal,
        cache: "no-store",
    });
}

async function hubJson<T>(
    method: string,
    path: string,
    opts?: {
        body?: unknown;
        apiKeyOverride?: string;
        signal?: AbortSignal;
    },
): Promise<T> {
    const res = await hubRequest(method, path, opts);
    const text = await res.text();
    if (!res.ok) {
        let detail = `Błąd API (${res.status})`;
        try {
            const j = text ? (JSON.parse(text) as Record<string, unknown>) : null;
            if (j && typeof j.detail === "string") {
                detail = j.detail;
            }
        } catch {
            if (text) detail = text.slice(0, 500);
        }
        throw new ApiClientError(detail, res.status, text);
    }
    return (text ? (JSON.parse(text) as T) : ({} as T)) as T;
}

async function execCap(
    ctx: SessionCtx,
    tool_name: string,
    arguments_: Record<string, unknown>,
    apiKeyOverride?: string,
): Promise<CapabilityExecuteResponse> {
    return hubJson<CapabilityExecuteResponse>("POST", "/chat/capabilities/execute", {
        body: {
            user_id: ctx.user_id,
            session_id: ctx.session_id,
            mode: ctx.mode,
            include_debug: Boolean(ctx.include_debug),
            tool_name,
            arguments: arguments_,
        },
        apiKeyOverride,
    });
}

export const apiClient = {
    runtimePing(apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>("GET", "/system/ping", {
            apiKeyOverride,
        });
    },

    systemHealth(userId: string, apiKeyOverride?: string) {
        return hubJson<SystemHealthResponse>(
            "GET",
            `/system/health/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        );
    },

    opsHealth(apiKeyOverride?: string) {
        return hubJson<SystemHealthResponse>("GET", "/ops/health", {
            apiKeyOverride,
        });
    },

    opsReady(apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>("GET", "/ops/ready", {
            apiKeyOverride,
        });
    },

    opsCapabilities(apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>("GET", "/ops/capabilities", {
            apiKeyOverride,
        });
    },

    cognitiveHealth(apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>("GET", "/cognitive/health", {
            apiKeyOverride,
        });
    },

    cockpitSchemaHealth(apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>("GET", "/cockpit/schema-health", {
            apiKeyOverride,
        });
    },

    getSessions(userId: string, apiKeyOverride?: string) {
        return hubJson<{
            sessions: Array<{
                id: string;
                title: string;
                created_at: number;
                updated_at: number;
                archived?: boolean;
                archived_at?: number;
            }>;
        }>("GET", `/chat/sessions?user_id=${encodeURIComponent(userId)}`, {
            apiKeyOverride,
        });
    },

    getSessionHistory(opts: {
        userId: string;
        sessionId: string;
        apiKeyOverride?: string;
        timeoutMs?: number;
    }) {
        const { userId, sessionId, apiKeyOverride, timeoutMs = 45_000 } = opts;
        const signal =
            typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
                ? AbortSignal.timeout(timeoutMs)
                : undefined;
        return hubJson<{
            session_id: string;
            messages: Array<{
                id: string;
                role: string;
                content: string;
                created_at: string;
            }>;
        }>(
            "GET",
            `/chat/session/${encodeURIComponent(sessionId)}/history?user_id=${encodeURIComponent(userId)}`,
            { apiKeyOverride, signal },
        );
    },

    renameSession(
        body: { user_id: string; session_id: string; title: string },
        apiKeyOverride?: string,
    ) {
        return hubJson("PATCH", "/chat/session/rename", {
            body,
            apiKeyOverride,
        });
    },

    deleteSession(
        body: { user_id: string; session_id: string },
        apiKeyOverride?: string,
    ) {
        return hubJson("DELETE", "/chat/session", { body, apiKeyOverride });
    },

    archiveSession(
        body: { user_id: string; session_id: string },
        apiKeyOverride?: string,
    ) {
        return hubJson<{
            ok: boolean;
            session_id: string;
            archived: boolean;
            archived_at?: number;
        }>("POST", "/chat/session/archive", { body, apiKeyOverride });
    },

    unarchiveSession(
        body: { user_id: string; session_id: string },
        apiKeyOverride?: string,
    ) {
        return hubJson<{
            ok: boolean;
            session_id: string;
            archived: boolean;
        }>("POST", "/chat/session/unarchive", { body, apiKeyOverride });
    },

    runAgent(
        body: {
            user_id: string;
            text: string;
            include_debug?: boolean;
        },
        apiKeyOverride?: string,
    ) {
        return hubJson<AgentCycleResponse>("POST", "/agent/run", {
            body,
            apiKeyOverride,
        });
    },

    runAgentLoop(
        body: {
            user_id: string;
            text: string;
            include_debug?: boolean;
            max_iters?: number;
        },
        apiKeyOverride?: string,
    ) {
        return hubJson<AgentCycleResponse>("POST", "/agent/loop", {
            body,
            apiKeyOverride,
        });
    },

    runtimeStatus(userId: string, apiKeyOverride?: string) {
        return hubJson<AgentStatusResponse>(
            "GET",
            `/agent/status/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        );
    },

    capabilities(
        mode: ToolMode,
        include_debug: boolean,
        apiKeyOverride?: string,
    ) {
        const q = new URLSearchParams({
            mode,
            include_debug: String(include_debug),
        });
        return hubJson<{
            ok: boolean;
            count?: number;
            capabilities: CapabilityDescriptor[];
        }>("GET", `/chat/capabilities?${q.toString()}`, { apiKeyOverride });
    },

    executeCapability(
        payload: SessionCtx & {
            tool_name: string;
            arguments?: Record<string, unknown>;
            confirmed?: boolean;
            tool_policy_overrides?: {
                allow_sensitive_mutations?: boolean;
            };
        },
        apiKeyOverride?: string,
    ) {
        return hubJson<CapabilityExecuteResponse>(
            "POST",
            "/chat/capabilities/execute",
            {
                body: {
                    user_id: payload.user_id,
                    session_id: payload.session_id,
                    mode: payload.mode,
                    include_debug: Boolean(payload.include_debug),
                    tool_name: payload.tool_name,
                    arguments: payload.arguments ?? {},
                    confirmed: payload.confirmed ?? false,
                    tool_policy_overrides:
                        payload.tool_policy_overrides ?? {},
                },
                apiKeyOverride,
            },
        );
    },

    memorySearch(
        ctx: SessionCtx & { query: string; limit: number },
        apiKeyOverride?: string,
    ) {
        return execCap(ctx, "memory.search", { query: ctx.query, limit: ctx.limit }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<MemoryContextResult>(r),
        );
    },

    memoryGetContext(
        ctx: SessionCtx & { query: string; limit: number },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "memory.get_context",
            { query: ctx.query, limit: ctx.limit },
            apiKeyOverride,
        ).then((r) => unwrapCapabilityResult<MemoryContextResult>(r));
    },

    getMemoryV2Summary(userId: string, apiKeyOverride?: string) {
        return hubJson<MemoryV2SummaryResponse>(
            "GET",
            `/memory/v2/summary/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        );
    },

    getMemoryContextPack(
        body: { user_id: string; query?: string; limit?: number; max_chars?: number; include_graph?: boolean },
        apiKeyOverride?: string,
    ) {
        return hubJson<MemoryContextPackResponse>("POST", "/memory/v2/context-pack", {
            body,
            apiKeyOverride,
        });
    },

    getMemoryV2IndexJobs(userId?: string, apiKeyOverride?: string) {
        const q = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
        return hubJson<MemoryV2IndexJobsResponse>("GET", `/memory/v2/index-jobs${q}`, {
            apiKeyOverride,
        });
    },

    getMemoryV2Procedures(
        userId: string,
        limit = 50,
        apiKeyOverride?: string,
    ) {
        return hubJson<unknown[]>(
            "GET",
            `/memory/v2/procedures/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        );
    },

    getMemoryV2Contradictions(
        userId: string,
        limit = 50,
        apiKeyOverride?: string,
    ) {
        return hubJson<unknown[]>(
            "GET",
            `/memory/v2/contradictions/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        );
    },

    archiveMemoryV2Item(
        body: { user_id: string; memory_id: string },
        apiKeyOverride?: string,
    ) {
        return hubJson("POST", "/memory/v2/item/archive", { body, apiKeyOverride });
    },

    suppressMemoryV2Item(
        body: { user_id: string; memory_id: string; suppressed: boolean },
        apiKeyOverride?: string,
    ) {
        return hubJson("POST", "/memory/v2/item/suppress", { body, apiKeyOverride });
    },

    pinMemoryV2Item(
        body: { user_id: string; memory_id: string; pinned: boolean },
        apiKeyOverride?: string,
    ) {
        return hubJson("POST", "/memory/v2/item/pin", { body, apiKeyOverride });
    },

    getMemoryV2RetrievalExplain(
        userId: string,
        opts?: { query?: string; top_n?: number },
        apiKeyOverride?: string,
    ) {
        const qs = new URLSearchParams();
        if (opts?.query != null && opts.query !== "") {
            qs.set("query", opts.query);
        }
        if (opts?.top_n != null) {
            qs.set("top_n", String(opts.top_n));
        }
        const tail = qs.toString();
        return hubJson<MemoryV2RetrievalExplanationResponse>(
            "GET",
            `/memory/v2/retrieval-explain/${encodeURIComponent(userId)}${tail ? `?${tail}` : ""}`,
            { apiKeyOverride },
        );
    },

    runMemoryV2ForgettingSweep(
        userId: string,
        opts?: { threshold?: number },
        apiKeyOverride?: string,
    ) {
        const th = opts?.threshold;
        const tail =
            th !== undefined && !Number.isNaN(th)
                ? `?threshold=${encodeURIComponent(String(th))}`
                : "";
        return hubJson<MemoryV2ForgettingSweepResponse>(
            "POST",
            `/memory/v2/forgetting/${encodeURIComponent(userId)}${tail}`,
            { apiKeyOverride },
        );
    },

    searchMemoryV2Raw(
        body: Record<string, unknown>,
        apiKeyOverride?: string,
    ) {
        return hubJson<Record<string, unknown>>("POST", "/memory/v2/search", {
            body,
            apiKeyOverride,
        });
    },

    createMemoryV2ItemRaw(
        body: Record<string, unknown>,
        apiKeyOverride?: string,
    ) {
        return hubJson<Record<string, unknown>>("POST", "/memory/v2/item", {
            body,
            apiKeyOverride,
        });
    },

    postMemoryV2Consolidate(userId: string, apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>(
            "POST",
            `/memory/v2/consolidate/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        );
    },

    getMemoryV2Autobio(userId: string, apiKeyOverride?: string) {
        return hubJson<Record<string, unknown>>(
            "GET",
            `/memory/v2/autobio/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        );
    },

    postMemoryV2AutobioCompact(
        userId: string,
        minEpisodeCount?: number,
        apiKeyOverride?: string,
    ) {
        const q =
            minEpisodeCount != null
                ? `?min_episode_count=${encodeURIComponent(String(minEpisodeCount))}`
                : "";
        return hubJson<Record<string, unknown>>(
            "POST",
            `/memory/v2/autobio/compact/${encodeURIComponent(userId)}${q}`,
            { apiKeyOverride },
        );
    },

    cockpitOverview(userId: string, limit: number, apiKeyOverride?: string) {
        return hubJson(
            "GET",
            `/cockpit/overview/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        ) as Promise<CockpitOverviewResult>;
    },

    cockpitPsycheV2(userId: string, apiKeyOverride?: string) {
        return hubJson("GET", `/cockpit/psyche-v2/${encodeURIComponent(userId)}`, {
            apiKeyOverride,
        }) as Promise<any>;
    },

    cockpitPsycheV2Relations(userId: string, apiKeyOverride?: string) {
        return hubJson(
            "GET",
            `/cockpit/psyche-v2/relations/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    cockpitPsycheV2Habits(userId: string, apiKeyOverride?: string) {
        return hubJson(
            "GET",
            `/cockpit/psyche-v2/habits/${encodeURIComponent(userId)}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    cockpitIdentity(userId: string, apiKeyOverride?: string) {
        return hubJson("GET", `/cockpit/identity/${encodeURIComponent(userId)}`, {
            apiKeyOverride,
        }) as Promise<any>;
    },

    cockpitCalibration(userId: string, query: string, apiKeyOverride?: string) {
        const q = new URLSearchParams({ query });
        return hubJson(
            "GET",
            `/cockpit/calibration/${encodeURIComponent(userId)}?${q.toString()}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    cockpitConsistency(
        userId: string,
        limit = 20,
        apiKeyOverride?: string,
    ) {
        return hubJson(
            "GET",
            `/cockpit/consistency/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    cockpitReflections(
        userId: string,
        limit = 20,
        apiKeyOverride?: string,
    ) {
        return hubJson(
            "GET",
            `/cockpit/reflections/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    cockpitPolicy(userId: string, apiKeyOverride?: string) {
        return hubJson("GET", `/cockpit/policy/${encodeURIComponent(userId)}`, {
            apiKeyOverride,
        }) as Promise<any>;
    },

    cockpitSimulations(
        userId: string,
        limit = 20,
        apiKeyOverride?: string,
    ) {
        return hubJson(
            "GET",
            `/cockpit/simulations/${encodeURIComponent(userId)}?limit=${encodeURIComponent(String(limit))}`,
            { apiKeyOverride },
        ) as Promise<any>;
    },

    goalListActive(ctx: SessionCtx, apiKeyOverride?: string) {
        return execCap(ctx, "goal.list_active", {}, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<GoalListResult>(r),
        );
    },

    goalTrace(
        params: { user_id: string; goal_id: string },
        apiKeyOverride?: string,
    ) {
        return hubJson<GoalTrace>(
            "GET",
            `/agent/goals/${encodeURIComponent(params.user_id)}/${encodeURIComponent(params.goal_id)}/trace`,
            { apiKeyOverride },
        );
    },

    goalCreate(ctx: SessionCtx & GoalCreateInput, apiKeyOverride?: string) {
        const { user_id, session_id, mode, include_debug, ...goalPayload } = ctx;
        return execCap(
            { user_id, session_id, mode, include_debug },
            "goal.create",
            goalPayload as Record<string, unknown>,
            apiKeyOverride,
        );
    },

    goalUpdate(ctx: SessionCtx & GoalUpdateInput, apiKeyOverride?: string) {
        const { user_id, session_id, mode, include_debug, ...goalPayload } = ctx;
        return execCap(
            { user_id, session_id, mode, include_debug },
            "goal.update",
            goalPayload as Record<string, unknown>,
            apiKeyOverride,
        );
    },

    goalComplete(
        ctx: SessionCtx & { goal_id: string; reason: string },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "goal.complete",
            { goal_id: ctx.goal_id, reason: ctx.reason },
            apiKeyOverride,
        );
    },

    goalFail(
        ctx: SessionCtx & { goal_id: string; reason: string },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "goal.fail",
            { goal_id: ctx.goal_id, reason: ctx.reason },
            apiKeyOverride,
        );
    },

    plannerPreview(ctx: SessionCtx & { text: string }, apiKeyOverride?: string) {
        return execCap(ctx, "planner.preview", { text: ctx.text }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<PlannerPreviewResult>(r),
        );
    },

    plannerBuildTaskGraph(
        ctx: SessionCtx & { text: string; include_context: boolean },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "planner.build_task_graph",
            { text: ctx.text, include_context: ctx.include_context },
            apiKeyOverride,
        ).then((r) => unwrapCapabilityResult<PlannerGraphResult>(r));
    },

    reasoningRunPreview(
        ctx: SessionCtx & { text: string },
        apiKeyOverride?: string,
    ) {
        return execCap(ctx, "reasoning.run_preview", { text: ctx.text }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<ReasoningPreviewResult>(r),
        );
    },

    researchQuery(
        ctx: SessionCtx & { query: string; research_type: string },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "research.query",
            { query: ctx.query, research_type: ctx.research_type },
            apiKeyOverride,
        ).then((r) => unwrapCapabilityResult<ResearchQueryResult>(r));
    },

    researchUrl(ctx: SessionCtx & { url: string }, apiKeyOverride?: string) {
        return execCap(ctx, "research.url", { url: ctx.url }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<ResearchUrlResult>(r),
        );
    },

    webFetchUrl(ctx: SessionCtx & { url: string }, apiKeyOverride?: string) {
        return execCap(ctx, "web.fetch_url", { url: ctx.url }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<WebFetchResult>(r),
        );
    },

    psycheReflect(
        ctx: SessionCtx & { query: string; limit: number },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "psyche.reflect",
            { query: ctx.query, limit: ctx.limit },
            apiKeyOverride,
        ).then((r) => unwrapCapabilityResult<PsycheReflectionResult>(r));
    },

    psycheAnalyzeSentiment(ctx: SessionCtx & { text: string }, apiKeyOverride?: string) {
        return execCap(ctx, "psyche.analyze_sentiment", { text: ctx.text }, apiKeyOverride).then(
            (r) => unwrapCapabilityResult<PsycheSentimentResult>(r),
        );
    },

    psycheEvolveState(
        ctx: SessionCtx & {
            text: string;
            role: "user" | "assistant" | "system";
        },
        apiKeyOverride?: string,
    ) {
        return execCap(
            ctx,
            "psyche.evolve_state",
            { text: ctx.text, role: ctx.role },
            apiKeyOverride,
        ).then((r) => unwrapCapabilityResult<PsycheStateResult>(r));
    },
};
