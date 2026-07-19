"""Canonical MutationPolicy — jedna bramka dla ToolRouter / AgentExecutor / HTTP / agent_engine.

Zero wyjątków: zapis pliku, snapshot i podobne mutacje wymagają jawnego ``confirmed``.
``force_agent_execute`` nigdy nie ustawia potwierdzenia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Agent-loop aliases + registry names that require confirmation.
CONFIRMATION_REQUIRED_TOOLS = frozenset(
    {
        "fs_write",
        "fs.write",
        "fs.write_file",
        "write_file",
        "snapshot",
        "system_snapshot",
        "system.snapshot",
        "snapshot.create",
    }
)


@dataclass(frozen=True)
class MutationDecision:
    allowed: bool
    reason: str
    requires_confirmation: bool = False
    tool: str = ""

    def as_block_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"policy_blocked: {self.reason}",
            "requires_confirmation": self.requires_confirmation,
            "tool": self.tool,
            "message": (
                "Ta operacja wymaga jawnego potwierdzenia użytkownika "
                "(zapis pliku / snapshot / usunięcie). "
                "force_agent_execute nie pomija tej polityki."
            ),
        }


def tool_requires_mutation_confirmation(tool_name: str) -> bool:
    name = (tool_name or "").strip().lower()
    if not name:
        return False
    if name in CONFIRMATION_REQUIRED_TOOLS:
        return True
    return name.startswith("fs.write") or name.startswith("snapshot.")


def mutation_is_confirmed(payload: dict[str, Any] | None) -> bool:
    """True only when the caller explicitly confirmed (never inferred from force_execute)."""
    data = payload if isinstance(payload, dict) else {}
    if bool(data.get("_confirmed") or data.get("confirmed")):
        return True
    nested = data.get("params")
    if isinstance(nested, dict) and bool(
        nested.get("_confirmed") or nested.get("confirmed")
    ):
        return True
    return False


def evaluate_mutation(
    tool_name: str,
    *,
    confirmed: bool | None = None,
    payload: dict[str, Any] | None = None,
) -> MutationDecision:
    """Single gate: may this mutating tool run?"""
    name = (tool_name or "").strip()
    if not tool_requires_mutation_confirmation(name):
        return MutationDecision(allowed=True, reason="not_a_gated_mutation", tool=name)
    ok_confirm = bool(confirmed) if confirmed is not None else mutation_is_confirmed(payload)
    if ok_confirm:
        return MutationDecision(allowed=True, reason="confirmed", tool=name)
    return MutationDecision(
        allowed=False,
        reason="tool requires confirmation",
        requires_confirmation=True,
        tool=name,
    )


def block_unconfirmed_mutation(
    tool_name: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a structured block result, or None if the action may proceed."""
    decision = evaluate_mutation(tool_name, payload=payload)
    if decision.allowed:
        return None
    return decision.as_block_dict()


def collect_pending_confirmations(
    *,
    tool_calls: list[Any] | None = None,
    tool_results: list[Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract UI-ready pending confirmation requests from a completed turn."""
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()

    calls = list(tool_calls or [])
    by_id: dict[str, Any] = {}
    for c in calls:
        cid = str(getattr(c, "tool_call_id", None) or (c.get("tool_call_id") if isinstance(c, dict) else "") or "")
        if cid:
            by_id[cid] = c

    for r in tool_results or []:
        name = str(getattr(r, "name", None) or (r.get("name") if isinstance(r, dict) else "") or "")
        err = str(getattr(r, "error", None) or (r.get("error") if isinstance(r, dict) else "") or "")
        out = getattr(r, "output", None) if not isinstance(r, dict) else r.get("output")
        if not isinstance(out, dict):
            out = {}
        nested = out.get("result") if isinstance(out.get("result"), dict) else {}
        needs = bool(
            out.get("requires_confirmation")
            or nested.get("requires_confirmation")
            or ("requires confirmation" in err.lower())
        )
        if not needs:
            continue
        cid = str(
            getattr(r, "tool_call_id", None)
            or (r.get("tool_call_id") if isinstance(r, dict) else "")
            or ""
        )
        call = by_id.get(cid)
        args: dict[str, Any] = {}
        if call is not None:
            raw_args = getattr(call, "arguments", None)
            if raw_args is None and isinstance(call, dict):
                raw_args = call.get("arguments")
            if isinstance(raw_args, dict):
                args = {k: v for k, v in raw_args.items() if k != "_confirmed"}
        key = f"{name}:{cid}:{sorted(args.keys())}"
        if key in seen:
            continue
        seen.add(key)
        pending.append(
            {
                "tool_name": name,
                "arguments": args,
                "tool_call_id": cid or None,
                "message": str(
                    out.get("message")
                    or nested.get("message")
                    or err
                    or "Wymagane potwierdzenie użytkownika."
                )[:500],
            }
        )

    for e in errors or []:
        if not isinstance(e, dict):
            continue
        if not (
            e.get("requires_confirmation")
            or "requires confirmation" in str(e.get("error") or "").lower()
        ):
            continue
        name = str(e.get("tool") or e.get("tool_name") or "")
        key = f"err:{name}:{e.get('error')}"
        if key in seen:
            continue
        seen.add(key)
        pending.append(
            {
                "tool_name": name,
                "arguments": dict(e.get("arguments") or {})
                if isinstance(e.get("arguments"), dict)
                else {},
                "tool_call_id": e.get("tool_call_id"),
                "message": str(e.get("message") or e.get("error") or "Wymagane potwierdzenie.")[
                    :500
                ],
            }
        )

    return pending
