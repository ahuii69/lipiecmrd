#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "$SCRIPT_DIR/../.." && pwd)"
source .venv/bin/activate

echo "=== Running ETAP 9A Tests ==="
echo ""

echo "1. Testing agent_runner sync fix..."
python -m pytest tests/test_etap9a.py::TestAgentRunnerAsyncioFix::test_run_agent_sync_works -xvs
echo "✓ Agent runner sync test PASSED"
echo ""

echo "2. Testing alias normalization..."
python -m pytest tests/test_etap9a.py::TestToolAliasNormalizationFix -q
echo "✓ Alias normalization tests PASSED"
echo ""

echo "3. Testing ETAP 9A trace fields..."
python -m pytest tests/test_etap9a.py::TestChatRuntimeEtap9aFix -q
echo "✓ ETAP 9A trace field tests PASSED"
echo ""

echo "=== All ETAP 9A tests completed successfully ==="
