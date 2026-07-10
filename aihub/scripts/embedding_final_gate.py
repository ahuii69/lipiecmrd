#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-runtime embedding + FAISS gate (no mocks). From repo root::

    python -m aihub.scripts.embedding_final_gate
    echo $?

S1 and S3 require a valid ``VOYAGE_API_KEY`` in the parent environment.
S2 uses a deliberately invalid key in a subprocess (parent key is not copied).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _print_header() -> None:
    print("[EMBEDDING FINAL GATE]")


def _child_payload() -> dict:
    """Runs inside ``--child`` subprocess; scenario in ``EMBEDDING_GATE_SCENARIO``."""
    import aihub.config  # noqa: F401
    from aihub import embedding_engine as ee

    scen = os.environ.get("EMBEDDING_GATE_SCENARIO", "")
    ee.reset_providers()
    os.environ["EMBEDDING_HEALTHCHECK_LIVE_PROBE"] = "1"

    if scen == "s1":
        h = ee.healthcheck()
        r = ee.embed_query("__embedding_final_gate_s1__")
        dim = len(r.vector)
        return {
            "scenario": "s1",
            "provider": r.provider,
            "model": r.model,
            "dim": dim,
            "fallback_used": r.embedding_fallback_used,
            "primary_attempted": r.primary_provider_attempted,
            "primary_used": r.embedding_primary_provider_used,
            "health_output_dimension": h.get("output_dimension"),
            "health_matches_embed": h.get("output_dimension") == dim,
            "health_warning": h.get("healthcheck_voyage_path_warning"),
        }

    if scen == "s2":
        h = ee.healthcheck()
        r = ee.embed_query("__embedding_final_gate_s2__")
        dim = len(r.vector)
        return {
            "scenario": "s2",
            "provider": r.provider,
            "model": r.model,
            "dim": dim,
            "fallback_used": r.embedding_fallback_used,
            "primary_attempted": r.primary_provider_attempted,
            "primary_used": r.embedding_primary_provider_used,
            "health_output_dimension": h.get("output_dimension"),
            "health_matches_embed": h.get("output_dimension") == dim,
        }

    if scen == "s3":
        h = ee.healthcheck()
        r = ee.embed_query("__embedding_final_gate_s3__")
        dim = len(r.vector)
        return {
            "scenario": "s3",
            "provider": r.provider,
            "model": r.model,
            "dim": dim,
            "fallback_used": r.embedding_fallback_used,
            "primary_attempted": r.primary_provider_attempted,
            "primary_used": r.embedding_primary_provider_used,
            "health_output_dimension": h.get("output_dimension"),
            "health_matches_embed": h.get("output_dimension") == dim,
            "health_warning": h.get("healthcheck_voyage_path_warning"),
        }

    if scen == "s4":
        h = ee.healthcheck()
        r = ee.embed_query("__embedding_final_gate_s4__")
        dim = len(r.vector)
        return {
            "scenario": "s4",
            "provider": r.provider,
            "model": r.model,
            "dim": dim,
            "fallback_used": r.embedding_fallback_used,
            "primary_attempted": r.primary_provider_attempted,
            "primary_used": r.embedding_primary_provider_used,
            "health_output_dimension": h.get("output_dimension"),
            "health_matches_embed": h.get("output_dimension") == dim,
        }

    if scen == "s5":
        # Provider/key must be set by subprocess env (before import); see parent S5.
        os.environ["EMBEDDING_HEALTHCHECK_LIVE_PROBE"] = "0"
        td = tempfile.mkdtemp(prefix="embedding_gate_faiss_")
        p = Path(td)
        os.environ["VECTOR_INDEX_PATH"] = str(p / "index.faiss")
        os.environ["VECTOR_META_PATH"] = str(p / "meta.json")

        from aihub.embedding_engine import clear_faiss_dimension_probe_cache

        clear_faiss_dimension_probe_cache()
        ee.reset_providers()

        import aihub.vector_engine as ve

        ve._index = None
        ve._meta = None
        ve._effective_dim = None

        cleared = ve.clear()
        if not cleared.get("ok"):
            return {
                "scenario": "s5",
                "error": f"clear_failed: {cleared}",
            }
        ar = ve.add_memory("embedding gate s5 unique phrase alpha", user_id="")
        if not ar.get("ok"):
            return {"scenario": "s5", "error": f"add_failed: {ar}"}
        sr = ve.search("unique phrase alpha", k=3, user_id="")
        if not sr.get("ok"):
            return {"scenario": "s5", "error": f"search_failed: {sr}"}
        idx_dim = ar.get("faiss_index_dimension")
        et = sr.get("embedding_trace") or {}
        qdim = et.get("embedding_runtime_dim")
        return {
            "scenario": "s5",
            "provider": et.get("embedding_primary_provider_used"),
            "model": et.get("embedding_model"),
            "dim": qdim,
            "fallback_used": et.get("embedding_fallback_used"),
            "primary_attempted": et.get("embedding_primary_provider_attempted"),
            "primary_used": et.get("embedding_primary_provider_used"),
            "index_dim": idx_dim,
            "dims_match": idx_dim is not None and qdim is not None and idx_dim == qdim,
            "dense_path_used": sr.get("dense_path_used"),
            "results_non_empty": len(sr.get("results") or []) > 0,
        }

    return {"scenario": scen, "error": "unknown_scenario"}


def _run_subprocess(env_updates: dict) -> dict:
    env = os.environ.copy()
    env.update(env_updates)
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + prev if prev else "")

    cmd = [sys.executable, "-m", "aihub.scripts.embedding_final_gate", "--child"]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    line = (proc.stdout or "").strip().splitlines()
    raw = line[-1] if line else ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "error": "bad_child_json",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    if proc.returncode != 0:
        data["child_returncode"] = proc.returncode
        data["child_stderr"] = proc.stderr
    return data


def _ok_s1(d: dict) -> bool:
    if d.get("error"):
        return False
    return (
        d.get("provider") == "voyage"
        and d.get("dim") == 1024
        and d.get("fallback_used") is False
        and d.get("health_matches_embed") is True
        and not d.get("health_warning")
    )


def _ok_s2(d: dict) -> bool:
    if d.get("error"):
        return False
    return (
        d.get("provider") == "sentence-transformers"
        and d.get("dim") == 384
        and d.get("fallback_used") is True
        and d.get("primary_attempted") == "voyage"
        and d.get("health_matches_embed") is True
    )


def _ok_s3(d: dict) -> bool:
    if d.get("error"):
        return False
    if d.get("primary_attempted") != "voyage":
        return False
    if d.get("health_warning"):
        return False
    if not d.get("health_matches_embed"):
        return False
    return d.get("provider") == "voyage" or d.get("fallback_used") is True


def _ok_s4(d: dict) -> bool:
    if d.get("error"):
        return False
    return (
        d.get("provider") == "sentence-transformers"
        and d.get("fallback_used") is False
        and d.get("primary_attempted") == "none"
        and d.get("health_matches_embed") is True
    )


def _ok_s5(d: dict) -> bool:
    if d.get("error"):
        return False
    return (
        d.get("dims_match") is True
        and d.get("dense_path_used") is True
        and d.get("results_non_empty") is True
    )


def main() -> int:
    if "--child" in sys.argv:
        try:
            print(json.dumps(_child_payload()), flush=True)
        except Exception as exc:  # noqa: BLE001 — gate must surface any failure
            print(json.dumps({"error": str(exc)}), flush=True)
            return 1
        return 0

    _print_header()
    voyage_key = (os.environ.get("VOYAGE_API_KEY") or "").strip()

    results: dict[str, tuple[bool, dict]] = {}

    # S1 — voyage + real key
    if not voyage_key:
        s1_payload = {
            "error": "VOYAGE_API_KEY missing (required for S1)",
            "scenario": "s1",
        }
        results["S1"] = (False, s1_payload)
        print("S1:", json.dumps(s1_payload, indent=2))
    else:
        d1 = _run_subprocess(
            {
                "EMBEDDING_GATE_SCENARIO": "s1",
                "EMBEDDING_PROVIDER": "voyage",
                "VOYAGE_API_KEY": voyage_key,
            }
        )
        results["S1"] = (_ok_s1(d1), d1)
        print("S1:", json.dumps(d1, indent=2))

    # S2 — voyage + invalid key (do not pass real key)
    d2 = _run_subprocess(
        {
            "EMBEDDING_GATE_SCENARIO": "s2",
            "EMBEDDING_PROVIDER": "voyage",
            "VOYAGE_API_KEY": "pa-invalid-voyage-key-for-gate-00000",
        }
    )
    results["S2"] = (_ok_s2(d2), d2)
    print("S2:", json.dumps(d2, indent=2))

    # S3 — auto + real key
    if not voyage_key:
        s3_payload = {
            "error": "VOYAGE_API_KEY missing (required for S3)",
            "scenario": "s3",
        }
        results["S3"] = (False, s3_payload)
        print("S3:", json.dumps(s3_payload, indent=2))
    else:
        d3 = _run_subprocess(
            {
                "EMBEDDING_GATE_SCENARIO": "s3",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": voyage_key,
            }
        )
        results["S3"] = (_ok_s3(d3), d3)
        print("S3:", json.dumps(d3, indent=2))

    # S4 — auto, no key
    e4 = os.environ.copy()
    e4.pop("VOYAGE_API_KEY", None)
    # subprocess needs full env without key
    d4 = _run_subprocess(
        {
            "EMBEDDING_GATE_SCENARIO": "s4",
            "EMBEDDING_PROVIDER": "auto",
            "VOYAGE_API_KEY": "",
        }
    )
    results["S4"] = (_ok_s4(d4), d4)
    print("S4:", json.dumps(d4, indent=2))

    d5 = _run_subprocess(
        {
            "EMBEDDING_GATE_SCENARIO": "s5",
            "EMBEDDING_PROVIDER": "auto",
            "VOYAGE_API_KEY": "",
        }
    )
    results["S5"] = (_ok_s5(d5), d5)
    print("S5:", json.dumps(d5, indent=2))

    lines = []
    final_ok = True
    for label in ("S1", "S2", "S3", "S4", "S5"):
        ok, _payload = results[label]
        final_ok = final_ok and ok
        lines.append(f"{label}: {'OK' if ok else 'FAIL'}")

    for line in lines:
        print(line)
    print("FINAL:", "OK" if final_ok else "FAIL")
    return 0 if final_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
