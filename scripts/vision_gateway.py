from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vision_kling_session_bridge import SessionBridgeNotReadyError
from vision_kling_session_bridge import generate as generate_kling_session_bridge
from vision_kling_session_bridge import prepare as prepare_kling_session_bridge
from vision_kling_session_bridge import status as kling_session_bridge_status


def _resolve_default_vision_root() -> Path:
    candidates = [
        os.environ.get("VISION_GATEWAY_VISION_ROOT", "").strip(),
        str(Path(__file__).resolve().parents[1] / "vision"),
        "/Users/a1/vision",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return Path(candidates[1]).expanduser()


def _cors_allow_origins() -> list[str]:
    defaults = [
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "https://visionlabs.cloud",
        "https://www.visionlabs.cloud",
    ]
    configured = os.environ.get("VISION_GATEWAY_CORS_ALLOW_ORIGINS", "").strip()
    if not configured:
        return defaults
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or defaults


def _public_output_url(job_id: str, filename: str) -> str:
    public_base = os.environ.get("VISION_GATEWAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    relative_path = f"/generated/{job_id}/{filename}"
    if public_base:
        return f"{public_base}{relative_path}"
    return relative_path


APP = FastAPI(title="Vision Gateway", version="0.1.0")
APP.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VISION_ROOT = _resolve_default_vision_root()
RUNTIME_ROOT = VISION_ROOT / ".runtime"
JOBS_FILE = RUNTIME_ROOT / "jobs.json"
OUTPUT_ROOT = VISION_ROOT / "generated"
DISABLE_FILE = RUNTIME_ROOT / "gateway.disabled"

for path in (RUNTIME_ROOT, OUTPUT_ROOT):
    path.mkdir(parents=True, exist_ok=True)

APP.mount("/generated", StaticFiles(directory=str(OUTPUT_ROOT)), name="generated")


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=5000)


class JobsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            self.jobs = json.loads(self.path.read_text(encoding="utf-8"))
            for job in self.jobs.values():
                if job.get("status") in {"queued", "preparing", "generating", "downloading"}:
                    job["status"] = "failed"
                    job["message"] = "Generation was interrupted before Vision could import the result."
                    job["error"] = "Gateway restarted before completion."

    def save(self) -> None:
        self.path.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")

    def create(self, prompt: str) -> dict[str, Any]:
        with self.lock:
            job_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            job = {
                "id": job_id,
                "prompt": prompt,
                "provider": "kling_web_session_bridge",
                "status": "queued",
                "message": "Queued inside Vision.",
                "created_at": now,
                "updated_at": now,
                "output_url": None,
                "output_path": None,
                "error": None,
            }
            self.jobs[job_id] = job
            self.save()
            return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            job.update(changes)
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
            return dict(job)


JOBS = JobsStore(JOBS_FILE)
QUEUE: queue.Queue[str] = queue.Queue()


def _process_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    output_dir = OUTPUT_ROOT / job_id
    try:
        JOBS.update(job_id, status="preparing", message="Preparing invisible Kling session bridge.")
        lane_state = kling_session_bridge_status()
        if not lane_state.get("ready"):
            prepare_kling_session_bridge()
        JOBS.update(job_id, status="generating", message="Generating inside Vision.")
        output_video = generate_kling_session_bridge(
            prompt=job["prompt"],
            output_dir=output_dir,
        )
        JOBS.update(job_id, status="downloading", message="Finishing and importing result.")
        JOBS.update(
            job_id,
            status="ready",
            message="Ready.",
            output_path=str(output_video),
            output_url=_public_output_url(job_id, output_video.name),
            error=None,
        )
    except SessionBridgeNotReadyError as exc:
        JOBS.update(
            job_id,
            status="failed",
            message="The invisible Kling session bridge is not ready yet.",
            error=str(exc) if str(exc) else None,
        )
    except RuntimeError as exc:
        JOBS.update(
            job_id,
            status="failed",
            message="The Kling session bridge failed before Vision could import the result.",
            error=str(exc) if str(exc) else None,
        )
    except Exception as exc:
        recovered = next(output_dir.glob("*.mp4"), None) if output_dir.exists() else None
        if recovered and recovered.exists():
            JOBS.update(
                job_id,
                status="ready",
                message="Ready.",
                output_path=str(recovered),
                output_url=_public_output_url(job_id, recovered.name),
                error=None,
            )
            return
        JOBS.update(
            job_id,
            status="failed",
            message="Generation failed before Vision could import the result.",
            error=str(exc),
        )


def _worker_loop() -> None:
    while True:
        job_id = QUEUE.get()
        try:
            try:
                _process_job(job_id)
            except Exception as exc:
                try:
                    JOBS.update(
                        job_id,
                        status="failed",
                        message="Generation failed before Vision could import the result.",
                        error=str(exc),
                    )
                except Exception:
                    pass
        finally:
            QUEUE.task_done()


WORKER = threading.Thread(target=_worker_loop, daemon=True)
WORKER.start()


@APP.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@APP.get("/api/engine/status")
def engine_status() -> dict[str, Any]:
    return {
        "kling_session_bridge": kling_session_bridge_status(),
    }


@APP.post("/api/engine/prepare")
def engine_prepare() -> JSONResponse:
    try:
        state = prepare_kling_session_bridge()
        return JSONResponse({"ok": state.get("ready", False), "message": state.get("message", "Kling session bridge inspected.")})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)})


@APP.post("/api/jobs")
def create_job(payload: CreateJobRequest) -> dict[str, Any]:
    job = JOBS.create(payload.prompt.strip())
    QUEUE.put(job["id"])
    return job


@APP.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


if (VISION_ROOT / "assets").exists():
    APP.mount("/assets", StaticFiles(directory=str(VISION_ROOT / "assets")), name="assets")


if (VISION_ROOT / "index.html").exists():
    @APP.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(VISION_ROOT / "index.html")


    @APP.get("/favicon.svg", include_in_schema=False)
    def frontend_favicon() -> FileResponse:
        return FileResponse(VISION_ROOT / "favicon.svg")


    @APP.get("/style.css", include_in_schema=False)
    def frontend_style() -> FileResponse:
        return FileResponse(VISION_ROOT / "style.css")


    @APP.get("/app.js", include_in_schema=False)
    def frontend_app() -> FileResponse:
        return FileResponse(VISION_ROOT / "app.js")


    @APP.get("/vision-config.js", include_in_schema=False)
    def frontend_config() -> FileResponse:
        return FileResponse(VISION_ROOT / "vision-config.js")


def main() -> None:
    if DISABLE_FILE.exists():
        raise SystemExit("Vision gateway is disabled on this workstation.")
    host = os.environ.get("VISION_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("VISION_GATEWAY_PORT", "8787")))
    uvicorn.run(APP, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
