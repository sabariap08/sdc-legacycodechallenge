import os
import sys

WORKSPACE = os.environ.get("WORKSPACE_PATH", ".")

sys.path.insert(0, WORKSPACE)

def test_bug_01_add_positive_numbers():
    from main import add
    result = add(10, 20)
    assert result == 30, f"Expected 30, got {result}"

def test_bug_02_add_negative_numbers():
    from main import add
    result = add(-5, -5)
    assert result == -10, f"Expected -10, got {result}"

def test_bug_03_add_zero():
    from main import add
    result = add(0, 0)
    assert result == 0, f"Expected 0, got {result}"

def test_bug_04_add_mixed():
    from main import add
    result = add(-1, 1)
    assert result == 0, f"Expected 0, got {result}"

def test_bug_05_add_large():
    from main import add
    result = add(1000000, 2000000)
    assert result == 3000000, f"Expected 3000000, got {result}"

if __name__ == "__main__":
    tests = [
        test_bug_01_add_positive_numbers,
        test_bug_02_add_negative_numbers,
        test_bug_03_add_zero,
        test_bug_04_add_mixed,
        test_bug_05_add_large,
    ]
    all_pass = True
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            all_pass = False
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            all_pass = False
    sys.exit(0 if all_pass else 1)
