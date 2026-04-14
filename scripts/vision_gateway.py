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

from run_google_nano_banana2 import status as google_image_status
from run_google_veo31 import generate_video as generate_google_veo_video
from run_google_veo31 import status as google_video_status
from run_seedance_modelark import generate_video as generate_seedance_video
from run_seedance_modelark import status as seedance_status
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
        "https://visionstudiolab.com",
        "https://www.visionstudiolab.com",
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


def _default_generation_quality() -> str:
    requested = os.environ.get("VISION_GATEWAY_DEFAULT_GENERATION_QUALITY", "auto").strip().lower()
    return requested if requested in {"auto", "fast", "studio", "director"} else "studio"


def _default_generation_provider() -> str:
    requested = os.environ.get("VISION_GATEWAY_DEFAULT_GENERATION_PROVIDER", "auto").strip().lower()
    return requested if requested in {"auto", "seedance", "google", "kling"} else "auto"


def _normalize_quality(value: str | None) -> str:
    if not value:
        return _default_generation_quality()
    normalized = value.strip().lower()
    return normalized if normalized in {"auto", "fast", "studio", "director"} else _default_generation_quality()


def _seedance_model_for_quality(quality: str) -> str | None:
    env_map = {
        "fast": os.environ.get("BYTEPLUS_SEEDANCE_FAST_MODEL", "").strip(),
        "studio": os.environ.get("BYTEPLUS_SEEDANCE_STANDARD_MODEL", "").strip(),
        "director": os.environ.get("BYTEPLUS_SEEDANCE_PREMIUM_MODEL", "").strip(),
    }
    return env_map.get(quality) or None


def _seedance_resolution_for_quality(quality: str) -> str:
    return {
        "fast": "480p",
        "studio": "720p",
        "director": "1080p",
    }.get(quality, "720p")


def _seedance_candidates_for_quality(quality: str, job_id: str) -> list[str]:
    if quality == "auto":
        auto_lanes = ["fast", "studio", "director"]
        seed = int(job_id[-2:], 16) % len(auto_lanes)
        return auto_lanes[seed:] + auto_lanes[:seed]
    return {
        "fast": ["fast", "studio", "director"],
        "studio": ["studio", "director", "fast"],
        "director": ["director", "studio", "fast"],
    }.get(quality, ["studio", "director", "fast"])


def _google_video_model_for_quality(quality: str) -> str | None:
    env_map = {
        "fast": os.environ.get("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview").strip(),
        "studio": os.environ.get("GOOGLE_VEO_STANDARD_MODEL", "veo-3.1-fast-generate-preview").strip(),
        "director": os.environ.get("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview").strip(),
    }
    return env_map.get(quality) or None


def _google_fallback_models_for_quality(quality: str) -> str:
    fallback_map = {
        "fast": os.environ.get("GOOGLE_VEO_FAST_FALLBACK_MODELS", "").strip(),
        "studio": os.environ.get(
            "GOOGLE_VEO_STANDARD_FALLBACK_MODELS",
            os.environ.get("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview"),
        ).strip(),
        "director": os.environ.get(
            "GOOGLE_VEO_PREMIUM_FALLBACK_MODELS",
            ",".join(
                value
                for value in [
                    os.environ.get("GOOGLE_VEO_STANDARD_MODEL", "veo-3.1-fast-generate-preview").strip(),
                    os.environ.get("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview").strip(),
                ]
                if value
            ),
        ).strip(),
    }
    return fallback_map.get(quality, "").strip()


def _google_status() -> dict[str, Any]:
    image_state = google_image_status()
    video_state = google_video_status()
    return {
        "ready": bool(image_state.get("ready") or video_state.get("ready")),
        "image": image_state,
        "video": video_state,
    }



def _select_generation_route(quality: str, job_id: str) -> dict[str, str]:
    seedance_state = seedance_status()
    google_state = _google_status()
    kling_state = kling_session_bridge_status()
    default_provider = _default_generation_provider()
    requested_quality = "studio" if quality == "auto" else quality
    seedance_candidates = _seedance_candidates_for_quality(quality, job_id)

    if default_provider == "google" and google_state["video"].get("ready"):
        model_name = _google_video_model_for_quality(requested_quality)
        if model_name:
            return {
                "provider": "google_veo",
                "quality": requested_quality,
                "model": model_name,
                "fallback_models": _google_fallback_models_for_quality(requested_quality),
                "aspect_ratio": "16:9",
            }

    if default_provider in {"auto", "seedance"} and seedance_state.get("ready"):
        for candidate in seedance_candidates:
            if candidate == "director" and google_state["video"].get("ready"):
                google_model = _google_video_model_for_quality(candidate)
                if google_model:
                    return {
                        "provider": "google_veo",
                        "quality": candidate,
                        "model": google_model,
                        "fallback_models": _google_fallback_models_for_quality(candidate),
                        "aspect_ratio": "16:9",
                    }
            model_name = _seedance_model_for_quality(candidate)
            if model_name:
                return {
                    "provider": "byteplus_seedance",
                    "quality": candidate,
                    "model": model_name,
                    "resolution": _seedance_resolution_for_quality(candidate),
                }

    if default_provider in {"auto", "google"} and google_state["video"].get("ready"):
        model_name = _google_video_model_for_quality(requested_quality)
        if model_name:
            return {
                "provider": "google_veo",
                "quality": requested_quality,
                "model": model_name,
                "fallback_models": _google_fallback_models_for_quality(requested_quality),
                "aspect_ratio": "16:9",
            }

    if default_provider in {"auto", "kling"} and kling_state.get("ready"):
        return {
            "provider": "kling_web_session_bridge",
            "quality": requested_quality,
            "model": os.environ.get("WORLDSIM_KLING_MODEL", "kling-2.6-pro"),
            "resolution": "1080p",
        }

    if default_provider == "seedance":
        raise RuntimeError("Seedance is not ready yet for this Vision deployment.")
    if default_provider == "google":
        raise RuntimeError("Google Veo is not ready yet for this Vision deployment.")
    raise SessionBridgeNotReadyError("No ready generation provider is available for Vision right now.")


APP = FastAPI(title="Vision Gateway", version="0.2.0")
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
    quality: str | None = Field(default=None, min_length=4, max_length=16)


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

    def create(self, prompt: str, quality: str) -> dict[str, Any]:
        with self.lock:
            job_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            job = {
                "id": job_id,
                "prompt": prompt,
                "provider": "auto",
                "quality": quality,
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
    route = _select_generation_route(str(job.get("quality") or "auto"), job_id)
    try:
        JOBS.update(
            job_id,
            provider=route["provider"],
            quality=route["quality"],
            status="preparing",
            message="Preparing generation lane inside Vision.",
        )
        if route["provider"] == "byteplus_seedance":
            JOBS.update(job_id, status="generating", message=f"Generating inside Vision ({route['quality']} lane).")
            output_video = generate_seedance_video(
                prompt=job["prompt"],
                output_dir=output_dir,
                model=route["model"],
                duration=5,
                aspect_ratio="16:9",
                resolution=route["resolution"],
            )
        elif route["provider"] == "google_veo":
            JOBS.update(job_id, status="generating", message=f"Generating inside Vision ({route['quality']} Google lane).")
            output_video = generate_google_veo_video(
                prompt=job["prompt"],
                output_dir=output_dir,
                model=route["model"],
                duration=5,
                aspect_ratio=route["aspect_ratio"],
                fallback_models=route.get("fallback_models", ""),
            )
        else:
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
            message="No ready invisible generation lane is available yet.",
            error=str(exc) if str(exc) else None,
        )
    except RuntimeError as exc:
        JOBS.update(
            job_id,
            status="failed",
            message="The generation lane failed before Vision could import the result.",
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
        "seedance": seedance_status(),
        "google": _google_status(),
        "default_provider": _default_generation_provider(),
        "default_quality": _default_generation_quality(),
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
    job = JOBS.create(payload.prompt.strip(), _normalize_quality(payload.quality))
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
