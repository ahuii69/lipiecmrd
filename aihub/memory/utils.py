import time
import json
import uuid
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

# systemowe zależności
from .memory import _db, ltm_add, ltm_search_hybrid
from .helpers import log_info, log_error, log_warning, tokenize

# -------------------- Konfiguracja --------------------

EPISODE_CONSOLIDATION_THRESHOLD = 5
PROCEDURE_LEARNING_THRESHOLD = 3
MENTAL_MODEL_THRESHOLD = 10
CONTEXT_SIMILARITY_THRESHOLD = 0.7
AUTO_CONSOLIDATION_INTERVAL = 3600

EPISODIC_TO_SEMANTIC_THRESHOLD = 0.6
SEMANTIC_CONSOLIDATION_INTERVAL = 100
PROCEDURAL_ADAPTATION_RATE = 0.1
MENTAL_MODEL_UPDATE_FREQUENCY = 50
MAX_CONTEXT_SUMMARY_LEN = 2000

# -------------------- Utilities --------------------

def _conn() -> sqlite3.Connection:
    con = _db()
    con.row_factory = sqlite3.Row
    return con

def _json_loads(maybe_json) -> Dict[str, Any]:
    if isinstance(maybe_json, dict):
        return maybe_json
    if not maybe_json:
        return {}
    try:
        return json.loads(maybe_json)
    except Exception:
        return {}

def _now() -> float:
    return time.time()

# -------------------- L1: Episodic --------------------

class EpisodicMemoryManager:
    def __init__(self):
        self._init_db()
        self._lock = threading.Lock()

    def _init_db(self):
        with _conn() as conn:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                type TEXT NOT NULL,
                summary TEXT NOT NULL,
                related_stm_ids TEXT,
                metadata TEXT
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user_ts ON memory_episodes(user_id, timestamp DESC);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_type ON memory_episodes(type);")

    def record_episode(self, user_id: str, episode_type: str, summary: str,
                       related_stm_ids: Optional[List[str]] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> str:
        eid = str(uuid.uuid4())
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        stm = json.dumps(related_stm_ids or [], ensure_ascii=False)
        with self._lock, _conn() as conn:
            conn.execute(
                "INSERT INTO memory_episodes (id, user_id, timestamp, type, summary, related_stm_ids, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, user_id, _now(), episode_type, summary, stm, meta)
            )
        log_info(f"L1 zapis epizodu: {eid}", "HIER_MEM")
        return eid

    def get_recent_episodes(self, limit: int = 10) -> List[Dict[str, Any]]:
        with _conn() as conn:
            rows = conn.execute("SELECT * FROM memory_episodes ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_memory_stats(self) -> Dict[str, Any]:
        with _conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM memory_episodes").fetchone()["c"]
            recent_24h = conn.execute("SELECT COUNT(*) AS c FROM memory_episodes WHERE timestamp > ?", (_now() - 24*3600,)).fetchone()["c"]
        return {
            "total_episodes": total,
            "recent_24h": recent_24h,
            "health_score": min(1.0, total / 100.0),
            "avg_episodes_per_day": recent_24h
        }

    def find_related_episodes(self, query: str, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        qtokens = set(tokenize((query or "").lower()))
        if not qtokens:
            return []
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, type, summary, metadata FROM memory_episodes WHERE user_id = ? ORDER BY timestamp DESC",
                (user_id,)
            ).fetchall()

        scored = []
        now = _now()
        for r in rows:
            summary = r["summary"] or ""
            stokens = set(tokenize(summary.lower()))
            inter = len(qtokens & stokens)
            union = len(qtokens | stokens) or 1
            jacc = inter / union
            age_h = (now - (r["timestamp"] or now)) / 3600.0
            recency = max(0.0, 1.0 - age_h / (24 * 7))
            score = jacc + 0.2 * recency
            if jacc > 0.1:
                d = dict(r)
                d["similarity_score"] = score
                d["metadata"] = _json_loads(d.get("metadata"))
                scored.append(d)

        scored.sort(key=lambda x: x["similarity_score"], reverse=True)
        return scored[:limit]

    def get_episode_patterns(self, user_id: str) -> Dict[str, Any]:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT timestamp, type, summary, metadata FROM memory_episodes WHERE user_id = ? ORDER BY timestamp DESC LIMIT 100",
                (user_id,)
            ).fetchall()
        if not rows:
            return {"patterns": [], "frequent_topics": [], "activity_rhythm": {}}

        all_topics: List[str] = []
        ts_list: List[float] = []
        for r in rows:
            all_topics.extend(tokenize((r["summary"] or "").lower()))
            ts_list.append(r["timestamp"])
        topic_counts = Counter(all_topics)
        frequent_topics = [t for t, c in topic_counts.most_common(10) if c > 2]
        hours = [time.localtime(t).tm_hour for t in ts_list]
        hour_counts = Counter(hours)
        patterns = self._detect_sequence_patterns([r["summary"] or "" for r in rows])
        peak_hours = [h for h, _ in hour_counts.most_common(3)]
        return {
            "patterns": patterns,
            "frequent_topics": frequent_topics,
            "peak_hours": peak_hours,
            "total_episodes": len(rows),
            "activity_rhythm": dict(hour_counts)
        }

    def _detect_sequence_patterns(self, summaries: List[str]) -> List[Dict[str, Any]]:
        patterns = []
        for i in range(len(summaries) - 1):
            a = set(tokenize(summaries[i].lower()))
            b = set(tokenize(summaries[i+1].lower()))
            common = a & b
            if len(common) >= 2:
                patterns.append({
                    "type": "sequential_topic",
                    "tokens": sorted(list(common))[:6],
                    "frequency": 1,
                    "positions": [i, i+1]
                })
        grouped: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for p in patterns:
            key = tuple(sorted(p["tokens"]))
            if key in grouped:
                grouped[key]["frequency"] += 1
            else:
                grouped[key] = p
        return list(grouped.values())

# -------------------- L2: Semantic --------------------

class SemanticMemoryManager:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        with _conn() as conn:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS memory_semantic_clusters (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                cluster_topic TEXT NOT NULL,
                related_episodes TEXT,
                consolidated_fact_id TEXT,
                strength REAL DEFAULT 1.0,
                last_reinforced REAL,
                created_at REAL
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_clusters_user_topic ON memory_semantic_clusters(user_id, cluster_topic);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_clusters_strength ON memory_semantic_clusters(strength DESC);")

    def consolidate_fact_from_episodes(self, episodes: List[Dict[str, Any]], user_id: str) -> Optional[str]:
        if len(episodes) < EPISODE_CONSOLIDATION_THRESHOLD:
            return None
        analysis = self._analyze_episode_topics(episodes)
        theme = analysis.get("dominant_theme")
        if not theme:
            return None
        existing = self._find_existing_cluster(user_id, theme)
        if existing:
            self._reinforce_cluster(existing["id"], episodes)
            return existing.get("consolidated_fact_id")

        fact_text = self._generate_intelligent_fact(analysis, episodes)
        tags = f"user:{user_id},semantic,consolidated,{theme}"
        confidence = self._calc_consolidation_conf(episodes, analysis)
        fact_id = ltm_add(fact_text, tags, conf=confidence)
        self._create_semantic_cluster(user_id, theme, [e.get("id") for e in episodes if e.get("id")], fact_id)
        log_info(f"L2 nowy fakt: '{fact_text[:60]}...' conf={confidence:.2f}", "HIER_MEM")
        return fact_id

    def _analyze_episode_topics(self, episodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        all_tokens: List[str] = []
        temporal = []
        for ep in episodes:
            toks = tokenize((ep.get("summary") or "").lower())
            all_tokens.extend(toks)
            md = _json_loads(ep.get("metadata"))
            temporal.append({
                "timestamp": ep.get("timestamp", _now()),
                "intent": md.get("intent", "unknown"),
                "tokens": toks
            })
        cnt = Counter(all_tokens)
        candidates = []
        for tok, n in cnt.most_common(20):
            if n < 2:
                continue
            ctx = self._topic_context_strength(tok, temporal)
            candidates.append((tok, n * ctx))
        candidates.sort(key=lambda x: x[1], reverse=True)
        dom = candidates[0][0] if candidates else None
        return {
            "dominant_theme": dom,
            "all_themes": [t for t, _ in candidates[:5]],
            "topic_distribution": dict(cnt.most_common(10)),
            "temporal_consistency": self._temporal_consistency(temporal),
            "intent_diversity": len(set(p["intent"] for p in temporal))
        }

    def _topic_context_strength(self, topic: str, temporal_rows: List[Dict[str, Any]]) -> float:
        hits = [p for p in temporal_rows if topic in p["tokens"]]
        if len(hits) < 2:
            return 0.5
        times = [p["timestamp"] for p in hits]
        spread = max(times) - min(times)
        time_factor = min(1.0, spread / 3600.0)
        intents = set(p["intent"] for p in hits)
        intent_factor = min(1.0, len(intents) / 3.0)
        return 0.5 + 0.3 * time_factor + 0.2 * intent_factor

    def _temporal_consistency(self, temporal_rows: List[Dict[str, Any]]) -> float:
        if len(temporal_rows) < 2:
            return 0.0
        ts = sorted([p["timestamp"] for p in temporal_rows])
        gaps = [ts[i+1] - ts[i] for i in range(len(ts)-1)]
        if not gaps:
            return 0.0
        avg = sum(gaps) / len(gaps)
        var = sum((g - avg) ** 2 for g in gaps) / len(gaps)
        std = var ** 0.5
        return max(0.0, 1.0 - (std / avg)) if avg > 0 else 0.0

    def _generate_intelligent_fact(self, analysis: Dict[str, Any], episodes: List[Dict[str, Any]]) -> str:
        theme = analysis["dominant_theme"]
        intent_div = analysis["intent_diversity"]
        temp_cons = analysis["temporal_consistency"]
        if temp_cons > 0.7:
            base = f"Użytkownik konsekwentnie interesuje się tematem '{theme}'"
        elif intent_div > 2:
            base = f"Użytkownik porusza temat '{theme}' w różnych kontekstach"
        else:
            base = f"Użytkownik wykazuje zainteresowanie tematem '{theme}'"
        rel = [t for t in analysis["all_themes"][1:3] if t != theme]
        if rel:
            base += f", często w połączeniu z: {', '.join(rel)}"
        if temp_cons > 0.5:
            base += ". Występują regularne wzorce czasowe"
        return base + "."

    def _calc_consolidation_conf(self, episodes: List[Dict[str, Any]], analysis: Dict[str, Any]) -> float:
        base = 0.7
        base += min(0.15, (len(episodes) - EPISODE_CONSOLIDATION_THRESHOLD) * 0.03)
        base += min(0.1, len(analysis["all_themes"]) / 10.0)
        base += analysis["temporal_consistency"] * 0.05
        return min(0.95, base)

    def _find_existing_cluster(self, user_id: str, theme: str) -> Optional[Dict[str, Any]]:
        with _conn() as conn:
            row = conn.execute(
                "SELECT id, consolidated_fact_id, strength FROM memory_semantic_clusters WHERE user_id = ? AND cluster_topic = ?",
                (user_id, theme)
            ).fetchone()
        return dict(row) if row else None

    def _create_semantic_cluster(self, user_id: str, theme: str, episode_ids: List[str], fact_id: str) -> str:
        cid = str(uuid.uuid4())
        with _conn() as conn:
            conn.execute(
                "INSERT INTO memory_semantic_clusters (id, user_id, cluster_topic, related_episodes, consolidated_fact_id, strength, last_reinforced, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, user_id, theme, json.dumps(episode_ids or []), fact_id, 1.0, _now(), _now())
            )
        return cid

    def _reinforce_cluster(self, cid: str, new_eps: List[Dict[str, Any]]):
        with _conn() as conn:
            row = conn.execute("SELECT related_episodes, strength FROM memory_semantic_clusters WHERE id = ?", (cid,)).fetchone()
            if not row:
                return
            have = set(json.loads(row["related_episodes"] or "[]"))
            add = {e.get("id") for e in new_eps if e.get("id")}
            updated = json.dumps(list(have | add), ensure_ascii=False)
            new_strength = min(5.0, (row["strength"] or 1.0) + 0.2)
            conn.execute(
                "UPDATE memory_semantic_clusters SET related_episodes = ?, strength = ?, last_reinforced = ? WHERE id = ?",
                (updated, new_strength, _now(), cid)
            )

    def get_all_facts(self, limit: int = 1000) -> List[Dict[str, Any]]:
        return ltm_search_hybrid("", limit=limit)

    def search_facts(self, query: str, limit: int = 15, min_confidence: float = 0.4) -> List[Dict[str, Any]]:
        res = ltm_search_hybrid(query or "", limit=limit)
        return [r for r in res if r.get("conf", 0.0) >= min_confidence][:limit]

    def get_memory_stats(self) -> Dict[str, Any]:
        # Bezpośrednie tabele LTM mogą się różnić. Używaj LTM API defensywnie.
        try:
            facts = ltm_search_hybrid("", limit=1000)
            avg_conf = (sum(f.get("conf", 0.0) for f in facts) / len(facts)) if facts else 0.0
        except Exception:
            facts, avg_conf = [], 0.0
        with _conn() as conn:
            clusters = conn.execute("SELECT COUNT(*) AS c FROM memory_semantic_clusters").fetchone()["c"]
        return {
            "total_facts": len(facts),
            "total_clusters": clusters,
            "average_confidence": avg_conf,
            "health_score": min(1.0, avg_conf)
        }

# -------------------- L3: Procedural --------------------

class ProceduralMemoryManager:
    def __init__(self):
        self._init_db()
        self._lock = threading.Lock()

    def _init_db(self):
        with _conn() as conn:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS memory_procedures (
                id TEXT PRIMARY KEY,
                trigger_intent TEXT NOT NULL UNIQUE,
                steps TEXT NOT NULL,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                avg_execution_time REAL DEFAULT 0.0,
                context_conditions TEXT,
                last_used REAL,
                created_at REAL,
                adaptations TEXT
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_proc_success_rate ON memory_procedures(success_rate DESC);")

    def learn_or_update_procedure(self, trigger_intent: str, steps: List[str],
                                  execution_time: float = 0.0, success: bool = True,
                                  context: Optional[Dict[str, Any]] = None) -> str:
        with self._lock, _conn() as conn:
            row = conn.execute("SELECT * FROM memory_procedures WHERE trigger_intent = ?", (trigger_intent,)).fetchone()
            now = _now()
            if row:
                succ = row["success_count"] + (1 if success else 0)
                fail = row["failure_count"] + (0 if success else 1)
                total = max(1, succ + fail)
                rate = succ / total
                old_avg = row["avg_execution_time"] or 0.0
                execs = row["success_count"] + row["failure_count"]
                new_avg = ((old_avg * execs) + max(0.0, execution_time)) / (execs + 1) if execution_time > 0 else old_avg
                cond = _json_loads(row["context_conditions"])
                cond = self._update_context_conditions(cond, context or {}, success)
                adpts = _json_loads(row["adaptations"]) or []
                if rate < 0.6 and (not adpts or adpts[-1].get("steps") != steps):
                    adpts.append({"timestamp": now, "version": len(adpts) + 1, "steps": steps, "reason": f"performance_drop_{rate:.2f}"})
                    adpts = adpts[-5:]
                conn.execute(
                    "UPDATE memory_procedures SET success_count=?, failure_count=?, success_rate=?, avg_execution_time=?, context_conditions=?, last_used=?, adaptations=? WHERE id=?",
                    (succ, fail, rate, new_avg, json.dumps(cond), now, json.dumps(adpts), row["id"])
                )
                return row["id"]
            else:
                pid = str(uuid.uuid4())
                cond = self._analyze_initial_context(context or {})
                adpts = [{"timestamp": now, "version": 1, "steps": steps, "reason": "initial_creation"}]
                conn.execute(
                    "INSERT INTO memory_procedures (id, trigger_intent, steps, success_count, failure_count, success_rate, avg_execution_time, context_conditions, last_used, created_at, adaptations) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, trigger_intent, json.dumps(steps), 1 if success else 0, 0 if success else 1,
                     1.0 if success else 0.0, max(0.0, execution_time), json.dumps(cond), now, now, json.dumps(adpts))
                )
                return pid

    def get_procedure(self, trigger_intent: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        with _conn() as conn:
            row = conn.execute("SELECT * FROM memory_procedures WHERE trigger_intent = ?", (trigger_intent,)).fetchone()
        if not row or row["success_count"] < PROCEDURE_LEARNING_THRESHOLD:
            return None
        match = self._context_match(_json_loads(row["context_conditions"]), context or {})
        adjusted = (row["success_rate"] or 0.0) * match
        return {
            "id": row["id"],
            "steps": json.loads(row["steps"]),
            "success_rate": row["success_rate"],
            "adjusted_success_rate": adjusted,
            "avg_execution_time": row["avg_execution_time"],
            "context_match": match,
            "adaptations_count": len(_json_loads(row["adaptations"]) or []),
            "recommended": adjusted > 0.7
        }

    def get_all_procedures(self, limit: int = 100) -> List[Dict[str, Any]]:
        with _conn() as conn:
            rows = conn.execute("SELECT * FROM memory_procedures ORDER BY success_rate DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def find_applicable_procedures(self, context_description: str, min_success_rate: float = 0.6) -> List[Dict[str, Any]]:
        with _conn() as conn:
            rows = conn.execute("SELECT * FROM memory_procedures WHERE success_rate >= ? ORDER BY success_rate DESC", (min_success_rate,)).fetchall()
        return [dict(r) for r in rows[:3]]

    def get_memory_stats(self) -> Dict[str, Any]:
        with _conn() as conn:
            tot = conn.execute("SELECT COUNT(*) AS c FROM memory_procedures").fetchone()["c"]
            avg = conn.execute("SELECT AVG(success_rate) AS a FROM memory_procedures").fetchone()["a"] or 0.0
            high = conn.execute("SELECT COUNT(*) AS c FROM memory_procedures WHERE success_rate > 0.8").fetchone()["c"]
        return {
            "total_procedures": tot,
            "average_success_rate": avg,
            "high_success_procedures": high,
            "health_score": avg
        }

    def _analyze_initial_context(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {k: {"success_values": [v], "failure_values": [], "weight": 0.1} for k, v in ctx.items()}

    def _update_context_conditions(self, existing: Dict[str, Any], new_ctx: Dict[str, Any], success: bool) -> Dict[str, Any]:
        if not new_ctx:
            return existing
        out = dict(existing or {})
        for k, v in new_ctx.items():
            o = out.setdefault(k, {"success_values": [], "failure_values": [], "weight": 0.0})
            (o["success_values"] if success else o["failure_values"]).append(v)
            s_u = len(set(o["success_values"])) if o["success_values"] else 0
            f_u = len(set(o["failure_values"])) if o["failure_values"] else 0
            denom = max(1, s_u + f_u)
            o["weight"] = abs(s_u - f_u) / denom
        return out

    def _context_match(self, conditions: Dict[str, Any], ctx: Dict[str, Any]) -> float:
        if not conditions or not ctx:
            return 0.8
        total = 0.0
        match = 0.0
        for k, spec in conditions.items():
            w = float(spec.get("weight", 0.0))
            if w <= 0.0:
                continue
            total += w
            svals = spec.get("success_values", [])
            if k in ctx and ctx[k] in svals:
                match += w
            elif k in ctx and svals:
                # proste podobieństwo tekstowe
                cv = str(ctx[k]).lower()
                if any(str(sv).lower() in cv for sv in svals):
                    match += w * 0.5
        return match / total if total > 0 else 0.8

# -------------------- L4: Mental Models --------------------

class MentalModelManager:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        with _conn() as conn:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS memory_mental_models (
                id TEXT PRIMARY KEY,
                model_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                evidence_count INTEGER DEFAULT 0,
                model_data TEXT NOT NULL,
                related_facts TEXT,
                related_procedures TEXT,
                last_updated REAL,
                created_at REAL,
                validation_score REAL DEFAULT 0.0
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_models_type_subject ON memory_mental_models(model_type, subject);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_models_confidence ON memory_mental_models(confidence DESC);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_models_validation ON memory_mental_models(validation_score DESC);")

    def build_user_profile_model(self, user_id: str, semantic_facts: List[Dict], procedures: List[Dict], episodes: List[Dict]) -> str:
        prefs = self._extract_user_preferences(semantic_facts, episodes)
        behavior = self._analyze_behavioral_patterns(episodes)
        comm = self._analyze_communication_style(episodes)
        temporal = self._analyze_temporal_patterns(episodes)
        conf_factors = {
            "data_volume": min(1.0, len(episodes) / 50.0),
            "consistency": 0.7,
            "recency": self._data_recency(episodes)
        }
        overall = conf_factors["data_volume"]*0.4 + conf_factors["consistency"]*0.4 + conf_factors["recency"]*0.2
        data = {
            "user_id": user_id,
            "preferences": prefs,
            "behavioral_patterns": behavior,
            "communication_style": comm,
            "temporal_patterns": temporal,
            "confidence_factors": conf_factors,
            "last_analysis": _now()
        }
        return self._save_or_update_model("user_profile", user_id, overall, len(semantic_facts)+len(episodes), data,
                                          [f.get("id","") for f in semantic_facts], [p.get("id","") for p in procedures])

    def build_domain_knowledge_model(self, domain: str, facts: List[Dict], procedures: List[Dict]) -> str:
        # uproszczony, ale stabilny
        completeness = min(1.0, len(facts)/50.0)
        relationships_strength = 0.5
        conf = completeness*0.5 + relationships_strength*0.3 + min(1.0, len(facts)/20.0)*0.2
        data = {
            "domain": domain,
            "completeness_score": completeness,
            "last_analysis": _now()
        }
        return self._save_or_update_model("domain_knowledge", domain, conf, len(facts), data,
                                          [f.get("id","") for f in facts], [p.get("id","") for p in procedures])

    def predict_user_behavior(self, user_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        model = self._get_model("user_profile", user_id)
        if not model:
            return {"predicted_action": None, "predicted_intent": None, "confidence": 0.0}
        md = _json_loads(model["model_data"])
        # prosta reguła
        pred = {
            "predicted_action": "ask_followup",
            "predicted_intent": "clarify",
            "confidence": min(1.0, (model["confidence"] or 0.0) * 0.9),
            "factors": {"temporal_alignment": 0.5, "preference_alignment": 0.6}
        }
        return pred

    def get_comprehensive_user_insights(self, user_id: str) -> Dict[str, Any]:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT model_type, model_data, confidence, validation_score FROM memory_mental_models WHERE subject = ? OR model_data LIKE ?",
                (user_id, f'%\"user_id\": \"{user_id}\"%')
            ).fetchall()
        insights = {
            "user_id": user_id,
            "available_models": [],
            "personality_profile": {},
            "expertise_assessment": {},
            "behavioral_predictions": {},
            "interaction_preferences": {},
            "overall_confidence": 0.0
        }
        conf_sum = 0.0
        for r in rows:
            insights["available_models"].append({"type": r["model_type"], "confidence": r["confidence"], "validation_score": r["validation_score"]})
            md = _json_loads(r["model_data"])
            if r["model_type"] == "user_profile":
                insights["personality_profile"] = md.get("preferences", {})
                insights["behavioral_predictions"] = md.get("behavioral_patterns", {})
                insights["interaction_preferences"] = md.get("communication_style", {})
            elif r["model_type"] == "domain_knowledge":
                d = md.get("domain", "unknown")
                insights["expertise_assessment"][d] = {"completeness": md.get("completeness_score", 0.0), "confidence": r["confidence"]}
            conf_sum += r["confidence"] or 0.0
        if rows:
            insights["overall_confidence"] = conf_sum / len(rows)
        return insights

    # helpers
    def _save_or_update_model(self, model_type: str, subject: str, confidence: float, evidence_count: int,
                              model_data: Dict, related_facts: List[str], related_procedures: List[str]) -> str:
        with _conn() as conn:
            row = conn.execute("SELECT id FROM memory_mental_models WHERE model_type = ? AND subject = ?", (model_type, subject)).fetchone()
            now = _now()
            if row:
                mid = row["id"]
                conn.execute(
                    "UPDATE memory_mental_models SET confidence=?, evidence_count=?, model_data=?, related_facts=?, related_procedures=?, last_updated=? WHERE id=?",
                    (confidence, evidence_count, json.dumps(model_data, ensure_ascii=False), json.dumps(related_facts), json.dumps(related_procedures), now, mid)
                )
                return mid
            else:
                mid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO memory_mental_models (id, model_type, subject, confidence, evidence_count, model_data, related_facts, related_procedures, last_updated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (mid, model_type, subject, confidence, evidence_count, json.dumps(model_data, ensure_ascii=False),
                     json.dumps(related_facts), json.dumps(related_procedures), now, now)
                )
                return mid

    def _get_model(self, model_type: str, subject: str) -> Optional[Dict[str, Any]]:
        with _conn() as conn:
            row = conn.execute("SELECT * FROM memory_mental_models WHERE model_type = ? AND subject = ?", (model_type, subject)).fetchone()
        return dict(row) if row else None

    def _extract_user_preferences(self, facts: List[Dict], episodes: List[Dict]) -> Dict[str, Any]:
        topic_freq: Dict[str, float] = {}
        for f in facts:
            for t in (f.get("tags") or "").split(","):
                t = t.strip()
                if t and not t.startswith("user:") and t not in {"semantic", "consolidated"}:
                    topic_freq[t] = topic_freq.get(t, 0.0) + float(f.get("conf", 1.0))
        topics = [t for t, _ in sorted(topic_freq.items(), key=lambda kv: kv[1], reverse=True)[:10]]
        return {
            "topics_of_interest": topics,
            "preferred_interaction_style": "unknown",
            "complexity_tolerance": 0.5,
            "response_time_preference": "normal",
            "detail_level_preference": "medium"
        }

    def _analyze_behavioral_patterns(self, episodes: List[Dict]) -> Dict[str, Any]:
        qtypes = {"what":0, "how":0, "why":0, "when":0, "where":0, "who":0}
        for ep in episodes:
            s = (ep.get("summary") or "").lower()
            for k in qtypes:
                if k in s:
                    qtypes[k] += 1
        if sum(qtypes.values()):
            dom = max(qtypes, key=qtypes.get)
        else:
            dom = "what"
        return {"dominant_question_type": dom, "counts": qtypes}

    def _analyze_communication_style(self, episodes: List[Dict]) -> Dict[str, Any]:
        return {"formality": "neutral", "verbosity": "medium"}

    def _analyze_temporal_patterns(self, episodes: List[Dict]) -> Dict[str, Any]:
        hours = [time.localtime(ep.get("timestamp", _now())).tm_hour for ep in episodes]
        cnt = Counter(hours)
        return {"peak_hours": [h for h, _ in cnt.most_common(3)], "distribution": dict(cnt)}

    def _data_recency(self, episodes: List[Dict]) -> float:
        if not episodes:
            return 0.0
        cutoff = _now() - 7*24*3600
        recent = sum(1 for ep in episodes if ep.get("timestamp", 0) > cutoff)
        return min(1.0, recent / len(episodes))

# -------------------- System koordynujący --------------------

class HierarchicalMemorySystem:
    def __init__(self):
        self.episodic = EpisodicMemoryManager()
        self.semantic = SemanticMemoryManager()
        self.procedural = ProceduralMemoryManager()
        self.mental_models = MentalModelManager()
        self.consolidation_config = {
            "episodic_to_semantic_threshold": EPISODIC_TO_SEMANTIC_THRESHOLD,
            "semantic_consolidation_interval": SEMANTIC_CONSOLIDATION_INTERVAL,
            "procedural_adaptation_rate": PROCEDURAL_ADAPTATION_RATE,
            "mental_model_update_frequency": MENTAL_MODEL_UPDATE_FREQUENCY,
            "cross_level_correlation_threshold": 0.6
        }
        log_info("HierarchicalMemorySystem init OK", "HIER_MEM")

    def process_new_memory(self, content: str, context: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = user_id or "default_user"
        entities = self._extract_entities(content)
        emotions = self._detect_emotions(content)
        metadata = {"context": context, "entities": entities, "emotions": emotions, "intent": context.get("intent", "unknown")}
        eid = self.episodic.record_episode(uid, "conversation_turn", content, [], metadata)

        semantic_updates = []
        for insight in self._extract_semantic_insights(content, entities):
            fid = ltm_add(insight["content"], insight["tags"], conf=insight["confidence"])
            semantic_updates.append({"fact_id": fid, "content": insight["content"], "confidence": insight["confidence"]})

        proc_updates = []
        for pat in self._identify_procedural_patterns(content, context):
            pid = self.procedural.learn_or_update_procedure(pat["name"], pat["steps"], pat.get("execution_time", 0.0), pat["outcome"] == "success", pat["context"])
            if pid:
                proc_updates.append({"procedure_id": pid, "pattern": pat["name"], "success": pat["outcome"] == "success"})

        mm_updates = []
        if self._should_trigger_consolidation():
            cons = self.consolidate_memories(uid)
            mm_updates = cons.get("mental_model_updates", [])

        return {
            "episode_id": eid,
            "semantic_updates": semantic_updates,
            "procedural_updates": proc_updates,
            "mental_model_updates": mm_updates,
            "consolidation_triggered": bool(mm_updates),
        }

    def consolidate_memories(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        recent = self.episodic.get_recent_episodes(limit=SEMANTIC_CONSOLIDATION_INTERVAL)
        facts = self.semantic.get_all_facts(limit=1000)
        procs = self.procedural.get_all_procedures(limit=100)

        # L1->L2
        fact_id = self.semantic.consolidate_fact_from_episodes(recent, user_id or "default_user")
        facts_created = 1 if fact_id else 0

        # L2->L3 (prosty heurystyczny licznik)
        procedures_created = max(0, len(facts) // 10)

        mental_model_updates = []
        if user_id:
            uid = user_id
            up_id = self.mental_models.build_user_profile_model(uid, facts, procs, recent)
            mental_model_updates.append({"type": "user_profile", "user_id": uid, "model_id": up_id})

        log_info(f"Konsolidacja: facts+={facts_created}, proc+={procedures_created}", "HIER_MEM")
        return {
            "episodes_consolidated": len(recent),
            "semantic_facts_generated": facts_created,
            "procedures_learned": procedures_created,
            "mental_model_updates": mental_model_updates,
            "consolidation_timestamp": _now()
        }

    def retrieve_comprehensive_context(self, query: str, user_id: Optional[str] = None, max_context_size: int = MAX_CONTEXT_SUMMARY_LEN) -> Dict[str, Any]:
        uid = user_id or "default_user"
        l1 = self.episodic.find_related_episodes(query, uid, limit=10)[:5]
        l2 = self.semantic.search_facts(query, limit=15, min_confidence=0.4)
        l3 = self.procedural.find_applicable_procedures(query, min_success_rate=0.6)[:3]
        user_insights = self.mental_models.get_comprehensive_user_insights(uid) if user_id else {}

        factors = []
        if l1:
            factors.append(sum(ep.get("similarity_score", 0.0) for ep in l1) / len(l1))
        if l2:
            factors.append(sum(f.get("conf", 0.0) for f in l2) / len(l2))
        if l3:
            factors.append(sum(p.get("success_rate", 0.0) for p in l3) / len(l3))
        total_conf = sum(factors) / len(factors) if factors else 0.0

        summary = f"Context: {len(l1)} episodes, {len(l2)} facts, {len(l3)} procedures (confidence: {total_conf:.2f})"
        return {
            "query": query,
            "user_id": user_id,
            "episodic_memories": l1,
            "semantic_facts": l2,
            "relevant_procedures": l3,
            "user_insights": user_insights,
            "predictions": self.mental_models.predict_user_behavior(uid, {"query": query, "timestamp": _now()}) if user_id else {},
            "context_summary": summary,
            "total_confidence": total_conf
        }

    def analyze_memory_health(self) -> Dict[str, Any]:
        l1 = self.episodic.get_memory_stats()
        l2 = self.semantic.get_memory_stats()
        l3 = self.procedural.get_memory_stats()
        with _conn() as conn:
            l4c = conn.execute("SELECT COUNT(*) AS c FROM memory_mental_models").fetchone()["c"]
            l4avg = conn.execute("SELECT COALESCE(AVG(confidence),0.0) AS a FROM memory_mental_models").fetchone()["a"]
        health = {
            "L1_episodic": l1,
            "L2_semantic": l2,
            "L3_procedural": l3,
            "L4_mental_models": {"total_models": l4c, "average_confidence": l4avg}
        }
        overall = sum([
            l1.get("health_score", 0.5),
            l2.get("health_score", 0.5),
            l3.get("health_score", 0.5),
            min(1.0, l4avg),
            0.8
        ]) / 5.0
        recs = []
        if overall < 0.7:
            recs.append("Increase consolidation frequency")
        if l4c < 5:
            recs.append("Collect more user interactions to improve L4")
        return {
            "overall_health_score": overall,
            "level_statistics": health,
            "consolidation_status": {
                "last_consolidation": "unknown",
                "episodes_pending_consolidation": len(self.episodic.get_recent_episodes(limit=SEMANTIC_CONSOLIDATION_INTERVAL)),
                "consolidation_efficiency": 0.8
            },
            "performance_metrics": {"memory_utilization": 0.75, "retrieval_speed": "fast", "consolidation_rate": "optimal"},
            "recommendations": recs
        }

    # --- helpers ---
    def _extract_entities(self, content: str) -> List[str]:
        words = (content or "").split()
        return [w for w in words if w[:1].isupper() and len(w) > 2][:10]

    def _detect_emotions(self, content: str) -> Dict[str, float]:
        pos = ["good","great","excellent","amazing","love","like","happy"]
        neg = ["bad","terrible","awful","hate","dislike","sad","angry"]
        low = (content or "").lower()
        p = sum(1 for w in pos if w in low)
        n = sum(1 for w in neg if w in low)
        t = p + n
        if t == 0:
            return {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
        return {"positive": p/t, "negative": n/t, "neutral": max(0.0, 1.0 - p/t - n/t)}

    def _extract_semantic_insights(self, text: str, entities: List[str]) -> List[Dict[str, Any]]:
        out = []
        if any(k in (text or "") for k in [" is ", " are ", " to ", " jest "]):
            out.append({
                "content": f"Fact from episode: {(text or '')[:100]}...",
                "tags": ",".join((entities or []) + ["episode_derived","factual"]),
                "confidence": 0.7
            })
        return out

    def _identify_procedural_patterns(self, text: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        keys = ["how to", "steps to", "process", "method", "way to"]
        for k in keys:
            if k in (text or "").lower():
                return [{
                    "name": f"procedure_from_{k.replace(' ','_')}",
                    "steps": [(text or "").strip()][:1],
                    "context": ctx,
                    "outcome": "success",
                    "execution_time": 0.0
                }]
        return []

    def _should_trigger_consolidation(self) -> bool:
        # trywialne kryterium
        return len(self.episodic.get_recent_episodes(limit=SEMANTIC_CONSOLIDATION_INTERVAL)) >= SEMANTIC_CONSOLIDATION_INTERVAL

# -------------------- API globalne --------------------

_singleton: Optional[HierarchicalMemorySystem] = None

def get_hierarchical_memory_system() -> HierarchicalMemorySystem:
    global _singleton
    if _singleton is None:
        _singleton = HierarchicalMemorySystem()
    return _singleton

# Back-compat krótkie aliasy
def enhance_memory_with_hierarchy(content: str, context: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    return get_hierarchical_memory_system().process_new_memory(content, context, user_id)

def get_hierarchical_context(query: str, user_id: Optional[str] = None, max_size: int = MAX_CONTEXT_SUMMARY_LEN) -> Dict[str, Any]:
    return get_hierarchical_memory_system().retrieve_comprehensive_context(query, user_id, max_size)

def run_memory_consolidation(user_id: Optional[str] = None) -> Dict[str, Any]:
    return get_hierarchical_memory_system().consolidate_memories(user_id)

def get_memory_health_report() -> Dict[str, Any]:
    return get_hierarchical_memory_system().analyze_memory_health()

def predict_user_next_action(user_id: str, current_context: Dict[str, Any]) -> Dict[str, Any]:
    return get_hierarchical_memory_system().mental_models.predict_user_behavior(user_id, current_context)

def get_user_comprehensive_profile(user_id: str) -> Dict[str, Any]:
    return get_hierarchical_memory_system().mental_models.get_comprehensive_user_insights(user_id)

# Alias dla kompatybilności wstecznej
hierarchical_memory_manager = get_hierarchical_memory_system()
