import os
import subprocess
import tempfile
import shutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.security import get_participant_user
from app.config import TEAM_WORKSPACE_PATH
from app.utils import sanitize_path
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/api/execution", tags=["execution"])


def _get_workspace(team_code: str, challenge_code: str) -> str:
    return os.path.join(os.path.abspath(TEAM_WORKSPACE_PATH), team_code, challenge_code)


class RunRequest(BaseModel):
    command: Optional[str] = None
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

    command = body.command or _detect_run_command(workspace)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        return {
            "stdout": result.stdout[-10000:],
            "stderr": result.stderr[-10000:],
            "exit_code": result.returncode,
            "command": command
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Execution timed out (30 second limit)",
            "exit_code": -1,
            "command": command
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Execution error: {str(e)}",
            "exit_code": -1,
            "command": command
        }


class TestRequest(BaseModel):
    command: Optional[str] = None


@router.post("/test")
async def test_code(body: TestRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = _get_workspace(team_code, challenge_code)

    command = body.command or _detect_test_command(workspace)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        return {
            "stdout": result.stdout[-10000:],
            "stderr": result.stderr[-10000:],
            "exit_code": result.returncode,
            "command": command
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Test execution timed out (60 second limit)",
            "exit_code": -1,
            "command": command
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Test error: {str(e)}",
            "exit_code": -1,
            "command": command
        }


def _detect_run_command(workspace: str) -> str:
    if os.path.exists(os.path.join(workspace, "main.py")):
        return "python main.py"
    if os.path.exists(os.path.join(workspace, "app", "main.py")):
        return "python -m uvicorn app.main:app --host 127.0.0.1 --port 8099"
    if os.path.exists(os.path.join(workspace, "manage.py")):
        return "python manage.py runserver"
    if os.path.exists(os.path.join(workspace, "package.json")):
        return "npm start"
    if os.path.exists(os.path.join(workspace, "pom.xml")):
        return "mvn compile && java -cp target/classes Main"
    return "echo 'No run command detected. Please specify a command.'"


def _detect_test_command(workspace: str) -> str:
    test_files = [f for f in os.listdir(workspace) if f.startswith("test_") and f.endswith(".py")]
    if test_files:
        return f"python -m pytest {' '.join(test_files)} -v"
    if os.path.exists(os.path.join(workspace, "tests")):
        return "python -m pytest tests/ -v"
    if os.path.exists(os.path.join(workspace, "test")):
        return "python -m pytest test/ -v"
    if os.path.exists(os.path.join(workspace, "package.json")):
        return "npm test"
    return "echo 'No test command detected. Please specify a command.'"
