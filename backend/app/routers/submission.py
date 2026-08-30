import os
import asyncio
import shutil
import tempfile
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.security import get_participant_user
from app.events import get_current_event, compute_event_status
from app.storage import load_workspace_files_from_db, load_evaluator_from_db
from datetime import datetime

router = APIRouter(prefix="/api/submission", tags=["submission"])

TEST_TIMEOUT = 60


async def _hydrate_workspace(team_code: str, challenge_code: str) -> str:
    work_dir = tempfile.mkdtemp(prefix="lcr_sub_")
    loaded = await load_workspace_files_from_db(team_code, challenge_code, work_dir)
    if loaded == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail="Workspace not found")
    return work_dir


@router.post("/submit")
async def submit_final(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    event = await get_current_event()
    computed = compute_event_status(event) if event else "DRAFT"
    event_id = event["event_id"] if event else None

    if computed not in ("ONGOING", "COMPLETED"):
        raise HTTPException(status_code=403, detail="Event has not started yet")

    alloc = await db.allocations.find_one({"team_code": team_code, "event_id": event_id}) if event_id else None
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]

    existing = await db.submissions.find_one(
        {"team_code": team_code, "challenge_code": challenge_code, "event_id": event_id},
        sort=[("submitted_at", -1)]
    )

    if existing:
        raise HTTPException(status_code=400, detail="Submission already exists.")

    workspace = await _hydrate_workspace(team_code, challenge_code)
    try:
        evaluation_result = await _run_evaluation(workspace, challenge_code, team_code)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    score = sum(1 for r in evaluation_result.get("results", []) if r.get("passed", False))
    total = len(evaluation_result.get("results", []))

    submission_doc = {
        "team_code": team_code,
        "challenge_code": challenge_code,
        "event_id": event_id,
        "submitted_at": datetime.utcnow(),
        "status": "evaluated",
        "score": score,
        "total": total,
        "evaluation_result": evaluation_result,
        "auto_submitted": False,
    }

    await db.submissions.insert_one(submission_doc)

    await db.audit_logs.insert_one({
        "action": "submission",
        "actor": team_code,
        "details": f"Score: {score}/{total}",
        "timestamp": datetime.utcnow()
    })

    return {
        "message": "Submission evaluated",
        "score": score,
        "total": total,
        "results": evaluation_result.get("results", [])
    }


@router.post("/auto-submit")
async def auto_submit(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    event = await get_current_event()
    event_id = event["event_id"] if event else None

    alloc = await db.allocations.find_one({"team_code": team_code, "event_id": event_id}) if event_id else None
    if not alloc or not alloc.get("released"):
        return {"message": "No allocation to auto-submit"}

    challenge_code = alloc["challenge_code"]

    existing = await db.submissions.find_one(
        {"team_code": team_code, "challenge_code": challenge_code, "event_id": event_id},
        sort=[("submitted_at", -1)]
    )

    if existing:
        return {"message": "Already has a submission, no auto-submit needed"}

    try:
        workspace = await _hydrate_workspace(team_code, challenge_code)
    except HTTPException:
        return {"message": "No workspace to auto-submit"}

    try:
        evaluation_result = await _run_evaluation(workspace, challenge_code, team_code)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    score = sum(1 for r in evaluation_result.get("results", []) if r.get("passed", False))
    total = len(evaluation_result.get("results", []))

    submission_doc = {
        "team_code": team_code,
        "challenge_code": challenge_code,
        "event_id": event_id,
        "submitted_at": datetime.utcnow(),
        "status": "evaluated",
        "score": score,
        "total": total,
        "evaluation_result": evaluation_result,
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
    event = await get_current_event()
    event_id = event["event_id"] if event else None
    sub = await db.submissions.find_one(
        {"team_code": team_code, "event_id": event_id} if event_id else {"team_code": team_code},
        sort=[("submitted_at", -1)]
    )
    if not sub:
        return {"submitted": False}

    computed = compute_event_status(event) if event else "DRAFT"

    return {
        "submitted": True,
        "score": sub.get("score", 0),
        "total": sub.get("total", 0),
        "submitted_at": sub.get("submitted_at"),
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
    evaluator_dir = tempfile.mkdtemp(prefix="lcr_eval_")
    try:
        loaded = await load_evaluator_from_db(challenge_code, evaluator_dir)
        test_files = sorted(f for f in os.listdir(evaluator_dir) if f.startswith("test_") and f.endswith(".py"))

        if loaded == 0 or not test_files:
            return {
                "results": [{"test": f"Bug {i+1}", "passed": False, "reason": "No evaluator available"}
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
    finally:
        shutil.rmtree(evaluator_dir, ignore_errors=True)