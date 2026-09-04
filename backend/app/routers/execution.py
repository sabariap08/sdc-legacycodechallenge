import os
import uuid
import time
import shutil
import asyncio
import tempfile
from enum import Enum
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.security import get_participant_user
from app.utils import sanitize_path
from app.events import get_current_event
from app.storage import load_workspace_files_from_db, load_evaluator_from_db, has_evaluator

router = APIRouter(prefix="/api/execution", tags=["execution"])

MAX_OUTPUT = 50000


async def _get_team_challenge_code(db, team_code: str):
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        return None
    return team.get("challenge_code")
RUN_TIMEOUT = 30
TEST_TIMEOUT = 60
MAX_FILE_SIZE = 1024 * 1024

LANGUAGES: Dict[str, Dict[str, Any]] = {
    ".py": {
        "name": "Python",
        "compile": None,
        "run": ["python3", "{file}"],
        "ext": ".py",
    },
    ".js": {
        "name": "JavaScript",
        "compile": None,
        "run": ["node", "{file}"],
        "ext": ".js",
    },
    ".c": {
        "name": "C",
        "compile": ["gcc", "{file}", "-o", "{out}", "-lm"],
        "run": ["./{out}"],
        "ext": ".c",
    },
    ".cpp": {
        "name": "C++",
        "compile": ["g++", "-std=c++17", "{file}", "-o", "{out}"],
        "run": ["./{out}"],
        "ext": ".cpp",
    },
    ".cc": {
        "name": "C++",
        "compile": ["g++", "-std=c++17", "{file}", "-o", "{out}"],
        "run": ["./{out}"],
        "ext": ".cc",
    },
    ".java": {
        "name": "Java",
        "compile": ["javac", "{file}"],
        "run": ["java", "-cp", "{dir}", "{class}"],
        "ext": ".java",
    },
    ".go": {
        "name": "Go",
        "compile": None,
        "run": ["go", "run", "{file}"],
        "ext": ".go",
    },
}

class ExecStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    COMPILATION_ERROR = "compilation_error"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"

_jobs: Dict[str, Dict[str, Any]] = {}


async def _hydrate_workspace(team_code: str, challenge_code: str) -> str:
    """Hydrate the team's workspace from Mongo into a private temp directory.

    DB is the source of truth - the temp dir is used only for the execution and
    removed once the run finishes, so no participant data ever persists on disk.
    """
    work_dir = tempfile.mkdtemp(prefix="lcr_exec_")
    loaded = await load_workspace_files_from_db(team_code, challenge_code, work_dir)
    if loaded == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        # Nothing persisted yet for this team workspace
        raise HTTPException(status_code=404, detail="Workspace not found")
    return work_dir


def _find_entry_file(workspace: str, file_path: str = None) -> Optional[str]:
    if file_path:
        full = os.path.join(workspace, file_path)
        if os.path.isfile(full):
            return file_path
        return None
    priorities = ["main.py", "app.py", "run.py", "solution.py", "index.js", "Main.java", "main.go", "main.c", "main.cpp"]
    for name in priorities:
        if os.path.exists(os.path.join(workspace, name)):
            return name
    entries = sorted(os.listdir(workspace))
    for f in entries:
        if os.path.isfile(os.path.join(workspace, f)):
            ext = os.path.splitext(f)[1].lower()
            if ext in LANGUAGES and not f.startswith("test_"):
                return f
    return None


def _resolve_command(template: str, file_path: str) -> str:
    base = os.path.splitext(file_path)[0]
    cmd = template.replace("{file}", file_path)
    cmd = cmd.replace("{class}", os.path.basename(base))
    cmd = cmd.replace("{out}", base)
    cmd = cmd.replace("{dir}", os.path.dirname(file_path) or ".")
    return cmd


def _build_compile_args(lang_config: dict, file_path: str) -> Optional[list]:
    if not lang_config.get("compile"):
        return None
    return [_resolve_command(p, file_path) for p in lang_config["compile"]]


def _build_run_args(lang_config: dict, file_path: str) -> list:
    return [_resolve_command(p, file_path) for p in lang_config["run"]]


def _clean_env() -> dict:
    sensitive = {
        "MONGODB_URI", "DATABASE_NAME", "SECRET_KEY",
        "ADMIN_USERNAME", "ADMIN_PASSWORD",
        "MONGO_URL", "MONGO_DB", "JWT_SECRET",
        "AWS_ACCESS_KEY", "AWS_SECRET_KEY",
        "GITHUB_TOKEN", "GIT_TOKEN",
        "RENDER_", "HEROKU_", "AZURE_", "GCP_",
    }
    env = {}
    for k, v in os.environ.items():
        skip = False
        for s in sensitive:
            if s in k.upper():
                skip = True
                break
        if not skip:
            env[k] = v
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    return env


def _make_job(team_code: str, challenge_code: str, job_type: str) -> dict:
    job_id = str(uuid.uuid4())[:12]
    job = {
        "id": job_id,
        "team_code": team_code,
        "challenge_code": challenge_code,
        "type": job_type,
        "status": ExecStatus.QUEUED.value,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "execution_time": 0,
        "language": "",
        "created_at": time.time(),
        "completed_at": None,
        "process": None,
    }
    _jobs[job_id] = job
    return job


async def _run_compile(job: dict, workspace: str, file_path: str, lang_config: dict) -> bool:
    compile_args = _build_compile_args(lang_config, file_path)
    if not compile_args:
        return True
    env = _clean_env()
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *compile_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        job["process"] = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            job["status"] = ExecStatus.TIMEOUT.value
            job["stderr"] = f"Compilation timed out after {RUN_TIMEOUT} seconds."
            job["execution_time"] = round(time.time() - start, 2)
            return False
        elapsed = round(time.time() - start, 2)
        if proc.returncode != 0:
            job["status"] = ExecStatus.COMPILATION_ERROR.value
            job["stderr"] = (err.decode("utf-8", errors="replace") if err else "Compilation failed")[-MAX_OUTPUT:]
            job["execution_time"] = elapsed
            return False
        return True
    except FileNotFoundError:
        job["status"] = ExecStatus.ERROR.value
        job["stderr"] = f"Compiler not found: {compile_args[0]}"
        job["execution_time"] = round(time.time() - start, 2)
        return False
    except Exception as e:
        job["status"] = ExecStatus.ERROR.value
        job["stderr"] = f"Compilation error: {str(e)}"
        job["execution_time"] = round(time.time() - start, 2)
        return False


async def _run_execute(job: dict, workspace: str, file_path: str, lang_config: dict, stdin_data: str = ""):
    run_args = _build_run_args(lang_config, file_path)
    env = _clean_env()
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *run_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        job["process"] = proc
        stdin_bytes = (stdin_data or "").encode("utf-8", errors="replace")
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=RUN_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            job["status"] = ExecStatus.TIMEOUT.value
            job["stderr"] = f"Execution timed out after {RUN_TIMEOUT} seconds."
            job["execution_time"] = round(time.time() - start, 2)
            return
        elapsed = round(time.time() - start, 2)
        job["stdout"] = (out.decode("utf-8", errors="replace") if out else "")[-MAX_OUTPUT:]
        job["stderr"] = (err.decode("utf-8", errors="replace") if err else "")[-MAX_OUTPUT:]
        job["exit_code"] = proc.returncode
        job["execution_time"] = elapsed
        if proc.returncode == 0:
            job["status"] = ExecStatus.SUCCESS.value
        else:
            job["status"] = ExecStatus.RUNTIME_ERROR.value
    except FileNotFoundError:
        job["status"] = ExecStatus.ERROR.value
        job["stderr"] = f"Runtime not found: {run_args[0]}"
        job["execution_time"] = round(time.time() - start, 2)
    except Exception as e:
        job["status"] = ExecStatus.ERROR.value
        job["stderr"] = f"Execution error: {str(e)}"
        job["execution_time"] = round(time.time() - start, 2)


async def _execute_job(job: dict, workspace: str, file_path: str, lang_config: dict, stdin_data: str = ""):
    job["status"] = ExecStatus.RUNNING.value
    job["language"] = lang_config["name"]
    if lang_config.get("compile"):
        ok = await _run_compile(job, workspace, file_path, lang_config)
        if not ok:
            return
    await _run_execute(job, workspace, file_path, lang_config, stdin_data)


def _has_workspace_tests(workspace: str):
    test_files = sorted(
        f for f in os.listdir(workspace)
        if f.startswith("test_") and f.endswith(".py")
    )
    if test_files:
        return True, test_files
    for sub in ["tests", "test"]:
        if os.path.isdir(os.path.join(workspace, sub)):
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
    challenge_code = await _get_team_challenge_code(db, team_code)
    if not challenge_code:
        raise HTTPException(status_code=403, detail="No challenge assigned to your team")

    workspace = await _hydrate_workspace(team_code, challenge_code)
    try:
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
        lang_config = LANGUAGES.get(ext)
        if not lang_config:
            return {
                "stdout": "",
                "stderr": f"Unsupported file type: {ext}. Supported: {', '.join(sorted(set(l['name'] for l in LANGUAGES.values())))}",
                "exit_code": -1,
                "execution_time": 0,
                "status": "error",
                "language": ext,
            }

        job = _make_job(team_code, challenge_code, "run")
        await _execute_job(job, workspace, entry, lang_config, body.stdin or "")

        _log_execution(team_code, challenge_code, job)

        return {
            "stdout": job["stdout"],
            "stderr": job["stderr"],
            "exit_code": job["exit_code"],
            "execution_time": job["execution_time"],
            "status": job["status"],
            "language": job["language"],
            "job_id": job["id"],
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


class TestRequest(BaseModel):
    stdin: Optional[str] = None


@router.post("/test")
async def test_code(body: TestRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    challenge_code = await _get_team_challenge_code(db, team_code)
    if not challenge_code:
        raise HTTPException(status_code=403, detail="No challenge assigned to your team")

    workspace = await _hydrate_workspace(team_code, challenge_code)
    try:
        has_eval, eval_files = await has_evaluator(challenge_code)
        if has_eval:
            return await _run_evaluator_tests(workspace, challenge_code, team_code)

        has_ws, ws_test_files = _has_workspace_tests(workspace)
        if has_ws:
            return await _run_pytest(workspace, ws_test_files)

        return {
            "configured": False,
            "message": "Automated testing is not configured for this challenge.",
            "results": [],
            "exit_code": 0,
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@router.post("/cancel/{job_id}")
async def cancel_execution(job_id: str, user=Depends(get_participant_user)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["team_code"] != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your job")
    proc = job.get("process")
    if proc and proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
    job["status"] = ExecStatus.CANCELLED.value
    job["completed_at"] = time.time()
    return {"message": "Execution cancelled", "job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, user=Depends(get_participant_user)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["team_code"] != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your job")
    proc = job.get("process")
    if proc and job["status"] == ExecStatus.RUNNING.value and proc.returncode is not None:
        out = proc.stdout
        err = proc.stderr
        job["stdout"] = (out.decode("utf-8", errors="replace") if out else "")[-MAX_OUTPUT:]
        job["stderr"] = (err.decode("utf-8", errors="replace") if err else "")[-MAX_OUTPUT:]
        job["exit_code"] = proc.returncode
        job["status"] = ExecStatus.SUCCESS.value if proc.returncode == 0 else ExecStatus.RUNTIME_ERROR.value
        job["completed_at"] = time.time()
    return {
        "id": job["id"],
        "status": job["status"],
        "stdout": job["stdout"],
        "stderr": job["stderr"],
        "exit_code": job["exit_code"],
        "execution_time": job["execution_time"],
        "language": job["language"],
    }


def _log_execution(team_code: str, challenge_code: str, job: dict):
    try:
        logger = logging.getLogger("execution")
        logger.info(
            "EXEC job=%s team=%s challenge=%s lang=%s status=%s time=%.2fs exit=%s",
            job["id"], team_code, challenge_code,
            job["language"], job["status"],
            job["execution_time"], job["exit_code"]
        )
    except Exception:
        pass


async def _run_evaluator_tests(workspace, challenge_code, team_code):
    """Hydrate evaluator tests from DB into a temp dir and run them."""
    evaluator_dir = tempfile.mkdtemp(prefix="lcr_eval_")
    results = []
    env = _clean_env()
    try:
        loaded = await load_evaluator_from_db(challenge_code, evaluator_dir)
        test_files = sorted(f for f in os.listdir(evaluator_dir) if f.startswith("test_") and f.endswith(".py"))
        if loaded == 0 or not test_files:
            return {
                "configured": True,
                "message": "Evaluator tests no longer available.",
                "results": [],
                "exit_code": 0,
                "total": 0,
                "passed": 0,
            }
        for i, tf in enumerate(sorted(test_files), 1):
            test_path = os.path.join(evaluator_dir, tf)
            env_run = env.copy()
            env_run["WORKSPACE_PATH"] = workspace
            env_run["CHALLENGE_CODE"] = challenge_code
            env_run["TEAM_CODE"] = team_code
            start = time.time()
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python3", test_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workspace,
                    env=env_run,
                )
                try:
                    out, err = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    results.append({"test": f"Test {i}", "passed": False, "reason": "Test timed out", "time": TEST_TIMEOUT})
                    continue
                elapsed = round(time.time() - start, 2)
                passed = proc.returncode == 0
                results.append({
                    "test": f"Test {i}",
                    "passed": passed,
                    "reason": (out.decode("utf-8", errors="replace")[-500:].strip() if not passed else ""),
                    "time": elapsed,
                })
            except Exception as e:
                results.append({"test": f"Test {i}", "passed": False, "reason": str(e), "time": 0})
    finally:
        shutil.rmtree(evaluator_dir, ignore_errors=True)

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
    cmd = ["python3", "-m", "pytest"]
    if test_files:
        cmd += test_files
    else:
        cmd.append("tests/")
    cmd.append("-v")

    env = _clean_env()
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "configured": True,
                "stdout": "",
                "stderr": f"Test execution timed out after {TEST_TIMEOUT} seconds.",
                "exit_code": -1,
                "execution_time": TEST_TIMEOUT,
                "status": "timeout",
                "message": "Tests timed out",
            }
        elapsed = round(time.time() - start, 2)
        stdout = (out.decode("utf-8", errors="replace") if out else "")[-MAX_OUTPUT:]
        stderr = (err.decode("utf-8", errors="replace") if err else "")[-MAX_OUTPUT:]
        return {
            "configured": True,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "execution_time": elapsed,
            "status": "success" if proc.returncode == 0 else "tests_failed",
            "message": "All tests passed" if proc.returncode == 0 else "Some tests failed",
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