#!/usr/bin/env python3
"""Test the security fixes."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from aihub.web_tools import _is_private_ip, _validate_url_safe

print("=" * 60)
print("TESTING SSRF PROTECTION")
print("=" * 60)

# Test 1: Private IP blocking
test_cases = [
    ("localhost", True, "localhost should be blocked"),
    ("127.0.0.1", True, "127.0.0.1 should be blocked"),
    ("192.168.1.1", True, "192.168.x.x should be blocked"),
    ("10.0.0.1", True, "10.x.x.x should be blocked"),
    ("172.16.0.1", True, "172.16.x.x should be blocked"),
    ("169.254.169.254", True, "169.254.x.x (AWS metadata) should be blocked"),
    ("google.com", False, "google.com should be allowed"),
    ("github.com", False, "github.com should be allowed"),
]

all_passed = True
for ip, should_block, description in test_cases:
    is_private = _is_private_ip(ip)
    passed = is_private == should_block
    status = "✅" if passed else "❌"
    print(f"{status} {description}: got {is_private}")
    if not passed:
        all_passed = False

print("\n" + "=" * 60)
print("TESTING URL VALIDATION")
print("=" * 60)

url_cases = [
    ("https://google.com", False, "Public URL should pass"),
    ("https://localhost:8000/admin", True, "localhost URL should block"),
    ("http://127.0.0.1:5000", True, "127.0.0.1 URL should block"),
    ("http://10.0.0.1", True, "Private IP URL should block"),
    ("http://192.168.1.1", True, "Private IP URL should block"),
    ("http://169.254.169.254/latest/meta-data/", True, "AWS metadata should block"),
]

for url, should_fail, description in url_cases:
    try:
        _validate_url_safe(url)
        failed = False
    except ValueError:
        failed = True

    passed = failed == should_fail
    status = "✅" if passed else "❌"
    print(f"{status} {description}: got failed={failed}")
    if not passed:
        all_passed = False

print("\n" + "=" * 60)
print("TESTING CONFIG SECRETS HANDLING")
print("=" * 60)

import os

os.environ["ENV"] = "development"

from aihub import config as config_dev

print(f"✅ Development mode loads: ENV={os.getenv('ENV')}")

# Try production mode (should fail if secrets not set)
os.environ["ENV"] = "production"
os.environ.pop("DEEPINFRA_API_KEY", None)
os.environ.pop("BRAVE_API_KEY", None)
os.environ.pop("VOYAGE_API_KEY", None)

try:
    # Re-import to trigger validation
    import importlib

    importlib.reload(config_dev)
    print("❌ Production mode should fail without secrets")
    all_passed = False
except RuntimeError as e:
    print(f"✅ Production mode correctly rejects missing secrets: {str(e)[:50]}...")

# Now with secrets set
os.environ["DEEPINFRA_API_KEY"] = "test-key-1"
os.environ["BRAVE_API_KEY"] = "test-key-2"
os.environ["VOYAGE_API_KEY"] = "test-key-3"
importlib.reload(config_dev)
print("✅ Production mode accepts when secrets are set")

print("\n" + "=" * 60)
if all_passed:
    print("✅ ALL TESTS PASSED!")
else:
    print("❌ SOME TESTS FAILED")
    sys.exit(1)
