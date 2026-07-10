# AI-Hub — Flow Diagrams Evidence (Raw Code Citations)

> Companion do `docs/FLOW_DIAGRAMS.md` v2.
> Każdy element z diagramów ma tu surowy cytat z kodu: plik, linie, fragment.
> Audit: 2025-01

---

## LEGENDA

- ✅ = Dowód istnieje, element aktywny w runtime
- ⚠️ = Dowód istnieje, ale element nie jest wołany automatycznie / częściowo aktywny
- 🔴 = Kod istnieje, ale nigdy nie jest wykonywany w runtime
- 📁 = Ścieżka pliku (względem `/root/ai-hub/`)

---

## 1. Startup chain

### start.sh → uvicorn

📁 `start.sh` linie 15-18:

```bash
APP_IMPORT="${APP_IMPORT:-aihub.main:app}"
...
exec uvicorn "$APP_IMPORT" --host "${HOST}" --port "${PORT}" ...
```

### main.py startup event

📁 `aihub/main.py` linie 90-99:

```python
@app.on_event("startup")
async def _startup():
    init_db()
    start_worker_once()
```

### start_worker_once

📁 `aihub/agent_worker.py` linie 162-178:

```python
_worker_started = False

def start_worker_once():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    t = threading.Thread(target=_run_loop, daemon=True, name="agent-worker")
    t.start()
    logger.info("agent-worker thread started")
```

---

## 2. POST /memory/add — pipeline zapisu

### Endpoint definition

📁 `aihub/main.py` linie 196-210:

```python
@app.post("/memory/add")
async def memory_add(req: Request):
    body = await req.json()
    user_id = body["user_id"]
    user_msg = body.get("user_msg", "")
    assistant_msg = body.get("assistant_msg", "")
    intent = body.get("intent", "")
    meta = body.get("meta", {})
    psyche_engine.ensure_user(user_id)
    psyche_engine.evolve(user_id, user_msg, "user")
    psyche_engine.evolve(user_id, assistant_msg, "assistant")
    result = memory_engine.process_turn(user_id, user_msg, assistant_msg, intent, meta)
    return result
```

### ensure_user

📁 `aihub/psyche_engine.py` linie 71-82:

```python
def ensure_user(user_id: str):
    row = get_psyche(user_id)
    if row:
        return row
    baseline = _baseline()
    upsert_psyche(user_id, baseline)
    append_event(user_id, "psyche.init", baseline)
    return baseline
```

### evolve — psyche update

📁 `aihub/psyche_engine.py` linie 99-154:

```python
def evolve(user_id: str, text: str, role: str = "user"):
    st = ensure_user(user_id)
    s, conf = analyze_sentiment(text)
    role_w = 1.0 if role == "user" else 0.35
    ...
    mood = st["mood"] + role_w * 0.18 * s * conf
    mood += 0.02 * (0.55 - mood)  # drift
    mood = max(0.0, min(1.0, mood))
    ...
    energy = st["energy"] + role_w * 0.06 * s * conf - 0.01 * (words / 80)
    focus = st["focus"] + role_w * 0.05 * conf - 0.02 * (words / 200)
    ...
    # trait learning
    if neg > pos and neg >= 2:  # harsh
        traits["directness"] = min(1, traits.get("directness", 0.5) + 0.03 * conf)
        traits["patience"] = max(0, traits.get("patience", 0.5) - 0.03 * conf)
        traits["swearing"] = min(1, traits.get("swearing", 0.0) + 0.02 * conf)
        traits["sarcasm"] = min(1, traits.get("sarcasm", 0.0) + 0.02 * conf)
        style = "ziomek"
    elif pos > neg and pos >= 2:  # friendly
        traits["agreeableness"] = min(1, traits.get("agreeableness", 0.5) + 0.02 * conf)
        traits["patience"] = min(1, traits.get("patience", 0.5) + 0.02 * conf)
        traits["sarcasm"] = max(0, traits.get("sarcasm", 0.0) - 0.01 * conf)
    ...
    temperature = 0.55 + 0.25 * (mood - 0.5)
    temperature = max(0.25, min(0.95, temperature))
    ...
    upsert_psyche(user_id, new_state)
    append_event(user_id, "psyche.update", delta)
```

### analyze_sentiment

📁 `aihub/psyche_engine.py` linie 86-97:

```python
def analyze_sentiment(text: str) -> tuple[float, float]:
    words = text.lower().split()
    pos = sum(1 for w in words if w in _POS)
    neg = sum(1 for w in words if w in _NEG)
    intens = sum(1 for w in words if w in _INTENSIFIERS)
    total = pos + neg
    sentiment = (pos - neg) / max(3, total)
    sentiment = max(-1.0, min(1.0, sentiment))
    confidence = 0.45 + 0.12 * total + 0.05 * intens
    confidence = max(0.0, min(0.95, confidence))
    return sentiment, confidence
```

### \_POS, \_NEG, \_INTENSIFIERS

📁 `aihub/psyche_engine.py` linie 9-36:

```python
_POS = {
    "dobrze", "super", "kocham", "świetnie", "dzięki", "dziękuję",
    "fajnie", "ok", "git", "spoko", "zajebiste", "bomba", "extra",
    "cudownie", "rewelacja", "pięknie", "idealnie", "perfekcyjnie",
}
_NEG = {
    "źle", "problem", "kurwa", "chuj", "gówno", "pierdolę",
    "beznadziejne", "nie działa", "zepsute", "bug", "błąd", "fail",
    "tragedia", "dramat", "katastrofa", "smutek",
}
_INTENSIFIERS = {"bardzo", "mega", "strasznie", "cholernie", "piekielnie", "kurwa"}
```

### process_turn — memory engine

📁 `aihub/memory_engine.py` linie 127-166:

```python
def process_turn(user_id, user_msg, assistant_msg, intent="", meta=None):
    remember_turn(user_msg, assistant_msg)  # vector hook → FAISS

    stm_ids = []
    if user_msg:
        stm_ids.append(add_stm(user_id, "user", user_msg))
    if assistant_msg:
        stm_ids.append(add_stm(user_id, "assistant", assistant_msg))

    summary = f"U:{user_msg} || A:{assistant_msg}"
    ep_id = add_episode(user_id, summary, intent=intent, meta=meta)

    fact_ids = []
    _FACT_KW = re.compile(r"lubię|nie lubię|preferuję|zawsze|nigdy|ważne|zakaz|nakaz", re.I)
    if user_msg and _FACT_KW.search(user_msg):
        fid = add_fact(user_id, user_msg, tags=["user", "preference", intent or "fact"])
        if fid:
            fact_ids.append(fid)

    _enforce_caps(user_id)
    append_event(user_id, "memory.process_turn", {"stm": len(stm_ids), "ep": ep_id})
    return {"stm_ids": stm_ids, "episode_id": ep_id, "fact_ids": fact_ids, "ts": _now()}
```

### remember_turn (vector_hook)

📁 `aihub/vector_hook.py` linie 1-10:

```python
from aihub.vector_engine import add_memory

def remember_turn(user_msg: str, assistant_msg: str):
    if user_msg:
        add_memory(user_msg)
    if assistant_msg:
        add_memory(assistant_msg)
```

### add_memory (vector_engine, FAISS)

📁 `aihub/vector_engine.py` linie 133-164:

```python
def add_memory(text: str):
    _ensure_model()  # lazy-load SentenceTransformer("all-MiniLM-L6-v2")
    global _index, _meta
    if _index is None:
        _index = faiss.IndexFlatL2(_dim)
        _meta = []
    vec = _model.encode([text])
    _index.add(vec)
    _meta.append({"text": text, "ts": time.time()})
    _save()
```

### add_stm

📁 `aihub/memory_engine.py` linie 44-55:

```python
def add_stm(user_id, role, content):
    msg_id = insert_stm_message(user_id, role, content)
    prune_stm(user_id, keep=STM_MAX_MESSAGES)
    return msg_id
```

### add_episode

📁 `aihub/memory_engine.py` linie 75-90:

```python
def add_episode(user_id, summary, intent="", meta=None):
    node_id = _id_for(summary, user_id, "L1")
    imp = _importance_from_text(summary)
    conf = _confidence_from_text(summary)
    tags = ["episode", intent] if intent else ["episode"]
    upsert_node(user_id, "L1", node_id, summary, tags=tags, importance=imp, confidence=conf, meta=meta or {})
    return node_id
```

### add_fact

📁 `aihub/memory_engine.py` linie 93-110:

```python
def add_fact(user_id, text, tags=None):
    node_id = _id_for(text, user_id, "L2")
    imp = _importance_from_text(text)
    conf = _confidence_from_text(text)
    upsert_node(user_id, "L2", node_id, text, tags=tags or [], importance=imp, confidence=conf)
    return node_id
```

### \_enforce_caps

📁 `aihub/memory_engine.py` linie 113-125:

```python
def _enforce_caps(user_id):
    # L1
    rows = list_recent_nodes(user_id, "L1", limit=EPISODES_MAX_PER_USER + 1)
    if len(rows) > EPISODES_MAX_PER_USER:
        for r in rows[EPISODES_MAX_PER_USER:]:
            soft_delete_node(r["id"])
    # L2
    rows = list_recent_nodes(user_id, "L2", limit=LTM_MAX_FACTS_PER_USER + 1)
    if len(rows) > LTM_MAX_FACTS_PER_USER:
        for r in rows[LTM_MAX_FACTS_PER_USER:]:
            soft_delete_node(r["id"])
```

---

## 3. POST /memory/search — pipeline odczytu

### Endpoint

📁 `aihub/main.py` linie 214-250:

```python
@app.post("/memory/search")
async def memory_search(req: Request):
    body = await req.json()
    user_id = body["user_id"]
    query = body.get("query", "")
    limit = body.get("limit", 10)
    ctx = memory_engine.retrieve_context(user_id, query, limit)
    return ctx
```

### retrieve_context

📁 `aihub/memory_engine.py` linie 193-260:

```python
def retrieve_context(user_id, query, limit=10):
    stm = get_stm(user_id, min(20, STM_MAX_MESSAGES))

    l1 = search_nodes_fts(user_id, "L1", query, limit * 20)
    l2 = search_nodes_fts(user_id, "L2", query, limit * 40)

    l1_ranked = _vector_rerank(query, l1, topk=limit)
    l2_ranked = _vector_rerank(query, l2, topk=limit)

    def _blend(items):
        result = []
        for content, cosine, node in items:
            imp = node.get("importance", 0.5)
            conf = node.get("confidence", 0.5)
            score = 0.72 * cosine + 0.18 * imp + 0.10 * conf
            result.append({**node, "score": round(score, 4)})
        result.sort(key=lambda x: x["score"], reverse=True)
        return result[:limit]

    episodic = _blend(l1_ranked)
    semantic = _blend(l2_ranked)
    ...
    append_event(user_id, "memory.retrieve", {"query": query, "results": total})
    return {"stm": stm, "episodic": episodic, "semantic": semantic, "total": total}
```

### \_vector_rerank (TF-IDF, NIE FAISS)

📁 `aihub/memory_engine.py` linie 172-190:

```python
def _vector_rerank(query, nodes, topk=10):
    if not nodes:
        return []
    docs = [n["content"] for n in nodes]
    tokenized = [tokenize(d) for d in docs]
    q_tok = tokenize(query)
    df = build_df(tokenized)
    df = prune_vocab(df, len(tokenized))
    n = len(tokenized)
    q_vec = tfidf_vector(q_tok, df, n)
    results = topk_cosine(q_vec, [(tfidf_vector(t, df, n), None) for t in tokenized], topk)
    return [(docs[i], score, nodes[i]) for score, i, _ in results]
```

### vector_index.py functions (TF-IDF)

📁 `aihub/vector_index.py` linie 13-95:

```python
def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())

def build_df(docs: list[list[str]]) -> dict[str, int]:
    ...

def prune_vocab(df, n_docs, min_df=1, max_df_ratio=0.95, max_vocab=8000):
    ...

def tfidf_vector(tokens, df, n_docs):
    # sublinear TF: 1 + log(tf), IDF: log(1 + n/df), L2 norm
    ...

def cosine_sparse(a, b):
    ...

def topk_cosine(query_vec, doc_vecs, k=10):
    ...
```

### search_nodes_fts (FTS5 MATCH z LIKE fallback)

📁 `aihub/db.py` linie 353-396:

```python
def search_nodes_fts(user_id, layer, query, limit=100):
    ...
    try:
        rows = cur.execute(
            "SELECT node_id FROM memory_fts WHERE memory_fts MATCH ? AND user_id = ? AND layer = ? LIMIT ?",
            (f"content:{query}", user_id, layer, limit),
        ).fetchall()
    except Exception:
        # fallback to LIKE
        rows = cur.execute(
            "SELECT id as node_id FROM memory_nodes WHERE user_id=? AND layer=? AND deleted=0 AND content LIKE ? LIMIT ?",
            (user_id, layer, f"%{query}%", limit),
        ).fetchall()
    ...
```

### DOWÓD: vector_engine.search() AKTYWNY (dense_hits boost)

> **KOREKTA (2026-03-06):** Wcześniejszy audit twierdził że vector_engine.search() nigdy nie jest wołany. To była **nieprawda** — został podpięty w wireup sprint.

📁 `aihub/vector_engine.py` linie 166-190 — search() exists:

```python
def search(query: str, k: int = 5) -> list[dict]:
    _ensure_model()
    if _index is None or _index.ntotal == 0:
        return []
    vec = _model.encode([query])
    D, I = _index.search(vec, min(k, _index.ntotal))
    ...
```

📁 `aihub/memory_engine.py` linie 333-345 — **wołany w retrieve_context() jako dense_hits boost**:

```python
    # Vector dense boost: optional FAISS semantic search to complement FTS
    dense_hits: List[Dict[str, Any]] = []
    try:
        from aihub.vector_engine import search as vector_search
        vr = vector_search(query, k=min(limit, 10))
        if vr.get("ok") and vr.get("results"):
            for r in vr["results"]:
                if r.get("similarity", 0) > 0.3:
                    dense_hits.append({"text": r["text"], "similarity": r["similarity"]})
    except Exception:
        logger.debug("retrieve_context: vector dense boost unavailable", exc_info=True)
```

**Status:** ✅ AKTYWNY — opcjonalny (try/except), nie blokuje pipeline jeśli FAISS niedostępny. Próg similarity >0.3.

---

## 4. POST /psyche/reflect

### Endpoint

📁 `aihub/main.py` linie 172-190:

```python
@app.post("/psyche/reflect")
async def psyche_reflect(req: Request):
    body = await req.json()
    user_id = body["user_id"]
    query = body.get("query", "co ostatnio")
    limit = body.get("limit", 20)
    psyche_engine.ensure_user(user_id)
    ctx = memory_engine.retrieve_context(user_id, query, limit=min(limit, 20))
    result = psyche_engine.reflect(user_id, ctx["stm"])
    return {**result, "memory_context": ctx}
```

### reflect()

📁 `aihub/psyche_engine.py` linie 157-193:

```python
def reflect(user_id: str, stm_messages: list) -> dict:
    st = ensure_user(user_id)
    # frequency count from last 20 messages
    freq = {}
    for msg in stm_messages[-20:]:
        for word in msg.get("content", "").lower().split():
            if len(word) >= 4:
                freq[word] = freq.get(word, 0) + 1
    topics = sorted(((w, c) for w, c in freq.items() if c >= 2), key=lambda x: -x[1])[:12]
    ...
    mood_desc = "spoko" if st["mood"] > 0.6 else ("wkurwiony" if st["mood"] < 0.35 else "neutralny")
    energy_desc = "wysoka" if st["energy"] > 0.65 else ("niska" if st["energy"] < 0.35 else "średnia")
    ...
    append_event(user_id, "psyche.reflect", {"topics_count": len(topics)})
    return {"reflection": ..., "topics": topics, "state": st, "ts": _now()}
```

---

## 5. POST /cognitive/decide

### Endpoint

📁 `aihub/main.py` linie 365-410:

```python
@app.post("/cognitive/decide")
async def cognitive_decide(req: Request):
    body = await req.json()
    user_id = body["user_id"]
    message = body.get("message", "")
    context = body.get("context", {})
    tools = body.get("tools", [])
    result = _cognitive.decide(user_id, message, context, tools)
    return result
```

### CognitiveController.**init**

📁 `aihub/cognitive_controller.py` linie 71-83:

```python
class CognitiveController:
    def __init__(self):
        self.attention = AttentionController()
        self.conflict_detector = ConflictDetector()
        self.knowledge_graph = KnowledgeGraph()
        self.learning_engine = LearningEngine()  # ← instancja, ale NIGDY nie wołana
        self._resource_counters = {}
        ...
```

### decide() method

📁 `aihub/cognitive_controller.py` linie 131-232:

```python
def decide(self, user_id, message, context=None, tools=None):
    psyche_state = ensure_user(user_id)
    ...
    memory_pressure = self._estimate_memory_pressure(user_id)
    predictions = predict_next_action(user_id, decision_context)
    intent = self._extract_intent(message)
    ...
    if intent in ("sprawdź", "wyszukaj", "research", "find"):
        result = self._decide_research(...)
    elif intent in ("stwórz", "napisz", "execute", "make"):
        result = self._decide_action(...)
    elif intent in ("nauczę", "learn", "teach", "explain"):
        result = self._decide_learn(...)
    else:
        result = self._decide_query(...)
    ...
    conflicts = self._detect_conflicts(result, user_id)
    if conflicts and conflicts.get("severity", 0) >= 0.8:
        return DecisionResult(action_type="skip", ...)
    ...
    append_event(user_id, "cognitive.decision", {...})
    return result
```

### \_estimate_memory_pressure → meta_memory.check_stale

📁 `aihub/cognitive_controller.py` linie 409-420:

```python
def _estimate_memory_pressure(self, user_id):
    stale = check_stale(user_id, days=30)
    return min(1.0, len(stale) / 500)
```

### predict_next_action

📁 `aihub/prediction_engine.py` linie 47-131:

```python
def predict_next_action(user_id: str, context: dict) -> list[dict]:
    predictions = []
    focus = context.get("focus", 0.5)
    urgency = context.get("urgency", 0.5)
    energy = context.get("energy", 0.5)
    memory_pressure = context.get("memory_pressure", 0.0)
    intent = context.get("intent", "")
    ...
    # pattern-based predictions based on context signals
    ...
    return sorted(predictions, key=lambda x: -x["confidence"])[:5]
```

### \_check_resources (TTL 300s)

📁 `aihub/cognitive_controller.py` linie 99-118:

```python
def _check_resources(self, resource_type, limit):
    now = time.time()
    key = resource_type
    if key not in self._resource_counters:
        self._resource_counters[key] = {"count": 0, "reset_at": now + 300}
    counter = self._resource_counters[key]
    if now > counter["reset_at"]:
        counter["count"] = 0
        counter["reset_at"] = now + 300
    if counter["count"] >= limit:
        return False
    counter["count"] += 1
    return True
```

### ConflictDetector.check_conflict

📁 `aihub/conflict_detector.py` linie 70-118:

```python
def check_conflict(self, decision, user_id, context=None):
    conflicts = []
    # 1. Security check - forbidden actions
    ...
    # 2. Logical consistency
    ...
    # 3. Resource constraints
    ...
    severity = max((c["severity"] for c in conflicts), default=0)
    return {"has_conflict": bool(conflicts), "conflicts": conflicts, "severity": severity}
```

### DOWÓD: LearningEngine NIGDY NIE WOŁANA

📁 `aihub/cognitive_controller.py` — `self.learning_engine` jest instancjowane w `__init__` (L82), ale szukając `self.learning_engine.` w pliku → **0 wywołań** jakiejkolwiek metody LearningEngine w decide() ani w żadnej innej metodzie CognitiveController.

---

## 6. Agent Worker (background loop, real)

### \_run_loop

📁 `aihub/agent_worker.py` linie 30-157:

```python
def _run_loop():
    ensure_schema()
    interval = AGENT_INTERVAL_S  # 3.5s
    while True:
        try:
            for user_id in _get_all_users():
                state = get_agent_state(user_id)
                if not state or not state.get("enabled"):
                    continue
                try:
                    asyncio.run(agent_tick(user_id, max_stm=200, max_tasks=6))
                except Exception as e:
                    _retries[user_id] = _retries.get(user_id, 0) + 1
                    if _retries[user_id] <= MAX_RETRIES:
                        time.sleep(RETRY_DELAY * _retries[user_id])
                    ...
        except Exception:
            ...
        time.sleep(interval)
```

### agent_tick (THE REAL background worker)

📁 `aihub/agent_engine.py` linie 296-400:

```python
async def agent_tick(user_id: str, max_stm: int = 200, max_tasks: int = 6):
    new_msgs = _pull_new_stm(user_id, since_ts=_cursors.get(user_id))
    if not new_msgs:
        return {"status": "ok", "processed": 0}

    batch_summary_parts = []
    for msg in new_msgs:
        evolve(user_id, msg["content"], msg["role"])
        if msg["role"] == "user":
            facts = extract_facts_from_text(msg["content"])
            for f in facts:
                add_fact(user_id, f["text"], tags=f.get("tags", []))
            tasks = plan_from_text(msg["content"])
            for t in tasks:
                enqueue_task(user_id, t["type"], t["params"])
        batch_summary_parts.append(f"{msg['role']}: {msg['content'][:100]}")

    if batch_summary_parts:
        add_episode(user_id, " | ".join(batch_summary_parts))

    _cursors[user_id] = new_msgs[-1]["ts"]

    # execute queued tasks
    executed = 0
    while executed < max_tasks:
        task = claim_next_task(user_id)
        if not task:
            break
        result = await execute_task(user_id, task)
        complete_task(task["id"], result)
        executed += 1

    append_event(user_id, "agent.tick", {"processed": len(new_msgs), "executed": executed})
    return {"status": "ok", "processed": len(new_msgs), "executed": executed}
```

### extract_facts_from_text (agent_engine, keyword-based)

📁 `aihub/agent_engine.py` linie 55-99:

```python
def extract_facts_from_text(text: str) -> list[dict]:
    facts = []
    lower = text.lower()
    # heuristic keyword-based extraction
    if re.search(r"lubię|uwielbiam|preferuję", lower):
        facts.append({"text": text, "tags": ["user", "preference"], ...})
    if re.search(r"nazywam się|jestem|mam na imię", lower):
        facts.append({"text": text, "tags": ["user", "identity"], ...})
    if re.search(r"pracuję|robię w|zajmuję się", lower):
        facts.append({"text": text, "tags": ["user", "bio"], ...})
    if re.search(r"hasło|token|secret|klucz", lower):
        facts.append({"text": text, "tags": ["safety", "security_mention"], ...})
    return facts
```

### plan_from_text

📁 `aihub/agent_engine.py` linie 102-149:

```python
def plan_from_text(text: str) -> list[dict]:
    tasks = []
    lower = text.lower()
    if re.search(r"wyszukaj|znajdź|sprawdź|fetch|pobierz", lower):
        ...  # type: "web.fetch"
    if re.search(r"zapisz|napisz|stwórz plik|utwórz", lower):
        ...  # type: "fs.write"
    if re.search(r"snapshot|backup|kopia", lower):
        ...  # type: "system.snapshot"
    return tasks
```

### execute_task

📁 `aihub/agent_engine.py` linie 167-190:

```python
async def execute_task(user_id: str, task: dict):
    task_type = task.get("type", "")
    params = task.get("params", {})

    if task_type == "web.fetch":
        url = params.get("url", "")
        result = await fetch_url(url)
        add_fact(user_id, f"Fetched: {url} → {result[:200]}", tags=["web", "fetch"])
        return {"ok": True, "type": "web.fetch"}
    elif task_type == "fs.write":
        path = params.get("path", "")
        content = params.get("content", "")
        write_file(path, content)
        add_fact(user_id, f"Written: {path}", tags=["fs", "write"])
        return {"ok": True, "type": "fs.write"}
    elif task_type == "system.snapshot":
        snap = create_snapshot(user_id)
        add_fact(user_id, f"Snapshot: {snap['id']}", tags=["system", "snapshot"])
        return {"ok": True, "type": "system.snapshot"}
    ...
```

---

## 7. Agent Loop (manual, via API only)

### Endpoint

📁 `aihub/agent_api.py` linie 88-93:

```python
@router.post("/agent/loop")
async def agent_loop_endpoint(req: Request):
    body = await req.json()
    ...
    result = await run_loop(text=body.get("text",""), user_id=uid, max_iters=body.get("max_iters", 3))
    return result
```

### run_loop

📁 `aihub/agent_loop.py` linie 272-296:

```python
async def run_loop(text: str, user_id: str, max_iters: int = 3):
    results = []
    for i in range(max_iters):
        r = await agent_cycle(user_id)
        results.append(r)
        if r.get("processed", 0) == 0:
            break
    return {"iterations": len(results), "results": results}
```

### agent_cycle

📁 `aihub/agent_loop.py` linie 169-267:

```python
async def agent_cycle(user_id):
    psyche_state = get_psyche_state(user_id)  # ensure_user
    msgs = get_pending_messages(user_id, limit=20)
    if not msgs:
        return {"processed": 0}
    ranked = rank_messages(user_id, msgs)  # AttentionController
    for msg in ranked[:3]:
        decision = _cognitive.decide(user_id, msg["content"], ...)
        conflicts = process_decision(decision, user_id)
        if not conflicts:
            result = _execute_action(decision.action_type, decision.params)
    ...
```

### \_execute_action (ALL STUBS)

📁 `aihub/agent_loop.py` linie 127-160:

```python
def _execute_action(action_type, params):
    if action_type == "query":
        return {"query": params.get("query",""), "context": "memory_search_executed"}
    elif action_type == "learn":
        return {"topic": params.get("topic",""), "stored": True}
    elif action_type == "research":
        return {"topic": params.get("topic",""), "researched": True}
    elif action_type == "action":
        return {"action": params.get("action",""), "executed": True}
    return {"action_type": action_type, "status": "unknown"}
```

---

## 8. Memory GC — NIGDY NIE WOŁANY

### collect_garbage

📁 `aihub/memory_gc.py` linie 44-130:

```python
def collect_garbage(user_id: str, config: dict = None):
    ...
    stale = check_stale(user_id, days=config.get("stale_days", 90))
    if stale:
        for sid in stale[:100]:
            soft_delete_node(sid)
    ...
    _archive_old_facts(user_id, threshold_days=30)
    ...
    count = _get_fact_count(user_id)
    if count > config.get("max_facts_per_user", 5000):
        _remove_low_priority_facts(user_id, ...)
    if count > config.get("compress_above_count", 2000):
        knowledge_evolution.evolve_all(user_id)
    ...
    _vacuum()
    append_event(user_id, "memory.gc", {...})
```

### schedule_gc (NO-OP)

📁 `aihub/memory_gc.py` linie 217-218:

```python
def schedule_gc():
    logger.info("schedule_gc called (no-op in current version)")
```

### DOWÓD BRAKU CALLERÓW:

Grep `collect_garbage|schedule_gc|memory_gc` po całej bazie kodu → wyniki WYŁĄCZNIE w `aihub/memory_gc.py` (definicje). Żaden inny plik nie importuje ani nie woła tych funkcji.

---

## 9. Learning Engine — INSTANCJA BEZ UŻYCIA

### Klasa

📁 `aihub/learning_engine.py` linie 1-310:

```python
class LearningEngine:
    RULES = [
        {"name": "user_identity", "pattern": r"(mam na imię|jestem|nazywam się)\s+(\w+)", ...},
        {"name": "user_preference", "pattern": r"(lubię|uwielbiam|preferuję|wolę)\s+(.+?)[\.\,\!]", ...},
        {"name": "user_work", "pattern": r"(pracuję|robię w|zajmuję się)\s+(.+?)[\.\,\!]", ...},
        {"name": "user_goal", "pattern": r"(chcę|zamierzam|planuję|moim celem)\s+(.+?)[\.\,\!]", ...},
        {"name": "technical_fact", "pattern": r"(używam|korzystam z|mam zainstalowany)\s+(.+?)[\.\,\!]", ...},
        {"name": "constraint", "pattern": r"(nie mogę|nie wolno|zakaz|ograniczenie)\s+(.+?)[\.\,\!]", ...},
    ]

    def process_turn(self, user_id, message, role="user"):
        ...  # full regex extraction pipeline

    def learn_from_reflection(self, user_id, reflection):
        ...  # learns from psyche reflection
```

### Dowód instancjowania

📁 `aihub/cognitive_controller.py` linia 82:

```python
self.learning_engine = LearningEngine()
```

### Dowód braku wywołań

Przeszukanie `cognitive_controller.py` po `self.learning_engine.` → **0 wyników**. Żadna metoda `decide()`, `_decide_learn()`, ani żadna inna metoda CognitiveController nie woła `self.learning_engine.process_turn()` ani `self.learning_engine.learn_from_reflection()`.

---

## 10. Research Engine — PLACEHOLDER

### \_generate_placeholder_results

📁 `aihub/research_engine.py` linie 184-189:

```python
def _generate_placeholder_results(self, query):
    logger.warning("No search API configured, returning placeholder results")
    return []
```

### Dowód braku importu w runtime

Grep `research_engine|ResearchEngine` w `main.py`, `agent_engine.py`, `agent_worker.py`, `agent_api.py` → **0 wyników**. Moduł jest importowany TYLKO w `cognitive_controller.py` (jeśli w ogóle — w obecnej wersji nie ma importu ResearchEngine w cognitive_controller, jedynie `_decide_research` zwraca DecisionResult bez wywoływania ResearchEngine).

---

## 11. Knowledge Graph — ZAWSZE PUSTY

### Struktura

📁 `aihub/knowledge_graph.py` linie 1-50:

```python
class KnowledgeGraph:
    def __init__(self):
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: list[KnowledgeEdge] = []
```

### stats() — wołany w /cognitive/health

📁 `aihub/knowledge_graph.py` linia 204-247:

```python
def stats(self):
    return {
        "nodes": len(self._nodes),
        "edges": len(self._edges),
        ...
    }
```

📁 `aihub/main.py` linia ~420:

```python
# W /cognitive/health endpoint:
kg_stats = _cognitive.knowledge_graph.stats()
```

### Dowód braku danych

Żadna ścieżka runtime nie woła `knowledge_graph.add_node()` ani `knowledge_graph.add_edge()`. Instancja jest tworzona pusta i zawsze pusta pozostaje. `stats()` zwraca `{"nodes": 0, "edges": 0, ...}`.

---

## 12. Meta Memory — częściowo aktywna

### check_stale (wołany)

📁 `aihub/meta_memory.py` linie 157-187:

```python
def check_stale(user_id: str, days: int = 30) -> list[str]:
    threshold = _now() - days * 86400
    ...
    return [row["node_id"] for row in rows]
```

### Caller: cognitive_controller.\_estimate_memory_pressure

📁 `aihub/cognitive_controller.py` linie 409-420:

```python
def _estimate_memory_pressure(self, user_id):
    stale = check_stale(user_id, days=30)
    return min(1.0, len(stale) / 500)
```

### Caller: memory_gc.collect_garbage (MARTWY)

📁 `aihub/memory_gc.py` linia 55:

```python
stale = check_stale(user_id, days=config.get("stale_days", 90))
```

(Ale `collect_garbage` sam nigdy nie jest wołany)

---

## 13. Knowledge Evolution — MARTWY (zależy od GC)

### deduplicate

📁 `aihub/knowledge_evolution.py` linie 50-120:

```python
def deduplicate(self, user_id, layer="L1", threshold=0.75):
    nodes = list_recent_nodes(user_id, layer, limit=2000)
    ...
    similarity = self._compute_semantic_similarity(a["content"], b["content"])
    if similarity > threshold:
        merged = self._merge_facts(a, b)
        ...
```

### \_compute_semantic_similarity (TF-IDF z vector_index)

📁 `aihub/knowledge_evolution.py` linie 130-155:

```python
def _compute_semantic_similarity(self, text_a, text_b):
    tok_a = tokenize(text_a)
    tok_b = tokenize(text_b)
    df = build_df([tok_a, tok_b])
    vec_a = tfidf_vector(tok_a, df, 2)
    vec_b = tfidf_vector(tok_b, df, 2)
    return cosine_sparse(vec_a, vec_b)
```

### Jedyny caller: memory_gc.collect_garbage (MARTWY)

📁 `aihub/memory_gc.py` linia ~108:

```python
if count > config.get("compress_above_count", 2000):
    knowledge_evolution.evolve_all(user_id)
```

---

## 14. DB Schema (pełna)

📁 `aihub/db.py` linie 22-120:

```sql
-- memory_nodes
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    layer TEXT NOT NULL,          -- L1, L2, L3, L3_archive
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',       -- JSON array
    meta TEXT DEFAULT '{}',       -- JSON object
    ts REAL NOT NULL,
    importance REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.5,
    deleted INTEGER DEFAULT 0
);

-- FTS5 fulltext search
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5 (
    content, user_id, layer, node_id
);

-- STM (short-term memory)
CREATE TABLE IF NOT EXISTS stm_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);

-- Psyche state
CREATE TABLE IF NOT EXISTS psyche_state (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,  -- JSON blob
    ts REAL NOT NULL
);

-- Event log
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    ts REAL NOT NULL
);

-- Snapshots
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    data TEXT NOT NULL,
    ts REAL NOT NULL
);

-- Memory meta (access tracking)
CREATE TABLE IF NOT EXISTS memory_meta (
    node_id TEXT PRIMARY KEY,
    access_count INTEGER DEFAULT 0,
    last_access REAL,
    freshness_score REAL DEFAULT 1.0,
    usage_score REAL DEFAULT 0.0,
    stale_warning INTEGER DEFAULT 0
);

-- VIEW
CREATE VIEW IF NOT EXISTS memory_facts AS
    SELECT * FROM memory_nodes WHERE layer IN ('L2', 'L3') AND deleted = 0;
```

---

_Wygenerowano automatycznie — audit kodu aihub/, 2025-01_
