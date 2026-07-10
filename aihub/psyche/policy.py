import time
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PolicyDecision:
    allow: bool
    reason: str
    tags: List[str]


class PolicyEngine:
    """
    Bez LLM: decyzje heurystyczne.
    """
    def __init__(self):
        self.rules: List[Dict[str, Any]] = []
        self.last_train_ts = 0

    def train_from_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Rule learner:
        - jeśli dużo 401/403 na endpointach admin/shell -> rekomenduj blokady/ostrzejszy gate
        - jeśli dużo requestów /files poza jail -> rekomenduj zawężenie
        """
        self.rules.clear()
        self.last_train_ts = int(time.time())

        total = len(events)
        if total == 0:
            return {"trained": False, "rules": 0}

        codes = {}
        paths = {}
        for e in events:
            codes[int(e.get("status", 0))] = codes.get(int(e.get("status", 0)), 0) + 1
            p = str(e.get("path", ""))
            paths[p] = paths.get(p, 0) + 1

        hard_fail = codes.get(401, 0) + codes.get(403, 0)
        if hard_fail / max(1, total) > 0.25:
            self.rules.append({
                "id": "auth_noise_guard",
                "when": "high_unauthorized_rate",
                "action": "recommend_rate_limit_and_token_ttl_shorter",
                "weight": 0.8,
            })

        if any(p.startswith("/files") for p in paths.keys()) and paths.get("/files/read", 0) > 50:
            self.rules.append({
                "id": "fs_heavy_usage",
                "when": "many_file_reads",
                "action": "recommend_enable_audit_and_stricter_jail",
                "weight": 0.6,
            })

        return {"trained": True, "rules": len(self.rules), "total_events": total, "codes": codes}

    def decide(self, context: Dict[str, Any]) -> PolicyDecision:
        # prościutko: jak path wrażliwy i brak “ok” flagi -> deny
        path = str(context.get("path", ""))
        is_admin = path.startswith("/admin") or path.startswith("/shell") or path.startswith("/system")
        ok = bool(context.get("auth_ok", False))

        if is_admin and not ok:
            return PolicyDecision(False, "admin_path_requires_auth", ["auth", "admin"])

        return PolicyDecision(True, "allowed", ["ok"])
