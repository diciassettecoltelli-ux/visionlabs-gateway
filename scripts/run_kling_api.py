from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi


DEFAULT_KLING_API_VIDEO_MODEL = "kling-v3-omni"
DEFAULT_KLING_API_FALLBACK_VIDEO_MODEL = "kling-v2-1-master"
DEFAULT_KLING_API_NATIVE_15_MODELS = {
    "kling-v3-omni",
}


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


def _normalize_model_name(model: str | None) -> str:
    return str(model or "").strip().lower()


def _primary_video_model(model: str | None = None) -> str:
    return str(model or os.getenv("KLING_API_VIDEO_MODEL", DEFAULT_KLING_API_VIDEO_MODEL)).strip()


def _fallback_video_model() -> str:
    return os.getenv("KLING_API_FALLBACK_VIDEO_MODEL", DEFAULT_KLING_API_FALLBACK_VIDEO_MODEL).strip()


def _native_15_models() -> set[str]:
    configured = os.getenv("KLING_API_NATIVE_15_MODELS", "").strip()
    if not configured:
        return set(DEFAULT_KLING_API_NATIVE_15_MODELS)
    return {item.strip().lower() for item in configured.split(",") if item.strip()}


def _model_supports_native_15(model: str | None) -> bool:
    normalized = _normalize_model_name(model)
    return normalized in _native_15_models() or "omni" in normalized


def _duration_segments(duration: int, *, model: str | None = None) -> list[int]:
    safe_duration = _safe_duration(duration)
    if safe_duration <= 10 or _model_supports_native_15(model):
        return [safe_duration]
    return [5, 5, 5]


def _is_model_unsupported_error(exc: Exception) -> bool:
    return "model is not supported" in str(exc).lower()


def _is_duration_unsupported_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duration" in message and any(
        phrase in message for phrase in ("not supported", "unsupported", "invalid", "must be")
    )


def _ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY", "").strip()
    if configured:
        return configured
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg is required to assemble Kling API 15-second videos.") from exc


def _concat_file_line(path: Path) -> str:
    absolute_path = path.resolve()
    return "file '" + str(absolute_path).replace("'", "'\\''") + "'"


def _concat_videos(segment_paths: list[Path], output_video: Path, output_dir: Path) -> Path:
    if len(segment_paths) < 2:
        return segment_paths[0]
    ffmpeg = _ffmpeg_executable()
    output_video.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_dir / "kling_api_concat.txt"
    concat_list.write_text("\n".join(_concat_file_line(path) for path in segment_paths) + "\n", encoding="utf-8")
    copy_command = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    try:
        subprocess.run(copy_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return output_video
    except subprocess.CalledProcessError:
        reencode_command = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        subprocess.run(reencode_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return output_video


def _segment_prompt(prompt: str, *, segment_index: int, segment_count: int, duration: int) -> str:
    if segment_count <= 1:
        return prompt
    return (
        f"{prompt}\n\n"
        f"Vision continuity segment {segment_index}/{segment_count}: generate only this {duration}-second part "
        "of one continuous final video. Keep the same subject, identity, location, lighting, camera language, "
        "color grade, and cinematic style. No text, no logo, no subtitles."
    )


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
    model = _primary_video_model()
    fallback_model = _fallback_video_model()
    return {
        "ready": bool(access_key and secret_key),
        "mode": "kling_api",
        "base_url": _base_url(),
        "has_access_key": bool(access_key),
        "has_secret_key": bool(secret_key),
        "model": model or None,
        "fallback_model": fallback_model or None,
        "native_15s": _model_supports_native_15(model),
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
    resolved_model = _primary_video_model(model)
    segments = _duration_segments(duration, model=resolved_model)

    try:
        return _generate_video_with_segments(
            prompt=prompt,
            output_dir=output_dir,
            output_video_path=output_video_path,
            model=resolved_model,
            duration=duration,
            segments=segments,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            sound_enabled=sound_enabled,
            quality=quality,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    except RuntimeError as exc:
        fallback_model = _fallback_video_model()
        can_fallback_model = bool(fallback_model and _normalize_model_name(fallback_model) != _normalize_model_name(resolved_model))
        should_try_split = _safe_duration(duration) > 10 and len(segments) == 1 and _is_duration_unsupported_error(exc)
        should_try_model_fallback = _is_model_unsupported_error(exc) and can_fallback_model
        if not should_try_model_fallback and not should_try_split:
            raise

        fallback_error = {
            "provider": "kling_api",
            "attempted_model": resolved_model,
            "fallback_model": fallback_model if should_try_model_fallback else resolved_model,
            "duration": _safe_duration(duration),
            "error": str(exc),
            "fallback_reason": "model_unsupported" if should_try_model_fallback else "duration_unsupported",
        }
        (output_dir / "kling_api_fallback.json").write_text(json.dumps(fallback_error, indent=2) + "\n", encoding="utf-8")
        fallback_generation_model = fallback_model if should_try_model_fallback else resolved_model
        fallback_segments = (
            [5, 5, 5]
            if should_try_split
            else _duration_segments(duration, model=fallback_generation_model)
        )
        return _generate_video_with_segments(
            prompt=prompt,
            output_dir=output_dir,
            output_video_path=output_video_path,
            model=fallback_generation_model,
            duration=duration,
            segments=fallback_segments,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            sound_enabled=sound_enabled,
            quality=quality,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            fallback_from=fallback_error,
        )


def _generate_video_with_segments(
    *,
    prompt: str,
    output_dir: Path,
    output_video_path: Path,
    model: str,
    duration: int,
    segments: list[int],
    aspect_ratio: str,
    resolution: str,
    sound_enabled: bool,
    quality: str | None,
    timeout_seconds: int,
    poll_interval_seconds: int,
    fallback_from: dict[str, Any] | None = None,
) -> Path:
    if len(segments) > 1:
        segment_paths: list[Path] = []
        segment_metadata: list[dict[str, Any]] = []
        for index, segment_duration in enumerate(segments, start=1):
            segment_path = output_dir / f"kling_api_segment_{index:02d}.mp4"
            segment_prompt = _segment_prompt(
                prompt,
                segment_index=index,
                segment_count=len(segments),
                duration=segment_duration,
            )
            saved_segment, metadata = _generate_video_task(
                prompt=segment_prompt,
                output_dir=output_dir,
                output_video_path=segment_path,
                metadata_path=output_dir / f"kling_api_segment_{index:02d}_metadata.json",
                model=model,
                duration=segment_duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                sound_enabled=sound_enabled,
                quality=quality,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            metadata["segment_index"] = index
            metadata["segment_count"] = len(segments)
            segment_paths.append(saved_segment)
            segment_metadata.append(metadata)

        saved_video = _concat_videos(segment_paths, output_video_path, output_dir)
        combined_metadata = {
            "provider": "kling_api",
            "duration_strategy": "split_5s_segments",
            "model": model,
            "prompt": prompt,
            "duration": sum(segments),
            "segment_durations": segments,
            "aspect_ratio": aspect_ratio,
            "resolution_requested": resolution,
            "sound_enabled_requested": bool(sound_enabled),
            "quality": quality,
            "segments": segment_metadata,
            "output_video": str(saved_video),
        }
        if fallback_from:
            combined_metadata["fallback_from"] = fallback_from
        (output_dir / "kling_api_metadata.json").write_text(
            json.dumps(combined_metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        return saved_video

    saved_video, _metadata = _generate_video_task(
        prompt=prompt,
        output_dir=output_dir,
        output_video_path=output_video_path,
        metadata_path=output_dir / "kling_api_metadata.json",
        model=model,
        duration=segments[0],
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        sound_enabled=sound_enabled,
        quality=quality,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return saved_video


def _generate_video_task(
    *,
    prompt: str,
    output_dir: Path,
    output_video_path: Path,
    metadata_path: Path,
    model: str | None,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    sound_enabled: bool,
    quality: str | None,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> tuple[Path, dict[str, Any]]:

    resolved_model = _primary_video_model(model)
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
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return saved_video, metadata


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
