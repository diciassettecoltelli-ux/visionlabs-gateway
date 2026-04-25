from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
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
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def _base64_urlsafe(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def _jwt() -> str:
    access_key = _env("KLING_ACCESS_KEY")
    secret_key = _env("KLING_SECRET_KEY")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": access_key,
        "exp": now + int(os.getenv("KLING_API_JWT_TTL_SECONDS", "1800")),
        "nbf": now - 5,
    }
    signing_input = ".".join(
        [
            _base64_urlsafe(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _base64_urlsafe(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(secret_key.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_base64_urlsafe(signature)}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_jwt()}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return os.getenv("KLING_API_BASE_URL", "https://api-singapore.klingai.com").strip().rstrip("/")


def _json_request(path_or_url: str, *, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else f"{_base_url()}{path_or_url}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=300, context=context) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Kling API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Kling API network error: {exc}") from exc


def _download(url: str, output_video: Path) -> Path:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=600, context=context) as response:
        output_video.write_bytes(response.read())
    return output_video


def _first_found(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in {None, ""}:
                return value
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


def _task_status(payload: dict[str, Any]) -> str:
    return str(_first_found(payload, ("task_status", "status", "state")) or "submitted").lower()


def _status_done(value: str) -> bool:
    return value.lower() in {"succeed", "succeeded", "success", "done", "completed", "complete", "finished"}


def _status_error(value: str) -> bool:
    return value.lower() in {"failed", "fail", "error", "rejected", "cancelled", "canceled"}


def _safe_duration(value: int) -> int:
    try:
        requested = int(value)
    except Exception:
        requested = 5
    if requested <= 3:
        return 3
    if requested <= 5:
        return 5
    if requested <= 10:
        return 10
    return 15


def _mode_for_generation(*, resolution: str, sound_enabled: bool, quality: str | None = None) -> str:
    configured = os.getenv("KLING_API_VIDEO_MODE", "").strip().lower()
    if configured in {"std", "pro"}:
        return configured
    normalized_quality = str(quality or "").strip().lower()
    if normalized_quality == "director" or sound_enabled or resolution in {"1080p", "4k"}:
        return "pro"
    return "std"


def _create_payload(
    *,
    prompt: str,
    model: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    sound_enabled: bool,
    quality: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_name": model,
        "prompt": prompt,
        "duration": str(_safe_duration(duration)),
        "aspect_ratio": aspect_ratio,
        "mode": _mode_for_generation(resolution=resolution, sound_enabled=sound_enabled, quality=quality),
    }
    negative_prompt = os.getenv("KLING_API_NEGATIVE_PROMPT", "").strip()
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    # Kling's public video API does not consistently expose resolution/audio knobs across models.
    # Keep Vision's requested controls in metadata for traceability without sending unsupported fields.
    passthrough_raw = os.getenv("KLING_API_EXTRA_PAYLOAD_JSON", "").strip()
    if passthrough_raw:
        try:
            extra = json.loads(passthrough_raw)
            if isinstance(extra, dict):
                payload.update(extra)
        except json.JSONDecodeError as exc:
            raise RuntimeError("KLING_API_EXTRA_PAYLOAD_JSON is not valid JSON.") from exc
    return payload


def status() -> dict[str, Any]:
    access_key = os.getenv("KLING_ACCESS_KEY", "").strip()
    secret_key = os.getenv("KLING_SECRET_KEY", "").strip()
    model = os.getenv("KLING_API_VIDEO_MODEL", "kling-v2-1").strip()
    return {
        "ready": bool(access_key and secret_key),
        "mode": "kling_api",
        "base_url": _base_url(),
        "has_access_key": bool(access_key),
        "has_secret_key": bool(secret_key),
        "model": model or None,
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
    sound_enabled: bool = False,
    quality: str | None = None,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 10,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = Path(output_video) if output_video else output_dir / "kling_api_video.mp4"

    resolved_model = model or _env("KLING_API_VIDEO_MODEL", "kling-v2-1")
    create_path = os.getenv("KLING_API_TEXT_TO_VIDEO_PATH", "/v1/videos/text2video").strip()
    query_template = os.getenv("KLING_API_TEXT_TO_VIDEO_QUERY_PATH_TEMPLATE", "/v1/videos/text2video/{task_id}").strip()
    payload = _create_payload(
        prompt=prompt,
        model=resolved_model,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        sound_enabled=sound_enabled,
        quality=quality,
    )

    created = _json_request(create_path, method="POST", payload=payload)
    task_id = _first_found(created, ("task_id", "id", "job_id", "generation_id"))
    if not task_id:
        raise RuntimeError(f"Kling API create response did not include a task id: {created}")

    deadline = time.time() + timeout_seconds
    status_payload = created
    current_status = _task_status(status_payload)
    while not _status_done(current_status):
        if _status_error(current_status):
            raise RuntimeError(f"Kling API task failed with status={current_status}: {status_payload}")
        if time.time() > deadline:
            raise TimeoutError(f"Kling API task {task_id} exceeded timeout.")
        time.sleep(max(1, poll_interval_seconds))
        status_payload = _json_request(query_template.format(task_id=task_id), method="GET")
        current_status = _task_status(status_payload)

    video_url = _first_found(status_payload, ("url", "video_url", "download_url", "file_url", "resource_url"))
    if not video_url:
        raise RuntimeError(f"Kling API task completed but no output video URL was present: {status_payload}")

    saved_video = _download(str(video_url), output_video_path)
    metadata = {
        "provider": "kling_api",
        "model": resolved_model,
        "prompt": prompt,
        "duration": _safe_duration(duration),
        "aspect_ratio": aspect_ratio,
        "resolution_requested": resolution,
        "sound_enabled_requested": bool(sound_enabled),
        "quality": quality,
        "task_id": str(task_id),
        "output_video": str(saved_video),
        "create_payload": payload,
        "create_response": created,
        "status_payload": status_payload,
    }
    (output_dir / "kling_api_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return saved_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Kling official API video generation job.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-video")
    parser.add_argument("--model")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--sound-enabled", action="store_true")
    parser.add_argument("--quality")
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
        sound_enabled=args.sound_enabled,
        quality=args.quality,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    print(saved_video)


if __name__ == "__main__":
    main()
