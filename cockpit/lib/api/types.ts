/** Typy transportu Cockpit ↔ AI-Hub (luźne pola tam, gdzie JSON jest bogaty). */

export type ToolMode = "chat" | "agent" | "readonly" | "debug";

export interface ChatMessageInput {
    role: "user" | "assistant" | "system";
    content: string;
    tool_calls?: unknown[];
}

export interface ChatTurnRequest {
    user_id: string;
    session_id: string;
    message: string;
    mode?: ToolMode;
    include_debug?: boolean;
    /** Opcjonalne w testach e2e / minimalnych payloadach — backend i tak waliduje kontekst. */
    history?: ChatMessageInput[];
    tool_policy_overrides?: Record<string, unknown>;
    attached_file_ids?: string[];
    input_via_stt?: boolean;
    /** Stable per send; retry must reuse the same key to avoid duplicate write-backs. */
    idempotency_key?: string;
    request_id?: string;
    correlation_id?: string;
    turn_id?: string;
}

export interface ProviderUsage {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
}

export interface ToolCallRequest {
    tool_call_id?: string;
    name?: string;
    arguments?: Record<string, unknown>;
}

export interface ToolCallResult {
    tool_call_id?: string;
    name?: string;
    ok?: boolean;
    output?: Record<string, unknown>;
    error?: string | null;
    latency_ms?: number;
}

export interface MemoryUsedTraceEntry {
    id: string;
    text: string;
    source: "stm" | "memory_v2" | "kg";
    is_suppressed?: boolean;
    is_pinned?: boolean;
    is_archived?: boolean;
}

export interface ChatTurnResponse {
    ok: boolean;
    response_text: string;
    model: string;
    provider: string;
    tool_calls?: ToolCallRequest[];
    tool_results?: ToolCallResult[];
    selected_mode?: ToolMode;
    usage?: ProviderUsage;
    trace?: Record<string, unknown>;
    errors?: unknown[];
    debug?: Record<string, unknown> | null;
    attachments_summary?: string | null;
    context_chips?: string[];
}

export interface ChatUploadResponse {
    ok?: boolean;
    file_id?: string;
    filename?: string;
    status?: string;
    extract_error?: string;
    detail?: string;
    [key: string]: unknown;
}

export interface CapabilityExecuteResponse {
    ok: boolean;
    mode?: ToolMode;
    tool_name?: string;
    tool_result?: ToolCallResult;
}

export interface CapabilityDescriptor {
    name: string;
    description: string;
    capability_group: string;
    enabled?: boolean;
    read_only?: boolean;
    [key: string]: unknown;
}

export interface AgentCycleResponse {
    ok?: boolean;
    cycles?: unknown[];
    summary?: Record<string, unknown>;
    [key: string]: unknown;
}

export interface AgentStatusResponse {
    state?: Record<string, unknown>;
    [key: string]: unknown;
}

export type GoalStatus = string;

export interface GoalRow {
    goal_id: string;
    title?: string;
    description?: string;
    goal_type?: string;
    status?: string;
    progress?: number;
    priority?: number;
    urgency?: number;
    importance?: number;
    confidence?: number;
    updated_at?: number;
    created_at?: number;
    tags?: unknown[];
    success_criteria?: unknown[];
    failure_criteria?: unknown[];
    metadata?: Record<string, unknown>;
    [key: string]: unknown;
}

export interface GoalListResult {
    goals?: GoalRow[];
    count?: number;
}

export interface GoalTraceEvent {
    event_id?: string;
    event_type?: string;
    ts?: number;
    data?: Record<string, unknown>;
}

export interface GoalTraceLink {
    link_id?: string;
    link_type?: string;
    entity_type?: string;
    entity_id?: string;
    ts?: number;
    payload?: Record<string, unknown>;
}

export interface GoalTrace {
    ok?: boolean;
    goal?: GoalRow;
    events?: GoalTraceEvent[];
    links?: GoalTraceLink[];
    error?: string;
}

export interface GoalCreateRequest {
    title: string;
    description?: string;
    goal_type?: string;
    source?: string;
    priority?: number;
    urgency?: number;
    importance?: number;
    confidence?: number;
    tags?: string[];
    success_criteria?: string[];
    failure_criteria?: string[];
    metadata?: Record<string, unknown>;
}

export type GoalCreateInput = GoalCreateRequest;

export interface GoalUpdateRequest {
    goal_id: string;
    status?: string;
    priority?: number;
    urgency?: number;
    importance?: number;
    confidence?: number;
    progress?: number;
    metadata?: Record<string, unknown>;
    reason?: string;
}

export type GoalUpdateInput = GoalUpdateRequest;

export interface MemoryContextResult {
    user_id?: string;
    query?: string;
    stm?: unknown[];
    episodic?: unknown[];
    semantic?: unknown[];
    psyche?: Record<string, unknown>;
    total?: number;
    memory_v2_items?: unknown[];
    [key: string]: unknown;
}

export interface MemoryV2SummaryItem {
    id?: string;
    label?: string;
    title?: string;
    content?: string;
    memory_type?: string;
    type?: string;
    source_kind?: string;
    source_ref?: string | null;
    created_ts?: number;
    updated_ts?: number;
    [key: string]: unknown;
}

export interface MemoryV2SummaryResponse {
    user_id: string;
    total_items?: number;
    facts?: MemoryV2SummaryItem[];
    preferences?: MemoryV2SummaryItem[];
    key_settlements?: MemoryV2SummaryItem[];
    procedures_highlight?: MemoryV2SummaryItem[];
}


export interface MemoryContextPackItem {
    id: string;
    source?: string;
    memory_type?: string;
    title?: string;
    content: string;
    score?: number;
    confidence?: number;
    salience?: number;
    reason_codes?: string[];
    metadata?: Record<string, unknown>;
}

export interface MemoryContextPackResponse {
    user_id: string;
    query: string;
    facts?: MemoryContextPackItem[];
    preferences?: MemoryContextPackItem[];
    procedures?: MemoryContextPackItem[];
    episodes?: MemoryContextPackItem[];
    contradictions?: MemoryContextPackItem[];
    other?: MemoryContextPackItem[];
    selected_ids?: string[];
    excluded_ids?: string[];
    token_budget_chars?: number;
    used_chars?: number;
    source_distribution?: Record<string, number>;
    retrieval_trace?: Record<string, unknown>;
}

export interface MemoryV2IndexJobsResponse {
    user_id?: string | null;
    counts?: Record<string, number>;
    total?: number;
}

/** Odpowiedź `GET /memory/v2/retrieval-explain/{user_id}`. */
export interface MemoryV2RetrievalExplanationResponse {
    user_id: string;
    query: string;
    top_reason_codes?: string[];
    match_count?: number;
    reinforced_count?: number;
    suppressed_count?: number;
    top_items_with_scores?: Record<string, unknown>[];
    retrieval_strategy?: string;
}

/** Odpowiedź `POST /memory/v2/forgetting/{user_id}`. */
export interface MemoryV2ForgettingSweepResponse {
    ok: boolean;
    evaluated_count?: number;
    suppressed_count?: number;
    threshold?: number;
}

export interface SystemHealthResponse {
    [key: string]: unknown;
}

export interface CognitiveHealthResponse {
    status?: string;
    health?: Record<string, unknown>;
    alerts?: unknown[];
    [key: string]: unknown;
}

export interface RuntimePingResponse {
    ok?: boolean;
    ts?: number;
    app?: string;
    [key: string]: unknown;
}

/** Zunifikowany podgląd ETAP9 — zagnieżdżone struktury z backendu. */
export type CockpitOverviewResult = Record<string, any>;

export interface PsycheStateResult {
    mood?: number;
    energy?: number;
    focus?: number;
    temperature?: number;
    style?: string;
    traits?: Record<string, unknown>;
    [key: string]: unknown;
}

export interface PsycheReflectionResult {
    [key: string]: unknown;
}

export interface PsycheSentimentResult {
    [key: string]: unknown;
}

export interface PlannerPreviewTask {
    [key: string]: unknown;
}

export interface PlannerPreviewResult {
    tasks?: PlannerPreviewTask[];
    count?: number;
}

export interface TaskGraphSerialized {
    [key: string]: unknown;
}

/** Węzeł grafu zwracany przez planner/reasoning preview. */
export interface TaskGraphNode {
    id?: string;
    type?: string;
    title?: string;
    summary?: string;
    priority?: number;
    dependsOn?: string[];
    payload?: Record<string, unknown>;
    hints?: string[];
    [key: string]: unknown;
}

export interface PlannerGraphResult {
    summary?: string;
    graph?: TaskGraphSerialized;
}

export interface ReasoningPreviewResult {
    preview_only?: boolean;
    planner_summary?: string;
    graph?: TaskGraphSerialized;
    warnings?: unknown[];
    confidence?: number;
    score?: number;
    meta?: Record<string, unknown>;
    debug?: Record<string, unknown>;
    [key: string]: unknown;
}

export interface ResearchQueryResult {
    results?: Array<Record<string, unknown>>;
    [key: string]: unknown;
}

export interface ResearchUrlResult {
    url?: string;
    status?: number;
    preview?: string;
    [key: string]: unknown;
}

export interface WebFetchResult {
    url?: string;
    text?: string;
    status?: number;
    [key: string]: unknown;
}
