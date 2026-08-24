import os
import time
import subprocess
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.security import get_participant_user
from app.config import TEAM_WORKSPACE_PATH, EVALUATOR_PATH
from app.utils import sanitize_path
from typing import Optional

router = APIRouter(prefix="/api/execution", tags=["execution"])

LANGUAGE_COMMANDS = {
    ".py":  {"run": ["python", "{file}"], "name": "Python"},
    ".js":  {"run": ["node", "{file}"], "name": "JavaScript"},
    ".java": {"run": ["java", "{class}"], "name": "Java", "compile": ["javac", "{file}"]},
    ".c":   {"run": ["./{out}"], "name": "C", "compile": ["gcc", "{file}", "-o", "{out}"]},
    ".cpp": {"run": ["./{out}"], "name": "C++", "compile": ["g++", "{file}", "-o", "{out}"]},
}

MAX_OUTPUT = 50000
RUN_TIMEOUT = 30
TEST_TIMEOUT = 60


def _get_workspace(team_code: str, challenge_code: str) -> str:
    return os.path.join(os.path.abspath(TEAM_WORKSPACE_PATH), team_code, challenge_code)


def _find_entry_file(workspace: str, file_path: str = None) -> Optional[str]:
    if file_path:
        if os.path.exists(os.path.join(workspace, file_path)):
            return file_path
        return None
    priorities = ["main.py", "app.py", "run.py", "solution.py", "index.py"]
    for name in priorities:
        if os.path.exists(os.path.join(workspace, name)):
            return name
    for f in sorted(os.listdir(workspace)):
        if os.path.isfile(os.path.join(workspace, f)):
            ext = os.path.splitext(f)[1].lower()
            if ext in LANGUAGE_COMMANDS and not f.startswith("test_"):
                return f
    return None


def _build_run_args(workspace: str, file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    lang = LANGUAGE_COMMANDS.get(ext)
    if not lang:
        return None
    base = os.path.splitext(file_path)[0]
    run_args = []
    for part in lang["run"]:
        part = part.replace("{file}", file_path)
        part = part.replace("{class}", base)
        part = part.replace("{out}", base)
        run_args.append(part)
    compile_args = None
    if "compile" in lang:
        compile_args = []
        for part in lang["compile"]:
            part = part.replace("{file}", file_path)
            part = part.replace("{class}", base)
            part = part.replace("{out}", base)
            compile_args.append(part)
    return run_args, compile_args


def _has_evaluator(challenge_code: str):
    evaluator_dir = os.path.join(EVALUATOR_PATH, challenge_code)
    if not os.path.exists(evaluator_dir):
        return False, []
    test_files = sorted(
        f for f in os.listdir(evaluator_dir)
        if f.startswith("test_") and f.endswith(".py")
    )
    return bool(test_files), test_files


def _has_workspace_tests(workspace: str):
    test_files = sorted(
        f for f in os.listdir(workspace)
        if f.startswith("test_") and f.endswith(".py")
    )
    if test_files:
        return True, test_files
    if os.path.isdir(os.path.join(workspace, "tests")):
        return True, []
    if os.path.isdir(os.path.join(workspace, "test")):
        return True, []
    for name in ["pytest.ini", "setup.cfg", "pyproject.toml"]:
        if os.path.exists(os.path.join(workspace, name)):
            return True, []
    return False, []


class RunRequest(BaseModel):
    stdin: Optional[str] = None
    file_path: Optional[str] = None


@router.post("/run")
async def run_code(body: RunRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = _get_workspace(team_code, challenge_code)
    if not os.path.exists(workspace):
        raise HTTPException(status_code=404, detail="Workspace not found")

    run_file = body.file_path
    if run_file:
        if not sanitize_path(run_file):
            raise HTTPException(status_code=400, detail="Invalid file path")
        full = os.path.normpath(os.path.join(workspace, run_file))
        if not full.startswith(os.path.normpath(workspace)):
            raise HTTPException(status_code=403, detail="Path traversal detected")
        if not os.path.isfile(full):
            raise HTTPException(status_code=404, detail="File not found")

    entry = _find_entry_file(workspace, run_file)
    if not entry:
        return {
            "stdout": "",
            "stderr": "No executable file found. Open a source file and click Run.",
            "exit_code": -1,
            "execution_time": 0,
            "status": "error",
            "language": "unknown",
        }

    ext = os.path.splitext(entry)[1].lower()
    lang = LANGUAGE_COMMANDS.get(ext, {})
    lang_name = lang.get("name", ext)

    args_result = _build_run_args(workspace, entry)
    if not args_result:
        return {
            "stdout": "",
            "stderr": f"Unsupported file type: {ext}",
            "exit_code": -1,
            "execution_time": 0,
            "status": "error",
            "language": lang_name,
        }

    run_args, compile_args = args_result

    env = {k: v for k, v in os.environ.items() if k not in (
        "MONGODB_URI", "DATABASE_NAME", "SECRET_KEY",
        "ADMIN_USERNAME", "ADMIN_PASSWORD",
    )}
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    start = time.time()

    if compile_args:
        try:
            comp = subprocess.run(
                compile_args,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
                cwd=workspace,
                env=env,
            )
            if comp.returncode != 0:
                return {
                    "stdout": "",
                    "stderr": comp.stderr[-MAX_OUTPUT:] if comp.stderr else "Compilation failed",
                    "exit_code": comp.returncode,
                    "execution_time": round(time.time() - start, 2),
                    "status": "compilation_error",
                    "language": lang_name,
                }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Compilation timed out after {RUN_TIMEOUT} seconds.",
                "exit_code": -1,
                "execution_time": round(time.time() - start, 2),
                "status": "timeout",
                "language": lang_name,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": f"Compilation error: {str(e)}",
                "exit_code": -1,
                "execution_time": round(time.time() - start, 2),
                "status": "error",
                "language": lang_name,
            }

    try:
        proc = subprocess.Popen(
            run_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        stdin_bytes = (body.stdin or "").encode("utf-8", errors="replace")
        out_bytes, err_bytes = proc.communicate(input=stdin_bytes, timeout=RUN_TIMEOUT)
        elapsed = round(time.time() - start, 2)

        return {
            "stdout": out_bytes.decode("utf-8", errors="replace")[-MAX_OUTPUT:],
            "stderr": err_bytes.decode("utf-8", errors="replace")[-MAX_OUTPUT:],
            "exit_code": proc.returncode,
            "execution_time": elapsed,
            "status": "success" if proc.returncode == 0 else "runtime_error",
            "language": lang_name,
        }
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": f"Execution timed out after {RUN_TIMEOUT} seconds.",
            "exit_code": -1,
            "execution_time": RUN_TIMEOUT,
            "status": "timeout",
            "language": lang_name,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Execution error: {str(e)}",
            "exit_code": -1,
            "execution_time": round(time.time() - start, 2),
            "status": "error",
            "language": lang_name,
        }


class TestRequest(BaseModel):
    stdin: Optional[str] = None


@router.post("/test")
async def test_code(body: TestRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = _get_workspace(team_code, challenge_code)
    if not os.path.exists(workspace):
        raise HTTPException(status_code=404, detail="Workspace not found")

    has_eval, eval_files = _has_evaluator(challenge_code)
    if has_eval:
        return await _run_evaluator_tests(workspace, challenge_code, team_code, eval_files)

    has_ws, ws_test_files = _has_workspace_tests(workspace)
    if has_ws:
        return await _run_pytest(workspace, ws_test_files)

    return {
        "configured": False,
        "message": "Automated testing is not configured for this challenge.",
        "results": [],
        "exit_code": 0,
    }


async def _run_evaluator_tests(workspace, challenge_code, team_code, test_files):
    evaluator_dir = os.path.join(EVALUATOR_PATH, challenge_code)
    results = []
    for i, tf in enumerate(sorted(test_files), 1):
        test_path = os.path.join(evaluator_dir, tf)
        env = os.environ.copy()
        env["WORKSPACE_PATH"] = workspace
        env["CHALLENGE_CODE"] = challenge_code
        env["TEAM_CODE"] = team_code
        start = time.time()
        try:
            r = subprocess.run(
                ["python", test_path],
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT,
                cwd=workspace,
                env=env,
            )
            elapsed = round(time.time() - start, 2)
            passed = r.returncode == 0
            results.append({
                "test": f"Test {i}",
                "passed": passed,
                "reason": r.stdout[-500:].strip() if not passed else "",
                "time": elapsed,
            })
        except subprocess.TimeoutExpired:
            results.append({"test": f"Test {i}", "passed": False, "reason": "Test timed out", "time": TEST_TIMEOUT})
        except Exception as e:
            results.append({"test": f"Test {i}", "passed": False, "reason": str(e), "time": 0})

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "configured": True,
        "message": f"{passed_count} / {len(results)} tests passed",
        "results": results,
        "exit_code": 0 if passed_count == len(results) else 1,
        "total": len(results),
        "passed": passed_count,
    }


async def _run_pytest(workspace, test_files):
    if test_files:
        cmd = ["python", "-m", "pytest"] + test_files + ["-v"]
    else:
        cmd = ["python", "-m", "pytest", "tests/", "-v"]

    env = {k: v for k, v in os.environ.items() if k not in (
        "MONGODB_URI", "DATABASE_NAME", "SECRET_KEY",
        "ADMIN_USERNAME", "ADMIN_PASSWORD",
    )}
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    start = time.time()
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
            cwd=workspace,
            env=env,
        )
        elapsed = round(time.time() - start, 2)
        return {
            "configured": True,
            "stdout": r.stdout[-MAX_OUTPUT:],
            "stderr": r.stderr[-MAX_OUTPUT:],
            "exit_code": r.returncode,
            "execution_time": elapsed,
            "status": "success" if r.returncode == 0 else "tests_failed",
            "message": "All tests passed" if r.returncode == 0 else "Some tests failed",
        }
    except subprocess.TimeoutExpired:
        return {
            "configured": True,
            "stdout": "",
            "stderr": f"Test execution timed out after {TEST_TIMEOUT} seconds.",
            "exit_code": -1,
            "execution_time": TEST_TIMEOUT,
            "status": "timeout",
            "message": "Tests timed out",
        }
    except Exception as e:
        return {
            "configured": True,
            "stdout": "",
            "stderr": f"Test error: {str(e)}",
            "exit_code": -1,
            "execution_time": 0,
            "status": "error",
            "message": "Test execution failed",
        }
