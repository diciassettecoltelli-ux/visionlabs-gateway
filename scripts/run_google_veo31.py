from __future__ import annotations

import argparse
import json
import mimetypes
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any

import certifi


def _resolve_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY to run Veo.")
    return api_key


def _guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(path))
    return mime_type or "image/png"


def _load_google_image(types_module: Any, image_path: Path) -> Any:
    return types_module.Image(
        image_bytes=image_path.read_bytes(),
        mime_type=_guess_mime_type(image_path),
    )


def _make_video_config(types_module: Any, *, duration_seconds: int, aspect_ratio: str, resolution: str | None = None) -> Any:
    config_cls = getattr(types_module, "GenerateVideosConfig", None)
    if config_cls is not None:
        kwargs: dict[str, Any] = {
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "number_of_videos": 1,
        }
        if resolution:
            kwargs["resolution"] = resolution
        return config_cls(**kwargs)
    payload = {"duration_seconds": duration_seconds, "aspect_ratio": aspect_ratio, "number_of_videos": 1}
    if resolution:
        payload["resolution"] = resolution
    return payload


def _safe_duration_seconds(value: int, *, resolution: str | None = None) -> int:
    try:
        requested = int(value)
    except Exception:
        requested = 6
    legal = [4, 6, 8]
    chosen = min(legal, key=lambda candidate: abs(candidate - requested))
    if (resolution or "").strip().lower() == "1080p" and chosen < 8:
        return 8
    return chosen


def _parse_fallback_models(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _operation_done(operation: Any) -> bool:
    if hasattr(operation, "done"):
        return bool(operation.done)
    if isinstance(operation, dict):
        return bool(operation.get("done", False))
    return False


def _poll_operation(client: Any, operation: Any, *, timeout_seconds: int, poll_interval_seconds: int) -> Any:
    start = time.time()
    current = operation
    while not _operation_done(current):
        if time.time() - start > timeout_seconds:
            raise TimeoutError("Veo generation exceeded timeout.")
        time.sleep(max(1, poll_interval_seconds))
        current = client.operations.get(current)
    return current


def _download_url(url: str, output_video: Path) -> Path:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=300, context=context) as response:
        output_video.write_bytes(response.read())
    return output_video


def _save_video_object(client: Any, video: Any, output_video: Path) -> Path:
    try:
        payload = client.files.download(file=video)
        if payload:
            output_video.parent.mkdir(parents=True, exist_ok=True)
            output_video.write_bytes(bytes(payload))
            return output_video
    except Exception:
        pass
    if hasattr(video, "save") and callable(video.save):
        try:
            video.save(str(output_video))
            return output_video
        except Exception:
            pass
    for attr in ("video_bytes", "bytes", "data"):
        payload = getattr(video, attr, None)
        if payload:
            output_video.parent.mkdir(parents=True, exist_ok=True)
            output_video.write_bytes(bytes(payload))
            return output_video
    for attr in ("uri", "url", "download_uri", "download_url", "file_uri"):
        payload = getattr(video, attr, None)
        if payload:
            return _download_url(str(payload), output_video)
    if isinstance(video, dict):
        for key in ("video_bytes", "bytes", "data"):
            payload = video.get(key)
            if payload:
                output_video.parent.mkdir(parents=True, exist_ok=True)
                output_video.write_bytes(bytes(payload))
                return output_video
        for key in ("uri", "url", "download_uri", "download_url", "file_uri"):
            payload = video.get(key)
            if payload:
                return _download_url(str(payload), output_video)
    raise RuntimeError("Unable to save Veo video artifact from operation response.")


def _find_video_object(response: Any) -> Any:
    generated_videos = getattr(response, "generated_videos", None)
    if generated_videos:
        first = generated_videos[0]
        return getattr(first, "video", first)
    if isinstance(response, dict):
        items = response.get("generated_videos", [])
        if items:
            first = items[0]
            return first.get("video", first)
    raise RuntimeError("Veo response did not contain generated_videos.")


def status() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    fast_model = os.getenv("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview").strip()
    standard_model = os.getenv("GOOGLE_VEO_STANDARD_MODEL", "veo-3.1-fast-generate-preview").strip()
    premium_model = os.getenv("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview").strip()
    image_model = os.getenv("GOOGLE_IMAGE_MODEL", "gemini-3.1-flash-image-preview").strip()
    return {
        "ready": bool(api_key and (fast_model or standard_model or premium_model)),
        "mode": "google",
        "has_api_key": bool(api_key),
        "video_models": {
            "fast": fast_model or None,
            "studio": standard_model or None,
            "director": premium_model or None,
        },
        "image_model": image_model or None,
    }


def generate_video(
    *,
    prompt: str,
    output_dir: str | Path,
    output_video: str | Path | None = None,
    model: str | None = None,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str | None = None,
    reference_image: str | Path | None = None,
    fallback_models: str | list[str] | None = None,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 10,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = Path(output_video) if output_video else output_dir / "google_veo31.mp4"
    safe_duration = _safe_duration_seconds(duration, resolution=resolution)
    reference_path = Path(reference_image).expanduser().resolve() if reference_image else None

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("google-genai is required for Veo runtime integration.") from exc

    client = genai.Client(api_key=_resolve_api_key())
    primary_model = model or os.getenv("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview")
    model_chain = [primary_model]
    if isinstance(fallback_models, str):
        model_chain.extend(_parse_fallback_models(fallback_models))
    elif isinstance(fallback_models, list):
        model_chain.extend([value for value in fallback_models if value])

    operation = None
    chosen_model = primary_model
    attempt_log: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for candidate in model_chain:
        chosen_model = candidate
        config = _make_video_config(
            types,
            duration_seconds=safe_duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "prompt": prompt,
            "config": config,
        }
        if reference_path and reference_path.exists():
            kwargs["image"] = _load_google_image(types, reference_path)
        try:
            operation = client.models.generate_videos(**kwargs)
            attempt_log.append({"model": chosen_model, "status": "started"})
            break
        except Exception as exc:
            last_error = exc
            attempt_log.append({"model": chosen_model, "status": "error", "error": str(exc)})

    if operation is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("Veo generation could not start.")

    finished = _poll_operation(
        client,
        operation,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    response = getattr(finished, "response", None)
    if response is None:
        response = getattr(finished, "result", None)
    if response is None and isinstance(finished, dict):
        response = finished.get("response") or finished.get("result")
    if response is None:
        raise RuntimeError("Veo operation completed without a response payload.")

    video = _find_video_object(response)
    saved_video = _save_video_object(client, video, output_video_path)
    metadata = {
        "provider": "google",
        "model": chosen_model,
        "prompt": prompt,
        "output_video": str(saved_video),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration": safe_duration,
        "reference_image": str(reference_path) if reference_path and reference_path.exists() else None,
        "attempt_log": attempt_log,
    }
    (output_dir / "google_veo31_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return saved_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a video with Google Veo.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-video")
    parser.add_argument("--model", default=os.getenv("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview"))
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--reference-image")
    parser.add_argument("--fallback-models", default=os.getenv("GOOGLE_VEO_FALLBACK_MODELS", ""))
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
        reference_image=args.reference_image,
        fallback_models=args.fallback_models,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    print(saved_video)


if __name__ == "__main__":
    main()
