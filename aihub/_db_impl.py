"""Compatibility DB implementation module.

This module preserves legacy import paths that expect ``aihub._db_impl``
while keeping ``aihub.db`` as the canonical implementation.

Design goals:
- non-destructive backward compatibility for recovered repository state,
- no DB logic duplication,
- no architectural changes.
"""

import importlib

_db = importlib.import_module("aihub.db")


# Re-export historically expected DB symbols used by package-level imports.
init_db = _db.init_db
now_ts = _db.now_ts
json_dumps = _db.json_dumps
json_loads = _db.json_loads
exec_one = _db.exec_one
fetch_all = _db.fetch_all
fetch_one = _db.fetch_one
upsert_psyche = _db.upsert_psyche
get_psyche = _db.get_psyche
append_event = _db.append_event
get_events_since = _db.get_events_since
insert_stm_message = _db.insert_stm_message
get_stm = _db.get_stm
prune_stm = _db.prune_stm
upsert_node = _db.upsert_node
search_nodes_fts = _db.search_nodes_fts
list_recent_nodes = _db.list_recent_nodes
soft_delete_node = _db.soft_delete_node


# Delegate all other attribute lookups to canonical implementation module.
def __getattr__(name: str):
    return getattr(_db, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_db)))


__all__ = [
    "append_event",
    "exec_one",
    "fetch_all",
    "fetch_one",
    "get_events_since",
    "get_psyche",
    "get_stm",
    "init_db",
    "insert_stm_message",
    "json_dumps",
    "json_loads",
    "list_recent_nodes",
    "now_ts",
    "prune_stm",
    "search_nodes_fts",
    "soft_delete_node",
    "upsert_node",
    "upsert_psyche",
]
