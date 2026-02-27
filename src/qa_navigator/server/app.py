"""FastAPI server for QA Navigator.

Exposes REST endpoints to trigger test runs, poll status, and fetch reports.
Supports both immediate (blocking) and background (async job) test execution.

Usage:
    uvicorn qa_navigator.server.app:app --host 0.0.0.0 --port 8080
"""

import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..checklist.generator import ChecklistGenerator
from ..checklist.models import Checklist
from ..computers.playwright_computer import QAPlaywrightComputer
from ..config import settings
from ..orchestrator.engine import TestOrchestrator
from ..report.html import generate_html_report

app = FastAPI(
    title="QA Navigator",
    description="AI-powered exhaustive Visual QA Testing Agent",
    version="1.0.0",
)

# In-memory job store — keyed by job_id
_jobs: dict[str, "JobState"] = {}
_RECORDINGS_DIR = Path("recordings")
_REPORTS_DIR = Path("reports")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class JobState(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = datetime.now(timezone.utc)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    target_url: str = ""
    instructions: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    pass_rate: float = 0.0
    report_path: Optional[str] = None
    recording_path: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        use_enum_values = True


class RunRequest(BaseModel):
    url: str
    instructions: str = "Test this application thoroughly. Check every button, input, link, and interactive element."
    headless: bool = True
    max_items: Optional[int] = None
    min_items: Optional[int] = None


async def _run_job(job_id: str, req: RunRequest) -> None:
    """Background coroutine that runs a full QA test job."""
    job = _jobs[job_id]
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(timezone.utc)
    job.target_url = req.url
    job.instructions = req.instructions

    recording_dir = _RECORDINGS_DIR / job_id
    report_dir = _REPORTS_DIR / job_id
    recording_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Override settings if provided
        if req.max_items is not None:
            import os
            os.environ["QA_NAV_MAX_CHECKLIST_ITEMS"] = str(req.max_items)
        if req.min_items is not None:
            import os
            os.environ["QA_NAV_MIN_CHECKLIST_ITEMS"] = str(req.min_items)

        generator = ChecklistGenerator()
        checklist = await generator.generate_from_instructions(
            instructions=req.instructions,
            target_url=req.url,
        )

        if checklist.total == 0:
            job.status = JobStatus.ERROR
            job.error_message = "No test items generated"
            job.finished_at = datetime.now(timezone.utc)
            return

        computer = QAPlaywrightComputer(
            screen_size=settings.screen_size,
            initial_url=req.url,
            headless=req.headless,
            recording_dir=str(recording_dir),
        )

        orchestrator = TestOrchestrator(computer=computer)

        try:
            result = await orchestrator.run(checklist)
        finally:
            await computer.close()

        # Save HTML report
        report_file = report_dir / f"{result.id}.html"
        recording_path = None
        if hasattr(computer, "video_path") and computer.video_path:
            recording_path = str(computer.video_path)

        generate_html_report(
            checklist=result,
            recording_path=recording_path,
            output_path=report_file,
        )

        job.total = result.total
        job.passed = result.passed
        job.failed = result.failed
        job.errored = result.errored
        job.pass_rate = result.pass_rate
        job.report_path = str(report_file)
        job.recording_path = recording_path
        job.status = JobStatus.DONE

    except Exception as e:
        job.status = JobStatus.ERROR
        job.error_message = str(e)

    finally:
        job.finished_at = datetime.now(timezone.utc)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "model": settings.computer_use_model}


@app.post("/run", status_code=202)
async def start_run(req: RunRequest, background_tasks: BackgroundTasks) -> dict:
    """Start a QA test run as a background job.

    Returns a job_id. Poll /jobs/{job_id} for status and results.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobState(job_id=job_id)
    background_tasks.add_task(_run_job, job_id, req)
    return {"job_id": job_id, "status": "pending", "poll_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JobState:
    """Get the status and results of a test run job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _jobs[job_id]


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
async def get_report(job_id: str) -> str:
    """Get the HTML report for a completed test run."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=202, detail=f"Job status: {job.status}")
    if not job.report_path or not Path(job.report_path).exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return Path(job.report_path).read_text(encoding="utf-8")


@app.get("/jobs")
async def list_jobs() -> list[dict]:
    """List all jobs (most recent first)."""
    jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    return [j.model_dump() for j in jobs[:50]]


@app.post("/run/sync")
async def run_sync(req: RunRequest) -> dict:
    """Run a QA test synchronously (blocking). Returns results when complete.

    Warning: This will block for the duration of the test run (minutes).
    Use /run for production use.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobState(job_id=job_id)
    await _run_job(job_id, req)
    return _jobs[job_id].model_dump()
