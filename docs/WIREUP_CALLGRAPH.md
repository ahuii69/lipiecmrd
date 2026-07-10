# WIREUP CALLGRAPH (Agent Core Repair + Scaling)

## Primary cognitive path

```text
agent_loop.agent_cycle
  -> attention_controller.rank_messages
  -> cognitive_controller.decide
     -> conflict_detector.check_conflict
  -> agent_loop.process_decision
     -> agent_executor.execute
        -> (query) memory_engine.retrieve_context
        -> (learn) memory_engine.add_fact
        -> (research) research_engine.research
        -> (action) web_tools.fetch_url | fs_tools.write_file | system_ops.create_snapshot
```

## Background autonomous path

```text
main.startup
  -> db.init_db
  -> knowledge_graph.load_from_db
  -> agent_worker.start_worker_once
     -> agent_engine.agent_tick (loop)
        -> _pull_new_stm
        -> psyche_engine.evolve
        -> agent_engine.extract_facts_from_text
        -> agent_engine.plan_from_text
        -> agent_db.enqueue_task
        -> agent_db.claim_next_task
        -> agent_engine.execute_task
        -> agent_db.complete_task
        -> agent_engine._maybe_gc
```

## Memory + knowledge path

```text
memory_engine.process_turn
  -> vector_hook.remember_turn(user_id,...)
  -> add_stm
  -> add_episode
     -> _feed_knowledge_graph(node_type=episode)
  -> learning_engine.extract_facts_from_message
  -> add_fact
     -> _feed_knowledge_graph(node_type=fact)
        -> user -> fact edge
        -> episode -> fact edge
        -> persist_node / persist_edge
```

## Retrieval path

```text
memory_engine.retrieve_context
  -> db.search_nodes_fts (L1/L2)
  -> TF-IDF rerank
  -> vector_engine.search(query, user_id)
  -> knowledge_graph.query_nodes(query)
  -> knowledge_graph.get_related_nodes(node_id)
  -> meta_memory.touch_nodes
```
