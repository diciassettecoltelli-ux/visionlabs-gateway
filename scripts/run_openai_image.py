from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_IMAGE_MODEL = "gpt-image-1.5"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "auto"
DEFAULT_OUTPUT_FORMAT = "png"

ALLOWED_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
ALLOWED_BACKGROUNDS = {"transparent", "opaque", "auto"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _api_key() -> str:
    key = _env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OpenAI image generation is not configured. Set OPENAI_API_KEY on the Vision gateway.")
    return key


def _default_model() -> str:
    return _env("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL) or DEFAULT_IMAGE_MODEL


def _default_size() -> str:
    requested = _env("OPENAI_IMAGE_SIZE", "")
    return requested if requested in ALLOWED_SIZES else DEFAULT_IMAGE_SIZE


def _default_quality() -> str:
    requested = _env("OPENAI_IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY).lower()
    return requested if requested in ALLOWED_QUALITIES else DEFAULT_IMAGE_QUALITY


def _default_background() -> str | None:
    requested = _env("OPENAI_IMAGE_BACKGROUND", "auto").lower()
    return requested if requested in ALLOWED_BACKGROUNDS else "auto"


def _size_for_aspect_ratio(aspect_ratio: str | None) -> str:
    configured = _env("OPENAI_IMAGE_SIZE", "")
    if configured in ALLOWED_SIZES and configured != "auto":
        return configured
    normalized = str(aspect_ratio or "").strip().lower().replace(" ", "")
    if normalized in {"9:16", "vertical", "portrait", "reel", "tiktok"}:
        return "1024x1536"
    if normalized in {"16:9", "horizontal", "landscape", "wide"}:
        return "1536x1024"
    if normalized in {"1:1", "square"}:
        return "1024x1024"
    return DEFAULT_IMAGE_SIZE


def _quality_for_job(quality: str | None) -> str:
    requested = str(quality or "").strip().lower()
    if requested in ALLOWED_QUALITIES:
        return requested
    if requested == "fast":
        return "low"
    if requested == "director":
        return "high"
    if requested == "studio":
        return "medium"
    return _default_quality()


def _create_client(api_key: str) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Install the openai package on the Vision gateway.") from exc
    return OpenAI(api_key=api_key)


def status() -> dict[str, Any]:
    has_api_key = bool(_env("OPENAI_API_KEY"))
    sdk_ready = True
    sdk_error = None
    try:
        from openai import OpenAI as _OpenAI  # noqa: F401
    except ImportError as exc:
        sdk_ready = False
        sdk_error = str(exc)
    return {
        "ready": bool(has_api_key and sdk_ready),
        "mode": "openai_image",
        "has_api_key": has_api_key,
        "sdk_ready": sdk_ready,
        "sdk_error": sdk_error,
        "model": _default_model(),
        "size": _default_size(),
        "quality": _default_quality(),
        "background": _default_background(),
    }


def generate_image(
    *,
    prompt: str,
    output_dir: Path,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    aspect_ratio: str | None = None,
    output_image: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_image or output_dir / "openai_image.png"
    image_size = size if size in ALLOWED_SIZES and size != "auto" else _size_for_aspect_ratio(aspect_ratio)
    image_quality = _quality_for_job(quality)
    background = _default_background()
    payload: dict[str, Any] = {
        "model": model or _default_model(),
        "prompt": prompt,
        "n": 1,
        "size": image_size,
        "quality": image_quality,
        "output_format": DEFAULT_OUTPUT_FORMAT,
    }
    if background:
        payload["background"] = background

    client = _create_client(_api_key())
    result = client.images.generate(**payload)
    if not result.data:
        raise RuntimeError("OpenAI did not return an image.")

    first = result.data[0]
    image_b64 = getattr(first, "b64_json", None)
    if image_b64:
        output_path.write_bytes(base64.b64decode(image_b64))
    else:
        image_url = getattr(first, "url", None)
        if not image_url:
            raise RuntimeError("OpenAI image response did not include b64_json or url.")
        with urllib.request.urlopen(image_url, timeout=120) as response:
            output_path.write_bytes(response.read())

    metadata = {
        "provider": "openai_image",
        "model": payload["model"],
        "size": image_size,
        "quality": image_quality,
        "background": background,
        "output": str(output_path),
    }
    (output_dir / "openai_image_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return output_path
