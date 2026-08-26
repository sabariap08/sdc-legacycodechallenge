import os
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.security import get_participant_user
from app.config import TEAM_WORKSPACE_PATH, EVALUATOR_PATH
from app.utils import compute_event_status
from datetime import datetime

router = APIRouter(prefix="/api/submission", tags=["submission"])

TEST_TIMEOUT = 60

@router.post("/submit")
async def submit_final(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings)

    if computed not in ("ONGOING", "COMPLETED"):
        raise HTTPException(status_code=403, detail="Event has not started yet")

    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = os.path.join(os.path.abspath(TEAM_WORKSPACE_PATH), team_code, challenge_code)

    if not os.path.exists(workspace):
        raise HTTPException(status_code=404, detail="Workspace not found")

    existing = await db.submissions.find_one(
        {"team_code": team_code, "challenge_code": challenge_code},
        sort=[("submitted_at", -1)]
    )

    if existing:
        raise HTTPException(status_code=400, detail="Submission already exists.")

    evaluation_result = await _run_evaluation(workspace, challenge_code, team_code)
    score = sum(1 for r in evaluation_result.get("results", []) if r.get("passed", False))
    total = len(evaluation_result.get("results", []))

    submission_doc = {
        "team_code": team_code,
        "challenge_code": challenge_code,
        "submitted_at": datetime.utcnow(),
        "status": "evaluated",
        "score": score,
        "total": total,
        "evaluation_result": evaluation_result,
        "version": 1,
        "auto_submitted": False,
    }

    await db.submissions.insert_one(submission_doc)

    await db.audit_logs.insert_one({
        "action": "submission",
        "actor": team_code,
        "details": f"Score: {score}/{total} (v1)",
        "timestamp": datetime.utcnow()
    })

    return {
        "message": "Submission evaluated",
        "score": score,
        "total": total,
        "version": 1,
        "results": evaluation_result.get("results", [])
    }

@router.post("/auto-submit")
async def auto_submit(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        return {"message": "No allocation to auto-submit"}

    challenge_code = alloc["challenge_code"]
    workspace = os.path.join(os.path.abspath(TEAM_WORKSPACE_PATH), team_code, challenge_code)

    existing = await db.submissions.find_one(
        {"team_code": team_code, "challenge_code": challenge_code},
        sort=[("submitted_at", -1)]
    )

    if existing:
        return {"message": "Already has a submission, no auto-submit needed"}

    if not os.path.exists(workspace):
        return {"message": "No workspace to auto-submit"}

    evaluation_result = await _run_evaluation(workspace, challenge_code, team_code)
    score = sum(1 for r in evaluation_result.get("results", []) if r.get("passed", False))
    total = len(evaluation_result.get("results", []))

    submission_doc = {
        "team_code": team_code,
        "challenge_code": challenge_code,
        "submitted_at": datetime.utcnow(),
        "status": "evaluated",
        "score": score,
        "total": total,
        "evaluation_result": evaluation_result,
        "version": 1,
        "auto_submitted": True,
    }

    await db.submissions.insert_one(submission_doc)

    await db.audit_logs.insert_one({
        "action": "auto_submit",
        "actor": team_code,
        "details": f"Auto-submitted. Score: {score}/{total}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Auto-submitted", "score": score, "total": total}

@router.get("/status")
async def submission_status(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    sub = await db.submissions.find_one(
        {"team_code": team_code},
        sort=[("submitted_at", -1)]
    )
    if not sub:
        return {"submitted": False}

    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings)

    return {
        "submitted": True,
        "score": sub.get("score", 0),
        "total": sub.get("total", 0),
        "submitted_at": sub.get("submitted_at"),
        "version": sub.get("version", 1),
        "auto_submitted": sub.get("auto_submitted", False),
        "results": sub.get("evaluation_result", {}).get("results", []),
        "event_status": computed,
    }

def _clean_env():
    sensitive = {
        "MONGODB_URI", "DATABASE_NAME", "SECRET_KEY",
        "ADMIN_USERNAME", "ADMIN_PASSWORD",
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
    return env

async def _run_evaluation(workspace, challenge_code, team_code):
    evaluator_dir = os.path.join(EVALUATOR_PATH, challenge_code)

    if not os.path.exists(evaluator_dir):
        return {
            "results": [{"test": f"Bug {i+1}", "passed": False, "reason": "No evaluator available"}
                        for i in range(10)]
        }

    test_files = []
    for f in os.listdir(evaluator_dir):
        if f.startswith("test_") and f.endswith(".py"):
            test_files.append(f)
    test_files.sort()

    if not test_files:
        return {
            "results": [{"test": f"Bug {i+1}", "passed": False, "reason": "No tests found"}
                        for i in range(10)]
        }

    env = _clean_env()
    results = []
    for i, test_file in enumerate(test_files, 1):
        test_path = os.path.join(evaluator_dir, test_file)
        env_run = env.copy()
        env_run["WORKSPACE_PATH"] = workspace
        env_run["CHALLENGE_CODE"] = challenge_code
        env_run["TEAM_CODE"] = team_code

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
                results.append({"test": f"Bug {i}", "passed": False, "reason": "Test timed out"})
                continue

            passed = proc.returncode == 0
            stdout = out.decode("utf-8", errors="replace") if out else ""
            reason = stdout[-500:].strip() if not passed else ""
            results.append({
                "test": f"Bug {i}",
                "passed": passed,
                "reason": reason if not passed else "PASS",
            })
        except Exception as e:
            results.append({"test": f"Bug {i}", "passed": False, "reason": str(e)})

    return {"results": results}
