CREATE TABLE IF NOT EXISTS chat_session_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL
        , meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS chat_sessions (
            user_id TEXT NOT NULL,
            id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (user_id, id)
        );
CREATE TABLE IF NOT EXISTS chat_uploaded_files (
            file_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            extracted_text TEXT,
            extract_status TEXT NOT NULL,
            extract_error TEXT,
            created_at DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS consistency_checks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            fact_text TEXT NOT NULL,
            classification TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            matched_node_id TEXT,
            matched_content TEXT,
            similarity_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            reasoning TEXT NOT NULL DEFAULT '',
            suggested_action TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS event_log (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS experiences (
            experience_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            trace_id TEXT,
            goal_id TEXT,
            created_at DOUBLE PRECISION NOT NULL,
            user_input_summary TEXT NOT NULL,
            selected_strategy TEXT NOT NULL,
            reason_codes TEXT NOT NULL,
            tools_needed INTEGER NOT NULL DEFAULT 0,
            tools_executed INTEGER NOT NULL DEFAULT 0,
            research_needed INTEGER NOT NULL DEFAULT 0,
            research_executed INTEGER NOT NULL DEFAULT 0,
            planner_recommended INTEGER NOT NULL DEFAULT 0,
            planner_executed INTEGER NOT NULL DEFAULT 0,
            agentic_recommended INTEGER NOT NULL DEFAULT 0,
            agentic_executed INTEGER NOT NULL DEFAULT 0,
            outcome_type TEXT NOT NULL,
            success INTEGER NOT NULL,
            failure_type TEXT,
            fallback_flag INTEGER NOT NULL DEFAULT 0,
            degraded_flag INTEGER NOT NULL DEFAULT 0,
            latency_ms DOUBLE PRECISION,
            content_hash TEXT NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            embedding_input_type TEXT NOT NULL,
            semantic_embedding TEXT,
            short_lesson_learned TEXT,
            reflection_seed TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            deleted INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS goal_events (
            id BIGSERIAL PRIMARY KEY,
            goal_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS goal_links (
            id BIGSERIAL PRIMARY KEY,
            goal_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS goals (
        goal_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        goal_type TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        priority DOUBLE PRECISION NOT NULL,
        urgency DOUBLE PRECISION NOT NULL,
        importance DOUBLE PRECISION NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION,
        parent_goal_id TEXT,
        tags TEXT NOT NULL,
        success_criteria TEXT NOT NULL,
        failure_criteria TEXT NOT NULL,
        progress DOUBLE PRECISION NOT NULL,
        metadata TEXT NOT NULL
    );
CREATE TABLE IF NOT EXISTS knowledge_edges (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT,
            created_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS memory_meta (
            fact_id TEXT PRIMARY KEY,
            access_count INTEGER NOT NULL DEFAULT 0,
            last_access DOUBLE PRECISION NOT NULL DEFAULT 0,
            creation_ts DOUBLE PRECISION NOT NULL,
            usage_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            importance_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            freshness_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            overall_priority DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            stale_warning INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS memory_nodes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            layer TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL,
            meta TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL,
            importance DOUBLE PRECISION NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE IF NOT EXISTS memory_fts (
    node_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    user_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED
);
CREATE INDEX IF NOT EXISTS idx_memory_fts_user_layer ON memory_fts(user_id, layer);

ALTER TABLE memory_fts ADD COLUMN IF NOT EXISTS content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_memory_fts_gin ON memory_fts USING GIN (content_tsv);

CREATE TABLE IF NOT EXISTS memory_v2_consolidations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            consolidation_type TEXT NOT NULL,
            input_memory_ids_json TEXT NOT NULL,
            output_memory_id TEXT NOT NULL,
            compression_ratio DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS memory_v2_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            memory_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            importance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            salience_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            emotional_weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            recurrence_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            freshness_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            identity_relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            relation_relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            outcome_reinforcement_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            source_reliability_score DOUBLE PRECISION NOT NULL DEFAULT 0.7,
            retrieval_priority_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            contradiction_state TEXT NOT NULL DEFAULT 'none',
            valid_from_ts DOUBLE PRECISION,
            valid_to_ts DOUBLE PRECISION,
            last_accessed_ts DOUBLE PRECISION,
            last_reinforced_ts DOUBLE PRECISION,
            reinforcement_count INTEGER NOT NULL DEFAULT 0,
            success_reinforcements INTEGER NOT NULL DEFAULT 0,
            failure_reinforcements INTEGER NOT NULL DEFAULT 0,
            decay_bucket TEXT NOT NULL DEFAULT 'active',
            stability_tier TEXT NOT NULL DEFAULT 'transient',
            is_pinned INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_suppressed INTEGER NOT NULL DEFAULT 0,
            embedding_vector_ref TEXT,
            created_ts DOUBLE PRECISION NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS memory_v2_lessons (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            lesson_scope TEXT NOT NULL,
            lesson_text TEXT NOT NULL,
            applies_when_json TEXT NOT NULL,
            avoid_when_json TEXT NOT NULL,
            strength_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            created_ts DOUBLE PRECISION NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS memory_v2_links (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            from_memory_id TEXT NOT NULL,
            to_memory_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS memory_v2_procedures (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            trigger_pattern TEXT NOT NULL,
            recommended_strategy TEXT NOT NULL,
            recommended_tools_json TEXT NOT NULL,
            avoid_patterns_json TEXT NOT NULL,
            success_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            failure_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            last_validated_ts DOUBLE PRECISION,
            created_ts DOUBLE PRECISION NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS policy_profiles (
            user_id TEXT PRIMARY KEY,
            hints TEXT NOT NULL DEFAULT '[]',
            reliability_index DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            total_reflections INTEGER NOT NULL DEFAULT 0,
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_state (
            user_id TEXT PRIMARY KEY,
            mood DOUBLE PRECISION NOT NULL,
            energy DOUBLE PRECISION NOT NULL,
            focus DOUBLE PRECISION NOT NULL,
            style TEXT NOT NULL,
            temperature DOUBLE PRECISION NOT NULL,
            traits TEXT NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_v2_behavior_rules (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            trigger_json TEXT NOT NULL,
            behavior_adjustment_json TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_ts DOUBLE PRECISION NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_v2_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            delta_json TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            source_ref TEXT,
            created_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_v2_habits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            habit_name TEXT NOT NULL,
            habit_type TEXT NOT NULL,
            intensity DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            reinforcement_count INTEGER NOT NULL DEFAULT 0,
            last_reinforced_ts DOUBLE PRECISION NOT NULL,
            context_json TEXT NOT NULL,
            created_ts DOUBLE PRECISION NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_v2_profile (
            user_id TEXT PRIMARY KEY,
            core_directness DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_patience DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_curiosity DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_caution DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_assertiveness DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_formality DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_warmth DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_initiative DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_skepticism DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            core_creativity DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_trust DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_familiarity DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_sync DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_friction DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            relation_warmth DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_directness_tolerance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_collaboration_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            relation_interaction_quality_ema DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            stress_load DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            confidence_baseline DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            adaptation_velocity DOUBLE PRECISION NOT NULL DEFAULT 0.2,
            last_reflection_ts DOUBLE PRECISION,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS psyche_v2_state (
            user_id TEXT PRIMARY KEY,
            mood DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            energy DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            focus DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            pressure DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            stability DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            certainty DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            social_openness DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            task_aggression DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            verbosity_bias DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            tool_bias DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            web_bias DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            current_mode TEXT NOT NULL DEFAULT 'neutral',
            pending_mode TEXT NOT NULL DEFAULT '',
            mode_streak INTEGER NOT NULL DEFAULT 0,
            pressure_smoothed DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            updated_ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS reflections (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            outcome_score DOUBLE PRECISION NOT NULL,
            lesson_learned TEXT NOT NULL DEFAULT '',
            policy_signal TEXT NOT NULL DEFAULT 'neutral',
            policy_weight DOUBLE PRECISION NOT NULL DEFAULT 0.3,
            recommended_adjustment TEXT NOT NULL DEFAULT '',
            patterns_detected TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS simulations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            variants_evaluated INTEGER NOT NULL,
            best_action TEXT NOT NULL DEFAULT '',
            best_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            ranked_data TEXT NOT NULL DEFAULT '[]',
            simulation_time_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL,
            db_path TEXT NOT NULL
        );
CREATE TABLE IF NOT EXISTS stm_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            meta TEXT NOT NULL,
            ts DOUBLE PRECISION NOT NULL
        );
CREATE TABLE IF NOT EXISTS strategy_decision_bias (
            user_id TEXT PRIMARY KEY,
            bias_instant DOUBLE PRECISION NOT NULL DEFAULT 0,
            bias_contextual DOUBLE PRECISION NOT NULL DEFAULT 0,
            bias_research DOUBLE PRECISION NOT NULL DEFAULT 0,
            bias_agentic DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at DOUBLE PRECISION NOT NULL,
            metrics_snapshot TEXT NOT NULL DEFAULT '{}'
        );
CREATE TABLE IF NOT EXISTS user_vault_entries (
            user_id TEXT NOT NULL,
            alias_key TEXT NOT NULL,
            ciphertext BYTEA NOT NULL,
            updated_ts DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (user_id, alias_key)
        );
CREATE INDEX IF NOT EXISTS idx_nodes_user_layer_ts ON memory_nodes(user_id, layer, ts DESC) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_nodes_user_imp ON memory_nodes(user_id, importance DESC, confidence DESC, ts DESC) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_stm_user_ts ON stm_messages(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_user_ts ON event_log(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated ON chat_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_uploads_session ON chat_uploaded_files(user_id, session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_vault_user ON user_vault_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_sess_msg_session ON chat_session_messages(user_id, session_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts DESC);
CREATE INDEX IF NOT EXISTS idx_meta_priority ON memory_meta(overall_priority DESC) WHERE archived=0;
CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_goals_user_type ON goals(user_id, goal_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_goals_user_expires ON goals(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_goal_events_goal_ts ON goal_events(goal_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_goal_events_user_ts ON goal_events(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_goal_links_goal_ts ON goal_links(goal_id, ts DESC);
CREATE OR REPLACE VIEW memory_facts AS
        SELECT id, user_id, layer, content, tags, meta, ts AS created_ts,
               importance, confidence, deleted
        FROM memory_nodes;
CREATE INDEX IF NOT EXISTS idx_experiences_user_ts ON experiences(user_id, created_at DESC) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_experiences_user_strategy ON experiences(user_id, selected_strategy, created_at DESC) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_experiences_content_hash ON experiences(content_hash) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_experiences_trace_id ON experiences(trace_id) WHERE deleted=0 AND trace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_experiences_session_id ON experiences(session_id, created_at DESC) WHERE deleted=0 AND session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_consistency_user_ts ON consistency_checks(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_user_ts ON reflections(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_user_action ON reflections(user_id, action_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_simulations_user_ts ON simulations(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_memv2_user_type ON memory_v2_items(user_id, memory_type, created_ts DESC) WHERE is_archived=0;
CREATE INDEX IF NOT EXISTS idx_memv2_user_salience ON memory_v2_items(user_id, salience_score DESC, created_ts DESC) WHERE is_archived=0;
CREATE INDEX IF NOT EXISTS idx_memv2_user_retrieval_priority ON memory_v2_items(user_id, retrieval_priority_score DESC, created_ts DESC) WHERE is_archived=0 AND is_suppressed=0;
CREATE INDEX IF NOT EXISTS idx_memv2_user_scope ON memory_v2_items(user_id, scope, created_ts DESC) WHERE is_archived=0;
CREATE INDEX IF NOT EXISTS idx_memv2_contradictions ON memory_v2_items(user_id, contradiction_state, created_ts DESC) WHERE contradiction_state != 'none';
CREATE INDEX IF NOT EXISTS idx_memv2_decay ON memory_v2_items(user_id, decay_bucket, last_accessed_ts) WHERE is_archived=0;
CREATE INDEX IF NOT EXISTS idx_memv2_links_from ON memory_v2_links(from_memory_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memv2_links_to ON memory_v2_links(to_memory_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memv2_consolidations_user ON memory_v2_consolidations(user_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memv2_procedures_user ON memory_v2_procedures(user_id, confidence_score DESC, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memv2_procedures_trigger ON memory_v2_procedures(user_id, trigger_pattern);
CREATE INDEX IF NOT EXISTS idx_memv2_lessons_user ON memory_v2_lessons(user_id, strength_score DESC, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_psychev2_events_user ON psyche_v2_events(user_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_psychev2_rules_user ON psyche_v2_behavior_rules(user_id, priority DESC, is_active) WHERE is_active=1;
CREATE INDEX IF NOT EXISTS idx_psychev2_habits_user ON psyche_v2_habits(user_id, intensity DESC, last_reinforced_ts DESC);

CREATE SCHEMA IF NOT EXISTS sidecar;

CREATE TABLE IF NOT EXISTS sidecar.http_events (
    id TEXT PRIMARY KEY,
    ts BIGINT NOT NULL,
    method TEXT,
    path TEXT,
    query TEXT,
    status INTEGER,
    latency_ms INTEGER,
    req_headers TEXT,
    req_body_b64 TEXT,
    resp_headers TEXT,
    resp_body_b64 TEXT,
    client_ip TEXT,
    user_agent TEXT,
    api_key_fp TEXT
);
CREATE INDEX IF NOT EXISTS idx_sidecar_http_events_ts ON sidecar.http_events(ts);
CREATE INDEX IF NOT EXISTS idx_sidecar_http_events_path ON sidecar.http_events(path);

CREATE TABLE IF NOT EXISTS sidecar.psyche_rules (
    id TEXT PRIMARY KEY,
    ts BIGINT,
    kind TEXT,
    pattern TEXT,
    weight DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS sidecar.anomalies (
    id BIGSERIAL PRIMARY KEY,
    ts BIGINT,
    method TEXT,
    path TEXT,
    status INTEGER,
    expected INTEGER,
    confidence DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS sidecar.healed (
    id BIGSERIAL PRIMARY KEY,
    path TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    ts BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sidecar_healed_ts ON sidecar.healed(ts);

CREATE TABLE IF NOT EXISTS sidecar.sqlite_import_log (
    id BIGSERIAL PRIMARY KEY,
    phase TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_table TEXT NOT NULL,
    rows_inserted BIGINT NOT NULL DEFAULT 0,
    rows_skipped BIGINT NOT NULL DEFAULT 0,
    detail TEXT,
    finished_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sidecar_sqlite_import_log_finished ON sidecar.sqlite_import_log(finished_at DESC);

-- Legacy aihub.db.sqlite / aihub.api.*_router (FTS + events mirror)
CREATE SCHEMA IF NOT EXISTS compat_router;

CREATE TABLE IF NOT EXISTS compat_router.mem (
    id TEXT PRIMARY KEY,
    ts BIGINT NOT NULL,
    key TEXT,
    text TEXT NOT NULL,
    meta_json TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_access_ts BIGINT NOT NULL DEFAULT 0,
    importance DOUBLE PRECISION NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED
);
CREATE INDEX IF NOT EXISTS idx_compat_router_mem_ts ON compat_router.mem(ts DESC);
CREATE INDEX IF NOT EXISTS idx_compat_router_mem_key ON compat_router.mem(key);
CREATE INDEX IF NOT EXISTS idx_compat_router_mem_deleted ON compat_router.mem(deleted);
CREATE INDEX IF NOT EXISTS idx_compat_router_mem_tsv ON compat_router.mem USING GIN (text_tsv);

CREATE TABLE IF NOT EXISTS compat_router.policy (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    ts BIGINT NOT NULL
);

CREATE OR REPLACE VIEW compat_router.events AS
SELECT
    id,
    ts,
    COALESCE(method, '') AS method,
    COALESCE(path, '') AS path,
    query,
    status,
    latency_ms,
    req_headers,
    req_body_b64,
    resp_headers,
    resp_body_b64,
    client_ip,
    user_agent,
    api_key_fp
FROM sidecar.http_events;

CREATE TABLE IF NOT EXISTS agent_state (
    user_id TEXT PRIMARY KEY,
    last_stm_ts DOUBLE PRECISION NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_user_status_pri ON agent_tasks(user_id, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_started ON agent_tasks(started_at) WHERE status = 'running';

CREATE SCHEMA IF NOT EXISTS legacy_ui;

CREATE TABLE IF NOT EXISTS legacy_ui.kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_ui.audit (
    id TEXT PRIMARY KEY,
    ts BIGINT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    meta_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_ui.memory (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    importance DOUBLE PRECISION NOT NULL,
    created BIGINT NOT NULL,
    updated BIGINT NOT NULL,
    last_access BIGINT NOT NULL,
    access_count INTEGER NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_legacy_ui_memory_kind_updated ON legacy_ui.memory(kind, updated DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_ui_memory_updated ON legacy_ui.memory(updated DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_ui_memory_importance ON legacy_ui.memory(importance DESC);

CREATE TABLE IF NOT EXISTS legacy_ui.memory_vec (
    memory_id TEXT PRIMARY KEY REFERENCES legacy_ui.memory(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    vec BYTEA NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_ui.memory_fts (
    memory_id TEXT PRIMARY KEY REFERENCES legacy_ui.memory(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED
);
CREATE INDEX IF NOT EXISTS idx_legacy_ui_memory_fts_gin ON legacy_ui.memory_fts USING GIN (text_tsv);

CREATE TABLE IF NOT EXISTS legacy_ui.psyche_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state_json TEXT NOT NULL,
    updated BIGINT NOT NULL
);

INSERT INTO legacy_ui.psyche_state(id, state_json, updated)
SELECT 1, '{"mood":"neutral","goals":[],"beliefs":{},"traits":{},"last_reflection":0}', FLOOR(EXTRACT(EPOCH FROM NOW()))::bigint
WHERE NOT EXISTS (SELECT 1 FROM legacy_ui.psyche_state WHERE id = 1);
