from aihub.memory_core import get_memory_core
from aihub.memory_psyche_contracts import MemoryScope, MemorySourceKind, MemoryType
from aihub.memory_v2_index_jobs import enqueue_index_job, index_job_summary


def test_memory_context_pack_groups_selected_items():
    core = get_memory_core()
    user_id = "ctx_pack_user"
    created = core.v2_create_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Kod produkcyjny",
        content="User wymaga pełnych plików, realnych importów i zero placeholderów.",
        source_kind="explicit_learning",
        importance_score=0.95,
        confidence_score=0.95,
    )
    assert created is not None

    pack = core.build_context_pack(user_id, "jak pisać kod dla usera", limit=8, max_chars=4000)
    dumped = pack.model_dump(mode="json")
    assert created.id in dumped["selected_ids"]
    assert dumped["preferences"]
    assert "placeholder" in pack.to_prompt_text().lower()
    assert dumped["retrieval_trace"]["candidate_count"] >= 1


def test_memory_v2_index_jobs_are_durable_and_exposed(client):
    core = get_memory_core()
    user_id = "idx_jobs_user"
    created = core.v2_create_item(
        user_id=user_id,
        memory_type="fact",
        scope="user",
        title="Index job smoke",
        content="Ten wpis służy do sprawdzenia trwałego outboxa indeksowania.",
        source_kind="explicit_learning",
        importance_score=0.8,
        confidence_score=0.9,
    )
    assert created is not None
    enqueue_index_job(user_id, created.id, reason="test retry")
    summary = index_job_summary(user_id)
    assert summary["counts"]["pending"] >= 1

    res = client.get(f"/memory/v2/index-jobs?user_id={user_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["pending"] >= 1

    pack = client.post(
        "/memory/v2/context-pack",
        json={"user_id": user_id, "query": "outbox indeksowania", "limit": 8, "max_chars": 4000},
    )
    assert pack.status_code == 200
    assert created.id in pack.json()["selected_ids"]


def test_chat_runtime_injects_canonical_context_pack_into_prompt_and_trace(isolated_db):
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    core = get_memory_core()
    user_id = "ctx_pack_chat_user"
    created = core.v2_create_item(
        user_id=user_id,
        memory_type="preference",
        scope="user",
        title="Format odpowiedzi",
        content="User chce najpierw sedno w punktach, potem szczegóły i pełne pliki bez skrótów.",
        source_kind="explicit_learning",
        importance_score=0.94,
        confidence_score=0.96,
    )
    runtime = ChatRuntime()
    ctx = runtime._build_context(
        ChatTurnInput(user_id=user_id, session_id="ctx-pack-chat", message="jak mam odpowiadać temu userowi?"),
        correction_turn_trace={},
    )
    pack = ctx.system_context.get("memory_context_pack")
    assert isinstance(pack, dict)
    assert created.id in pack.get("selected_ids", [])
    assert ctx.system_context.get("memory_context_pack_prompt")

    prompt = runtime._build_system_prompt(
        ctx,
        memory_brief="test brief",
        psyche_brief="test psyche",
        first_turn_in_thread=True,
    )
    assert "KANONICZNY MEMORY CONTEXT PACK" in prompt
    assert created.id in prompt

    trace = {}
    runtime._augment_memory_observability(trace, [], ctx.memory_context)
    assert trace["memory_context_pack_injected"] is True
    assert created.id in trace["memory_context_pack_selected_ids"]
