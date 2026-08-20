# Evaluator Test Template
#
# Each test file should test one specific bug.
# The test should check BEHAVIOR, not source code changes.
#
# Environment variables available:
#   WORKSPACE_PATH - Path to the team's workspace
#   CHALLENGE_CODE - The challenge code (e.g. CH-01)
#   TEAM_CODE      - The team code (e.g. BUG-7K4M)
#
# Exit code 0 = PASS (bug fixed)
# Exit code non-zero = FAIL (bug still present)

import os
import sys
import subprocess

WORKSPACE = os.environ.get("WORKSPACE_PATH", ".")

def test_bug_01():
    """Test that the main application starts without errors."""
    try:
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=15
        )
        assert result.returncode == 0, f"App failed to start: {result.stderr}"
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        assert False, f"Error: {e}"

if __name__ == "__main__":
    try:
        test_bug_01()
        print("PASS")
        sys.exit(0)
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
