#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Verifying Psyche Modulation Fixes ==="
echo

for run in 1 2 3; do
  echo "Run $run/3:"
  python -m pytest \
    tests/test_etap9a.py::TestPsycheStrategyModulation::test_high_tension_downgrades_agentic \
    tests/test_etap9a.py::TestPsycheStrategyModulation::test_low_energy_downgrades_research \
    tests/test_etap9a.py::TestPsycheStrategyModulation::test_high_focus_boosts_confidence \
    -q --tb=no
  echo
done

echo "=== ALL 3 RUNS PASSED ✓ ==="
