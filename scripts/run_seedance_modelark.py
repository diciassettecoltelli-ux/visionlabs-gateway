from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _first_found(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] not in {None, ""}:
                return obj[key]
        for value in obj.values():
            found = _first_found(value, keys)
            if found not in {None, ""}:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _first_found(item, keys)
            if found not in {None, ""}:
                return found
    return None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_env('BYTEPLUS_API_KEY')}",
        "Content-Type": "application/json",
    }


def _json_request(url: str, *, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=300, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Seedance HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Seedance network error: {exc}") from exc


def _download(url: str, output_video: Path) -> Path:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=600, context=context) as response:
            output_video.write_bytes(response.read())
        return output_video
    except urllib.error.HTTPError:
        fallback_request = urllib.request.Request(
            url,
            headers={k: v for k, v in _headers().items() if k != "Content-Type"},
            method="GET",
        )
        with urllib.request.urlopen(fallback_request, timeout=600, context=context) as response:
            output_video.write_bytes(response.read())
        return output_video


def _status_done(value: str) -> bool:
    return value.lower() in {"done", "completed", "complete", "success", "succeeded", "finished"}


def _status_error(value: str) -> bool:
    return value.lower() in {"error", "failed", "fail", "rejected", "cancelled", "canceled"}


def _task_status(payload: dict[str, Any]) -> str:
    return str(_first_found(payload, ("status", "task_status", "state")) or "submitted")


def _append_prompt_controls(prompt: str, *, resolution: str, duration: int, aspect_ratio: str) -> str:
    controls = [
        f"--resolution {resolution}",
        f"--duration {duration}",
        f"--ratio {aspect_ratio}",
        "--camerafixed false",
    ]
    return f"{prompt.strip()} {' '.join(controls)}".strip()


def status() -> dict[str, Any]:
    base_url = os.getenv("BYTEPLUS_BASE_URL", "").strip()
    api_key = os.getenv("BYTEPLUS_API_KEY", "").strip()
    fast_model = os.getenv("BYTEPLUS_SEEDANCE_FAST_MODEL", "").strip()
    standard_model = os.getenv("BYTEPLUS_SEEDANCE_STANDARD_MODEL", "").strip()
    premium_model = os.getenv("BYTEPLUS_SEEDANCE_PREMIUM_MODEL", "").strip()
    return {
        "ready": bool(base_url and api_key and (fast_model or standard_model or premium_model)),
        "mode": "byteplus_seedance",
        "base_url": base_url,
        "has_api_key": bool(api_key),
        "models": {
            "fast": fast_model or None,
            "studio": standard_model or None,
            "director": premium_model or None,
        },
    }


def generate_video(
    *,
    prompt: str,
    output_dir: str | Path,
    output_video: str | Path | None = None,
    model: str | None = None,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 10,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = Path(output_video) if output_video else output_dir / "seedance_video.mp4"

    base_url = _env("BYTEPLUS_BASE_URL").rstrip("/")
    resolved_model = model or _env("BYTEPLUS_SEEDANCE_STANDARD_MODEL")
    effective_prompt = _append_prompt_controls(
        prompt,
        resolution=resolution,
        duration=duration,
        aspect_ratio=aspect_ratio,
    )

    payload = {
        "model": resolved_model,
        "content": [
            {
                "type": "text",
                "text": effective_prompt,
            }
        ],
    }

    created = _json_request(
        f"{base_url}/contents/generations/tasks",
        method="POST",
        payload=payload,
    )
    task_id = _first_found(created, ("id", "task_id", "job_id", "generation_id"))
    if not task_id:
        raise RuntimeError(f"Seedance create response did not contain a recognizable task id: {created}")

    deadline = time.time() + timeout_seconds
    status_payload = created
    task_status = _task_status(status_payload)
    while not _status_done(task_status):
        if _status_error(task_status):
            raise RuntimeError(f"Seedance task failed with status={task_status}: {status_payload}")
        if time.time() > deadline:
            raise TimeoutError(f"Seedance task {task_id} exceeded timeout.")
        time.sleep(max(1, poll_interval_seconds))
        status_payload = _json_request(
            f"{base_url}/contents/generations/tasks/{task_id}",
            method="GET",
        )
        task_status = _task_status(status_payload)

    video_url = _first_found(status_payload, ("video_url", "url", "download_url", "file_url"))
    if not video_url:
        raise RuntimeError(f"Seedance task completed but no output video URL was present: {status_payload}")

    saved_video = _download(str(video_url), output_video_path)
    metadata = {
        "provider": "byteplus_seedance",
        "model": resolved_model,
        "prompt": prompt,
        "effective_prompt": effective_prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "task_id": str(task_id),
        "output_video": str(saved_video),
        "status_payload": status_payload,
    }
    (output_dir / "seedance_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return saved_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a BytePlus Seedance video generation job.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-video")
    parser.add_argument("--model")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-interval-seconds", type=int, default=10)
    args = parser.parse_args()

    saved_video = generate_video(
        prompt=args.prompt,
        output_dir=args.output_dir,
        output_video=args.output_video,
        model=args.model,
        duration=args.duration,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    print(saved_video)


if __name__ == "__main__":
    main()
