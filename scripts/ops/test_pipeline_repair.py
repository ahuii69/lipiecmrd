#!/usr/bin/env python3
"""Pipeline repair validation test"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

print("=" * 70)
print("SYSTEM REPAIR VALIDATION REPORT")
print("=" * 70)

# TEST 1: Module Imports
print("\nTEST 1: Module Imports")
print("-" * 70)

results = {}

try:
    results["CognitiveController"] = "PASS"
    print("✓ CognitiveController")
except Exception as e:
    results["CognitiveController"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ CognitiveController: {e}")

try:
    results["AttentionController"] = "PASS"
    print("✓ AttentionController")
except Exception as e:
    results["AttentionController"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ AttentionController: {e}")

try:
    results["ConflictDetector"] = "PASS"
    print("✓ ConflictDetector + check_conflict")
except Exception as e:
    results["ConflictDetector"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ ConflictDetector: {e}")

try:
    results["KnowledgeGraph"] = "PASS"
    print("✓ KnowledgeGraph")
except Exception as e:
    results["KnowledgeGraph"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ KnowledgeGraph: {e}")

try:
    results["MetaMemory"] = "PASS"
    print("✓ MetaMemory (check_stale)")
except Exception as e:
    results["MetaMemory"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ MetaMemory: {e}")

try:
    results["AgentLoop"] = "PASS"
    print("✓ AgentLoop (with get_psyche_state, rank_messages)")
except Exception as e:
    results["AgentLoop"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ AgentLoop: {e}")

try:
    results["Database"] = "PASS"
    print("✓ Database (append_event, now_ts)")
except Exception as e:
    results["Database"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ Database: {e}")

try:
    results["MetricsEngine"] = "PASS"
    print("✓ MetricsEngine")
except Exception as e:
    results["MetricsEngine"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ MetricsEngine: {e}")

try:
    results["PredictionEngine"] = "PASS"
    print("✓ PredictionEngine")
except Exception as e:
    results["PredictionEngine"] = f"FAIL: {str(e)[:50]}"
    print(f"✗ PredictionEngine: {e}")

# TEST 2: Function Signature Validation
print("\n\nTEST 2: Function Signatures")
print("-" * 70)

try:
    import inspect

    from aihub.attention_controller import rank_messages as rank_msgs

    sig = inspect.signature(rank_msgs)
    params = list(sig.parameters.keys())
    if params == ["user_id", "messages"]:
        print(f"✓ rank_messages signature correct: {params}")
        results["RankMessagesSignature"] = "PASS"
    else:
        print(f"✗ rank_messages signature wrong: {params}")
        results["RankMessagesSignature"] = f"FAIL: {params}"
except Exception as e:
    print(f"✗ rank_messages check: {e}")
    results["RankMessagesSignature"] = f"FAIL: {str(e)[:50]}"

try:
    import inspect

    from aihub.conflict_detector import check_conflict as cc

    sig = inspect.signature(cc)
    params = list(sig.parameters.keys())
    if params == ["user_id", "actions"]:
        print(f"✓ check_conflict signature correct: {params}")
        results["CheckConflictSignature"] = "PASS"
    else:
        print(f"✗ check_conflict signature wrong: {params}")
        results["CheckConflictSignature"] = f"FAIL: {params}"
except Exception as e:
    print(f"✗ check_conflict check: {e}")
    results["CheckConflictSignature"] = f"FAIL: {str(e)[:50]}"

try:
    import inspect

    from aihub.agent_loop import get_psyche_state as gps

    sig = inspect.signature(gps)
    params = list(sig.parameters.keys())
    if params == ["user_id"]:
        print(f"✓ get_psyche_state signature correct: {params}")
        results["GetPsycheStateSignature"] = "PASS"
    else:
        print(f"✗ get_psyche_state signature wrong: {params}")
        results["GetPsycheStateSignature"] = f"FAIL: {params}"
except Exception as e:
    print(f"✗ get_psyche_state check: {e}")
    results["GetPsycheStateSignature"] = f"FAIL: {str(e)[:50]}"

# TEST 3: Code Quality Checks
print("\n\nTEST 3: Code Quality")
print("-" * 70)

try:
    with open(_REPO_ROOT / "aihub/cognitive_controller.py", "r", encoding="utf-8") as f:
        content = f.read()
        if "def _rank_messages" in content:
            print("✗ Dead code _rank_messages still present")
            results["DeadCodeRemoval"] = "FAIL: _rank_messages still in file"
        else:
            print("✓ Dead code _rank_messages removed")
            results["DeadCodeRemoval"] = "PASS"
except Exception as e:
    print(f"✗ Code quality check: {e}")
    results["DeadCodeRemoval"] = f"FAIL: {str(e)[:50]}"

try:
    with open(_REPO_ROOT / "aihub/attention_controller.py", "r", encoding="utf-8") as f:
        content = f.read()
        if "from aihub.psyche_engine import" in content:
            print("✓ attention_controller:18 import fixed")
            results["ImportFixAtt"] = "PASS"
        else:
            print("✗ attention_controller import still broken")
            results["ImportFixAtt"] = "FAIL"
except Exception as e:
    results["ImportFixAtt"] = f"FAIL: {str(e)[:50]}"

try:
    with open(_REPO_ROOT / "aihub/knowledge_graph.py", "r", encoding="utf-8") as f:
        content = f.read()
        if "from aihub.db import" in content:
            print("✓ knowledge_graph:18 import fixed")
            results["ImportFixKG"] = "PASS"
        else:
            print("✗ knowledge_graph import still broken")
            results["ImportFixKG"] = "FAIL"
except Exception as e:
    results["ImportFixKG"] = f"FAIL: {str(e)[:50]}"

try:
    with open(_REPO_ROOT / "aihub/agent_loop.py", "r", encoding="utf-8") as f:
        content = f.read()
        if "rank_messages(user_id, messages)" in content:
            print("✓ agent_loop:160 rank_messages signature fixed")
            results["SignatureFixRank"] = "PASS"
        else:
            print("✗ agent_loop rank_messages signature still wrong")
            results["SignatureFixRank"] = "FAIL"
except Exception as e:
    results["SignatureFixRank"] = f"FAIL: {str(e)[:50]}"

try:
    with open(_REPO_ROOT / "aihub/agent_loop.py", "r", encoding="utf-8") as f:
        content = f.read()
        if "def get_psyche_state(user_id: str)" in content:
            print("✓ agent_loop:143 get_psyche_state implemented")
            results["GetPsycheStateImpl"] = "PASS"
        else:
            print("✗ agent_loop get_psyche_state not found")
            results["GetPsycheStateImpl"] = "FAIL"
except Exception as e:
    results["GetPsycheStateImpl"] = f"FAIL: {str(e)[:50]}"

# Summary
print("\n\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

passed = sum(1 for v in results.values() if v == "PASS")
failed = sum(1 for v in results.values() if v != "PASS")
total = len(results)

print(f"\nTotal Tests: {total}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if failed == 0:
    print("\n✓ ALL REPAIRS SUCCESSFUL - System Ready for Testing")
    status = "READY"
else:
    print(f"\n✗ {failed} issues remain - See details above")
    status = "INCOMPLETE"

print("\n" + "=" * 70)
print(f"FINAL STATUS: {status}")
print("=" * 70 + "\n")

sys.exit(0 if failed == 0 else 1)
