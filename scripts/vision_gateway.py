from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import sqlite3
import smtplib
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from run_google_nano_banana2 import status as google_image_status
from run_google_prompt_enhancer import improve_prompt as improve_vision_prompt
from run_google_prompt_enhancer import status as google_prompt_status
from run_google_veo31 import generate_video as generate_google_veo_video
from run_google_veo31 import status as google_video_status
from run_kling_api import generate_video as generate_kling_api_video
from run_kling_api import status as kling_api_status
from run_openai_image import status as openai_image_status
from run_seedance_modelark import generate_video as generate_seedance_video
from run_seedance_modelark import status as seedance_status
from vision_kling_session_bridge import SessionBridgeNotReadyError
from vision_kling_session_bridge import generate as generate_kling_session_bridge
from vision_kling_session_bridge import generate_image as generate_kling_image
from vision_kling_session_bridge import prepare as prepare_kling_session_bridge
from vision_kling_session_bridge import status_image as kling_image_status
from vision_kling_session_bridge import status as kling_session_bridge_status


PROCESS_ACCESS_SECRET = secrets.token_urlsafe(48)
STRIPE_BILLING_CACHE_LOCK = threading.Lock()
STRIPE_BILLING_CACHE: dict[str, dict[str, Any]] = {}
VISION_STUDIO_LEGACY_PRICE_CENTS = frozenset({99})


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


def _normalize_generated_asset_path(path: str | None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    candidate = f"/{raw}" if raw.startswith("generated/") else raw
    try:
        parsed = urllib.parse.urlparse(candidate if "://" in candidate else f"http://vision.local{candidate}")
    except Exception:
        return ""
    pathname = str(parsed.path or "")
    while "//" in pathname:
        pathname = pathname.replace("//", "/")
    if not pathname.startswith("/generated/") or pathname == "/generated/":
        return ""
    return pathname


def _resolve_generated_asset_file(path: str | None) -> tuple[str, Path] | None:
    asset_path = _normalize_generated_asset_path(path)
    if not asset_path:
        return None
    relative_path = asset_path.removeprefix("/generated/").lstrip("/")
    if not relative_path:
        return None
    candidate = (OUTPUT_ROOT / relative_path).resolve()
    output_root = OUTPUT_ROOT.resolve()
    try:
        candidate.relative_to(output_root)
    except ValueError:
        return None
    return asset_path, candidate


def _default_generation_quality() -> str:
    requested = os.environ.get("VISION_GATEWAY_DEFAULT_GENERATION_QUALITY", "auto").strip().lower()
    return requested if requested in {"auto", "fast", "studio", "director"} else "studio"


def _default_generation_provider() -> str:
    return _normalize_generation_provider(os.environ.get("VISION_GATEWAY_DEFAULT_GENERATION_PROVIDER", "auto"))


def _default_image_provider() -> str:
    # Image generation is intentionally pinned to the authenticated Kling web
    # session bridge.  Do not silently spend credits with another provider when
    # that session is unavailable.
    return "kling"


def _normalize_generation_provider(value: str | None) -> str:
    requested = str(value or "auto").strip().lower()
    return requested if requested in {"auto", "seedance", "google", "kling", "openai"} else "auto"


def _normalize_quality(value: str | None) -> str:
    if not value:
        return "auto"
    normalized = value.strip().lower()
    return normalized if normalized in {"auto", "fast", "studio", "director"} else _default_generation_quality()


def _normalize_mode(value: str | None) -> str:
    if not value:
        return "image"
    normalized = value.strip().lower()
    return normalized if normalized in {"video", "image"} else "image"


def _normalize_duration_seconds(value: int | None) -> int:
    try:
        requested = int(value) if value is not None else 5
    except (TypeError, ValueError):
        requested = 5
    if requested <= 3:
        return 3
    if requested <= 5:
        return 5
    if requested <= 10:
        return 10
    return 15


def _normalize_resolution(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "")
    if normalized in {"480", "480p"}:
        return "480p"
    if normalized in {"720", "720p", "hd"}:
        return "720p"
    if normalized in {"1080", "1080p", "fullhd", "fhd"}:
        return "1080p"
    if normalized in {"2k", "1440", "1440p", "qhd"}:
        return "2k"
    if normalized in {"4k", "2160", "2160p", "uhd"}:
        return "4k"
    return "720p"


def _normalize_aspect_ratio(value: str | None) -> str:
    normalized = str(value or "16:9").strip().lower().replace(" ", "")
    aliases = {
        "vertical": "9:16",
        "portrait": "9:16",
        "reel": "9:16",
        "reels": "9:16",
        "tiktok": "9:16",
        "short": "9:16",
        "shorts": "9:16",
        "landscape": "16:9",
        "horizontal": "16:9",
        "wide": "16:9",
        "square": "1:1",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"1:1", "16:9", "9:16", "4:5", "3:4", "4:3", "3:2"}:
        return normalized
    return "16:9"


def _fit_image_to_aspect_ratio(image_path: str | Path, aspect_ratio: str | None) -> Path:
    path = Path(image_path)
    normalized = _normalize_aspect_ratio(aspect_ratio)
    width_ratio, height_ratio = (int(part) for part in normalized.split(":", 1))
    target_ratio = width_ratio / height_ratio

    with Image.open(path) as source:
        source.load()
        width, height = source.size
        if width <= 0 or height <= 0:
            raise RuntimeError("Generated image has invalid dimensions.")

        current_ratio = width / height
        if abs(current_ratio - target_ratio) <= 0.001:
            return path

        if current_ratio > target_ratio:
            crop_width = max(1, min(width, round(height * target_ratio)))
            left = max(0, (width - crop_width) // 2)
            crop_box = (left, 0, left + crop_width, height)
        else:
            crop_height = max(1, min(height, round(width / target_ratio)))
            top = max(0, (height - crop_height) // 2)
            crop_box = (0, top, width, top + crop_height)

        cropped = source.crop(crop_box)
        if cropped.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
            cropped = cropped.convert("RGBA" if "A" in cropped.getbands() else "RGB")
        cropped.save(path, format="PNG")

    return path


def _quality_from_generation_settings(mode: str, resolution: str, sound_enabled: bool) -> str:
    if mode == "image":
        return "director" if resolution == "4k" else "studio"
    if resolution == "4k":
        return "director"
    if resolution == "1080p" or sound_enabled:
        return "director"
    if resolution == "480p":
        return "fast"
    return "studio"


def _vision_credit_cost(mode: str, *, duration_seconds: int, resolution: str, sound_enabled: bool) -> dict[str, Any]:
    normalized_mode = _normalize_mode(mode)
    normalized_duration = _normalize_duration_seconds(duration_seconds)
    normalized_resolution = _normalize_resolution(resolution)
    if normalized_mode == "image":
        premium_image = normalized_resolution == "4k"
        amount = 25000 if premium_image else 10000
        tier = "premium_image" if premium_image else "standard_image"
        label = "Premium image" if premium_image else "Standard image"
    elif normalized_resolution == "4k":
        amount = normalized_duration * 200000
        tier = "premium_video"
        label = f"4K video · {normalized_duration}s"
    elif sound_enabled or normalized_resolution == "1080p":
        amount = normalized_duration * 50000
        tier = "pro_video"
        label = f"Full HD video · {normalized_duration}s"
    else:
        amount = normalized_duration * 20000
        tier = "standard_video"
        label = f"Standard video · {normalized_duration}s"
    return {
        "amount": int(amount),
        "currency": "vision_credits",
        "tier": tier,
        "label": label,
        "mode": normalized_mode,
        "duration_seconds": normalized_duration if normalized_mode == "video" else None,
        "resolution": normalized_resolution,
        "sound_enabled": bool(sound_enabled) if normalized_mode == "video" else False,
    }


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
        return ["director", "studio", "fast"]
    return {
        "fast": ["fast", "studio", "director"],
        "studio": ["studio", "director"],
        "director": ["director"],
    }.get(quality, ["studio", "director", "fast"])


def _effective_job_quality(mode: str, quality: str) -> str:
    if mode != "video":
        return "studio" if quality == "auto" else quality
    if quality == "auto":
        return "director"
    return quality


def _prompt_route_profile(prompt: str) -> str:
    lowered = " ".join((prompt or "").lower().split())
    human_cues = {
        "woman",
        "man",
        "girl",
        "boy",
        "person",
        "people",
        "portrait",
        "face",
        "skin",
        "eyes",
        "fashion",
        "editorial",
        "dress",
        "model",
        "character",
        "couple",
        "beauty",
        "close-up",
        "close up",
    }
    environment_cues = {
        "house",
        "villa",
        "interior",
        "room",
        "architecture",
        "building",
        "landscape",
        "forest",
        "mountain",
        "desert",
        "ocean",
        "beach",
        "city",
        "street",
        "bar",
        "hotel",
        "restaurant",
        "cocktail",
        "product",
        "perfume",
        "bottle",
        "jewelry",
    }
    motion_cues = {
        "walking",
        "running",
        "driving",
        "tracking",
        "camera drift",
        "dolly",
        "orbit",
        "pan",
        "tilt",
        "handheld",
        "slow motion",
        "wind",
        "waves",
        "rain",
        "action",
        "car",
        "vehicle",
        "motorcycle",
    }
    luxury_cues = {
        "luxury",
        "premium",
        "cinematic",
        "ultra-realistic",
        "ultra realistic",
        "feature-film",
        "feature film",
        "photoreal",
        "photo-real",
        "editorial",
    }

    has_human = any(cue in lowered for cue in human_cues)
    has_environment = any(cue in lowered for cue in environment_cues)
    has_motion = any(cue in lowered for cue in motion_cues)
    has_luxury = any(cue in lowered for cue in luxury_cues)

    if has_human and (has_luxury or has_motion):
        return "human_premium"
    if has_human:
        return "human"
    if has_environment and has_motion:
        return "motion_environment"
    if has_environment or has_luxury:
        return "environment"
    return "general"


def _provider_priority_for_prompt(prompt: str, quality: str) -> list[str]:
    profile = _prompt_route_profile(prompt)
    if quality == "fast":
        if profile.startswith("human"):
            return ["google", "kling", "seedance"]
        return ["google", "seedance", "kling"]
    if profile.startswith("human"):
        return ["google", "kling", "seedance"]
    if profile == "motion_environment":
        return ["google", "seedance", "kling"]
    if profile == "environment":
        return ["google", "seedance", "kling"]
    return ["google", "seedance", "kling"]


def _kling_api_first_enabled() -> bool:
    return os.environ.get("VISION_KLING_API_FIRST", "true").strip().lower() not in {"0", "false", "no", "off"}


def _quality_candidates_for_prompt(quality: str) -> list[str]:
    if quality == "auto":
        return ["director", "studio", "fast"]
    return {
        "fast": ["fast", "studio", "director"],
        "studio": ["studio", "director"],
        "director": ["director"],
    }.get(quality, ["director", "studio", "fast"])


def _auto_enhance_job_prompt(prompt: str, mode: str) -> dict[str, Any]:
    cleaned = prompt.strip()
    try:
        result = improve_vision_prompt(prompt=cleaned, mode=mode)
        improved_prompt = str(result.get("improved_prompt") or "").strip()
        if improved_prompt:
            return {
                "prompt": improved_prompt,
                "source_prompt": cleaned,
                "prompt_summary": str(result.get("summary") or "").strip() or None,
                "prompt_provider": str(result.get("provider") or "vision_local"),
                "prompt_model": str(result.get("model") or "") or None,
                "prompt_enhanced": improved_prompt != cleaned,
            }
    except Exception as exc:
        return {
            "prompt": cleaned,
            "source_prompt": cleaned,
            "prompt_summary": None,
            "prompt_provider": None,
            "prompt_model": None,
            "prompt_enhanced": False,
            "prompt_enhancement_error": str(exc),
        }
    return {
        "prompt": cleaned,
        "source_prompt": cleaned,
        "prompt_summary": None,
        "prompt_provider": None,
        "prompt_model": None,
        "prompt_enhanced": False,
    }


def _google_video_model_for_quality(quality: str) -> str | None:
    env_map = {
        "fast": os.environ.get("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview").strip(),
        "studio": os.environ.get("GOOGLE_VEO_STANDARD_MODEL", "veo-3.1-fast-generate-preview").strip(),
        "director": os.environ.get("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview").strip(),
    }
    return env_map.get(quality) or None


def _google_resolution_for_quality(quality: str) -> str:
    env_map = {
        "fast": os.environ.get("GOOGLE_VEO_FAST_RESOLUTION", "720p").strip().lower(),
        "studio": os.environ.get("GOOGLE_VEO_STANDARD_RESOLUTION", "720p").strip().lower(),
        "director": os.environ.get("GOOGLE_VEO_PREMIUM_RESOLUTION", "4k").strip().lower(),
    }
    requested = env_map.get(quality) or "720p"
    return requested if requested in {"720p", "1080p", "4k"} else "720p"


def _google_duration_for_quality(quality: str) -> int:
    return {
        "fast": 4,
        "studio": 6,
        "director": 8,
    }.get(quality, 6)


def _google_fallback_models_for_quality(quality: str) -> str:
    fallback_map = {
        "fast": os.environ.get("GOOGLE_VEO_FAST_FALLBACK_MODELS", "").strip(),
        "studio": os.environ.get(
            "GOOGLE_VEO_STANDARD_FALLBACK_MODELS",
            ",".join(
                value
                for value in [
                    os.environ.get("GOOGLE_VEO_PREMIUM_MODEL", "veo-3.1-generate-preview").strip(),
                    os.environ.get("GOOGLE_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview").strip(),
                ]
                if value
            ),
        ).strip(),
        "director": os.environ.get(
            "GOOGLE_VEO_PREMIUM_FALLBACK_MODELS",
            os.environ.get("GOOGLE_VEO_STANDARD_MODEL", "veo-3.1-fast-generate-preview").strip(),
        ).strip(),
    }
    return fallback_map.get(quality, "").strip()


def _google_status() -> dict[str, Any]:
    image_state = google_image_status()
    video_state = google_video_status()
    prompt_state = _prompt_status()
    return {
        "ready": bool(image_state.get("ready") or video_state.get("ready") or prompt_state.get("ready")),
        "image": image_state,
        "video": video_state,
        "prompt": prompt_state,
    }


def _image_route_for_provider(provider: str) -> dict[str, str] | None:
    if provider == "openai":
        openai_state = openai_image_status()
        if openai_state.get("ready"):
            return {
                "provider": "openai_image",
                "model": str(openai_state.get("model") or os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5")),
                "size": str(openai_state.get("size") or os.environ.get("OPENAI_IMAGE_SIZE", "1024x1536")),
                "fallback_models": "",
            }
        return None
    if provider == "kling":
        kling_state = kling_image_status()
        if kling_state.get("ready"):
            return {
                "provider": "kling_image",
                "model": os.environ.get("VISION_KLING_IMAGE_MODEL", "kling-image-web"),
                "fallback_models": "",
            }
        return None
    if provider == "google":
        google_state = _google_status()
        image_state = google_state["image"]
        if image_state.get("ready"):
            return {
                "provider": "google_image",
                "model": str(image_state.get("model") or os.environ.get("GOOGLE_IMAGE_MODEL", "imagen-4.0-generate-001")),
                "fallback_models": str(image_state.get("fallback_models") or os.environ.get("GOOGLE_IMAGE_FALLBACK_MODELS", "imagen-4.0-fast-generate-001")),
            }
        return None
    return None


def _select_image_route(_preferred_provider: str | None = None) -> dict[str, str]:
    route = _image_route_for_provider("kling")
    if route:
        return route
    raise RuntimeError("Kling image generation is not ready yet for this Vision deployment.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_pack_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "studio",
            "name": "Vision Studio",
            "subtitle": "Unlimited 4K images",
            "description": "Monthly access for unlimited 4K image generation, prompt enhancement, recent image history, and watermark-free downloads.",
            "price_cents": 199,
            "original_price_cents": 199,
            "currency": "eur",
            "vision_credits": 0,
            "credit_label": "Unlimited 4K images",
            "total_credit_label": "Unlimited 4K images every month",
            "discount_label": "",
            "video_credits": 0,
            "image_credits": 999999,
            "video_label": "",
            "duration_label": "",
            "image_label": "Unlimited 4K images",
            "value_label": "Unlimited 4K images every month.",
            "badge": "Monthly",
            "cta_label": "Start Vision Studio",
            "features": [
                "Unlimited 4K image generation",
                "Prompt enhancement included",
                "Recent image history",
                "Watermark-free downloads",
            ],
        },
    ]


def _format_pack_price_display(price_cents: int, currency: str) -> str:
    amount = price_cents / 100
    if currency.lower() == "eur":
        return f"€{amount:.2f}".replace(".", ",")
    return f"{amount:.2f} {currency.upper()}"


def _packs_summary() -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for pack in _default_pack_catalog():
        summary = copy.deepcopy(pack)
        summary["price_display"] = _format_pack_price_display(int(summary["price_cents"]), str(summary["currency"]))
        packs.append(summary)
    return packs


def _public_pack(summary: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(summary)


def _should_reveal_pack_credits(access_summary: dict[str, Any] | None) -> bool:
    if not access_summary:
        return False
    return bool(access_summary.get("admin") or access_summary.get("access_id") or access_summary.get("has_access"))


def _pack_summary_for_access(access_summary: dict[str, Any] | None, pack_id: str | None = None) -> dict[str, Any]:
    summary = _pack_summary(pack_id)
    return summary if _should_reveal_pack_credits(access_summary) else _public_pack(summary)


def _packs_summary_for_access(access_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summaries = _packs_summary()
    return summaries if _should_reveal_pack_credits(access_summary) else [_public_pack(summary) for summary in summaries]


def _pack_by_id(pack_id: str | None) -> dict[str, Any]:
    normalized = str(pack_id or "").strip().lower()
    packs = _packs_summary()
    for pack in packs:
        if str(pack.get("id")) == normalized:
            return pack
    return packs[0]


def _pack_by_exact_id(pack_id: str | None) -> dict[str, Any] | None:
    normalized = str(pack_id or "").strip().lower()
    if not normalized:
        return None
    for pack in _packs_summary():
        if str(pack.get("id") or "").strip().lower() == normalized:
            return pack
    return None


def _pack_price_cents() -> int:
    return int(_pack_summary().get("price_cents") or 199)


def _accepted_pack_price_cents(pack: dict[str, Any]) -> set[int]:
    accepted = {int(pack.get("price_cents") or 0)}
    if str(pack.get("id") or "").strip().lower() == "studio":
        accepted.update(VISION_STUDIO_LEGACY_PRICE_CENTS)
    return accepted


def _pack_accepts_price_cents(pack: dict[str, Any], amount: int | None) -> bool:
    return amount is not None and amount in _accepted_pack_price_cents(pack)


def _pack_currency() -> str:
    return str(_pack_summary().get("currency") or "eur").strip().lower() or "eur"


def _pack_video_credits() -> int:
    return max(int(_pack_summary().get("video_credits") or 0), 0)


def _pack_image_credits() -> int:
    return max(int(_pack_summary().get("image_credits") or 999999), 0)


def _pack_vision_credits() -> int:
    return max(int(_pack_summary().get("vision_credits") or 0), 0)


def _pack_name() -> str:
    return str(_pack_summary().get("name") or "Vision Studio").strip() or "Vision Studio"


def _pack_description() -> str:
    return str(_pack_summary().get("description") or "Unlimited 4K images").strip() or "Unlimited 4K images"


def _access_cookie_name() -> str:
    return os.environ.get("VISION_ACCESS_COOKIE_NAME", "vision_access").strip() or "vision_access"


def _access_secret() -> str:
    return os.environ.get("VISION_ACCESS_SECRET", "").strip() or PROCESS_ACCESS_SECRET


def _user_cookie_name() -> str:
    return os.environ.get("VISION_USER_COOKIE_NAME", "vision_user").strip() or "vision_user"


def _user_secret() -> str:
    configured = os.environ.get("VISION_USER_SECRET", "").strip()
    return configured or _access_secret()


def _signup_discount_percent() -> int:
    try:
        return max(0, min(90, int(os.environ.get("VISION_SIGNUP_DISCOUNT_PERCENT", "20"))))
    except ValueError:
        return 20


def _auth_code_ttl_minutes() -> int:
    try:
        return max(5, min(60, int(os.environ.get("VISION_AUTH_CODE_TTL_MINUTES", "15"))))
    except ValueError:
        return 15


def _auth_code_resend_seconds() -> int:
    try:
        return max(15, min(300, int(os.environ.get("VISION_AUTH_CODE_RESEND_SECONDS", "60"))))
    except ValueError:
        return 60


def _auth_code_max_attempts() -> int:
    try:
        return max(3, min(10, int(os.environ.get("VISION_AUTH_CODE_MAX_ATTEMPTS", "5"))))
    except ValueError:
        return 5


def _user_token_ttl_seconds() -> int:
    try:
        hours = max(1, min(2160, int(os.environ.get("VISION_USER_TOKEN_TTL_HOURS", "720"))))
    except ValueError:
        hours = 720
    return hours * 60 * 60


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _hash_auth_code(email: str, code: str) -> str:
    normalized = _normalize_email(email)
    return hashlib.sha256(f"{normalized}:{code}:{_user_secret()}".encode("utf-8")).hexdigest()


def _sign_user_token(payload: dict[str, Any]) -> str:
    issued_at = int(datetime.now(timezone.utc).timestamp())
    token_payload = {
        **payload,
        "iat": issued_at,
        "exp": issued_at + _user_token_ttl_seconds(),
    }
    serialized = json.dumps(token_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")
    signature = hmac.new(_user_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _verify_user_token(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_user_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    padding = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode((body + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        return None
    return payload


def _notification_log_path() -> Path:
    return RUNTIME_ROOT / "purchase_notifications.jsonl"


def _notification_recipients() -> list[str]:
    raw = os.environ.get("VISION_NOTIFY_EMAIL_TO", "").strip()
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def _notification_sender() -> str:
    configured = os.environ.get("VISION_NOTIFY_EMAIL_FROM", "").strip()
    if configured:
        return configured
    username = os.environ.get("VISION_NOTIFY_SMTP_USERNAME", "").strip()
    if username:
        return username
    return "vision@localhost"


def _resend_api_key_for_email(host: str, password: str) -> str:
    configured = os.environ.get("VISION_NOTIFY_RESEND_API_KEY", "").strip()
    if configured:
        return configured
    if host == "smtp.resend.com" and password.startswith("re_"):
        return password
    return ""


def _send_resend_email(
    *,
    api_key: str,
    recipients: list[str],
    subject: str,
    body_lines: list[str],
    sender: str,
) -> None:
    payload = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "text": "\n".join(body_lines),
    }
    encoded_payload = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=encoded_payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "visionlabs-gateway/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in {200, 201, 202}:
                raise RuntimeError(f"Resend API returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Resend API connection failed: {exc.reason}") from exc


def _send_email(*, recipients: list[str], subject: str, body_lines: list[str], sender: str | None = None) -> None:
    host = os.environ.get("VISION_NOTIFY_SMTP_HOST", "").strip()
    if not recipients:
        return

    port = int(os.environ.get("VISION_NOTIFY_SMTP_PORT", "587"))
    username = os.environ.get("VISION_NOTIFY_SMTP_USERNAME", "").strip()
    password = os.environ.get("VISION_NOTIFY_SMTP_PASSWORD", "").strip()
    use_ssl = os.environ.get("VISION_NOTIFY_SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
    use_starttls = os.environ.get("VISION_NOTIFY_SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes", "on"}
    resolved_sender = sender or _notification_sender()

    resend_api_key = _resend_api_key_for_email(host, password)
    if resend_api_key:
        _send_resend_email(
            api_key=resend_api_key,
            recipients=recipients,
            subject=subject,
            body_lines=body_lines,
            sender=resolved_sender,
        )
        return

    if not host:
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = resolved_sender
    message["To"] = ", ".join(recipients)
    message.set_content("\n".join(body_lines))

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            if username and password:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_starttls:
            context = ssl.create_default_context()
            server.starttls(context=context)
        if username and password:
            server.login(username, password)
        server.send_message(message)


def _prompt_status() -> dict[str, Any]:
    return google_prompt_status()


def _write_purchase_notification(record: dict[str, Any]) -> None:
    log_path = _notification_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send_purchase_notification_email(record: dict[str, Any]) -> None:
    recipients = _notification_recipients()
    if not recipients:
        return
    body = [
        "A new Vision purchase has been confirmed.",
        "",
        f"Email: {record.get('email') or 'not provided'}",
        f"Pack: {record.get('pack_name')}",
        f"Credits: {record.get('vision_credits') or 'legacy'} Vision credits",
        f"Image capacity: {record.get('image_credits')} images",
        f"Amount: {record.get('amount_total')} {str(record.get('currency') or '').upper()}",
        f"Access ID: {record.get('access_id')}",
        f"Checkout session: {record.get('session_id')}",
        f"Purchased at: {record.get('confirmed_at')}",
    ]
    _send_email(
        recipients=recipients,
        subject=f"New Vision purchase · {record.get('email') or 'unknown email'}",
        body_lines=body,
    )


def _notify_purchase_async(*, session: dict[str, Any], entry: dict[str, Any]) -> None:
    metadata = session.get("metadata") or {}
    session_pack = _pack_by_id(metadata.get("vision_pack_id"))
    record = {
        "session_id": session.get("id"),
        "email": entry.get("email"),
        "access_id": entry.get("id"),
        "pack_name": metadata.get("vision_pack_name") or session_pack.get("name"),
        "vision_credits": metadata.get("vision_pack_vision_credits") or session_pack.get("vision_credits"),
        "video_credits": metadata.get("vision_pack_video_credits") or session_pack.get("video_credits"),
        "image_credits": metadata.get("vision_pack_image_credits") or session_pack.get("image_credits"),
        "amount_total": (session.get("amount_total") or int(session_pack.get("price_cents") or _pack_price_cents())) / 100,
        "currency": session.get("currency") or session_pack.get("currency") or _pack_currency(),
        "confirmed_at": _now_iso(),
    }

    def _worker() -> None:
        try:
            _write_purchase_notification(record)
            _send_purchase_notification_email(record)
        except Exception as exc:
            print(f"[vision] purchase notification failed: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


def _send_auth_code_email(*, email: str, code: str) -> None:
    normalized = _normalize_email(email)
    if not normalized:
        return
    body = [
        "Welcome to Vision.",
        "",
        "Thank you for choosing Vision.",
        "Use the access code below to enter your Vision Studio and return to your workspace whenever you want.",
        "",
        f"Access code: {code}",
        "",
        f"This code expires in {_auth_code_ttl_minutes()} minutes.",
        "It keeps your access secure and lets you return to your Studio from any device.",
        "If you did not request this code, you can ignore this message.",
    ]
    _send_email(
        recipients=[normalized],
        subject="Your Vision access code",
        body_lines=body,
        sender=_notification_sender(),
    )


def _sign_access_token(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")
    signature = hmac.new(_access_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _verify_access_token(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_access_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    padding = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode((body + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _frontend_base_url(request: Request) -> str:
    configured = os.environ.get("VISION_FRONTEND_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    origin = request.headers.get("origin", "").strip().rstrip("/")
    if origin:
        return origin
    referer = request.headers.get("referer", "").strip()
    if referer:
        parsed = urllib.parse.urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return "https://visionstudiolab.com"


def _safe_frontend_return_path(value: str | None, *, default: str = "/") -> str:
    fallback = default if default.startswith("/") and not default.startswith("//") else "/"
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if len(raw) > 512 or not raw.startswith("/") or raw.startswith("//"):
        return fallback
    if "\\" in raw or any(ord(character) < 32 for character in raw):
        return fallback
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return fallback
    decoded_path = parsed.path
    for _ in range(2):
        decoded_path = urllib.parse.unquote(decoded_path)
    if decoded_path.startswith("//") or "\\" in decoded_path or any(ord(character) < 32 for character in decoded_path):
        return fallback
    segments = [segment for segment in decoded_path.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        return fallback
    return parsed.path or fallback


def _frontend_return_url(request: Request, return_path: str | None, *, default: str = "/") -> str:
    return f"{_frontend_base_url(request)}{_safe_frontend_return_path(return_path, default=default)}"


def _cookie_settings(request: Request) -> dict[str, Any]:
    host = (request.url.hostname or "").lower()
    secure = host not in {"127.0.0.1", "localhost"}
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "none" if secure else "lax",
        "max_age": 60 * 60 * 24 * 90,
        "path": "/",
    }


def _set_access_cookie(response: Response, request: Request, payload: dict[str, Any]) -> None:
    response.set_cookie(
        key=_access_cookie_name(),
        value=_sign_access_token(payload),
        **_cookie_settings(request),
    )


def _set_user_cookie(response: Response, request: Request, payload: dict[str, Any]) -> None:
    settings = _cookie_settings(request)
    settings["max_age"] = _user_token_ttl_seconds()
    response.set_cookie(
        key=_user_cookie_name(),
        value=_sign_user_token(payload),
        **settings,
    )


def _clear_user_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(key=_user_cookie_name(), path="/", samesite=_cookie_settings(request)["samesite"])


def _clear_access_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(key=_access_cookie_name(), path="/", samesite=_cookie_settings(request)["samesite"])


def _strip_none_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _stripe_secret_key() -> str:
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("Stripe checkout is not configured yet.")
    return secret


def _stripe_api_version() -> str:
    return os.environ.get("STRIPE_API_VERSION", "2026-02-25.clover").strip() or "2026-02-25.clover"


def _stripe_request(method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    method_name = method.upper()
    url = f"https://api.stripe.com{path}"
    encoded_data: bytes | None = None
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{_stripe_secret_key()}:".encode("utf-8")).decode("ascii"),
        "Stripe-Version": _stripe_api_version(),
    }
    if data is not None:
        encoded = urllib.parse.urlencode(_strip_none_values(data), doseq=True)
        if method_name == "GET":
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{encoded}"
        else:
            encoded_data = encoded.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=encoded_data, headers=headers, method=method_name)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Stripe API error ({exc.code}): {body}") from exc


def _create_stripe_checkout_session(
    *,
    request: Request,
    email: str,
    user_id: str,
    pack_id: str | None,
    return_path: str | None = None,
    customer_id: str | None = None,
    tracking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return_url = _frontend_return_url(request, return_path)
    pack = _pack_by_id(pack_id)
    payload: dict[str, Any] = {
        "mode": "subscription",
        "success_url": f"{return_url}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{return_url}?checkout=cancel",
        "client_reference_id": user_id,
        "allow_promotion_codes": "true",
        "billing_address_collection": "auto",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": str(pack.get("currency") or "eur"),
        "line_items[0][price_data][unit_amount]": str(pack.get("price_cents") if pack.get("price_cents") is not None else 199),
        "line_items[0][price_data][recurring][interval]": "month",
        "line_items[0][price_data][recurring][interval_count]": "1",
        "line_items[0][price_data][product_data][name]": str(pack.get("name") or "Vision Studio"),
        "line_items[0][price_data][product_data][description]": str(pack.get("description") or ""),
        "metadata[vision_pack_id]": str(pack.get("id") or "studio"),
        "metadata[vision_pack_name]": str(pack.get("name") or "Vision Studio"),
        "metadata[vision_pack_vision_credits]": str(pack.get("vision_credits") or ""),
        "metadata[vision_pack_video_credits]": str(pack.get("video_credits") if pack.get("video_credits") is not None else 0),
        "metadata[vision_pack_image_credits]": str(pack.get("image_credits") if pack.get("image_credits") is not None else 999999),
        "metadata[vision_user_id]": user_id,
        "metadata[vision_user_email]": _normalize_email(email),
        "subscription_data[metadata][vision_pack_id]": str(pack.get("id") or "studio"),
        "subscription_data[metadata][vision_pack_name]": str(pack.get("name") or "Vision Studio"),
        "subscription_data[metadata][vision_pack_vision_credits]": str(pack.get("vision_credits") or ""),
        "subscription_data[metadata][vision_pack_video_credits]": str(pack.get("video_credits") if pack.get("video_credits") is not None else 0),
        "subscription_data[metadata][vision_pack_image_credits]": str(pack.get("image_credits") if pack.get("image_credits") is not None else 999999),
        "subscription_data[metadata][vision_user_id]": user_id,
        "subscription_data[metadata][vision_user_email]": _normalize_email(email),
    }
    for key, value in _tracking_metadata(tracking).items():
        payload[f"metadata[{key}]"] = value
    if customer_id:
        payload["customer"] = customer_id
    else:
        payload["customer_email"] = email
    return _stripe_request("POST", "/v1/checkout/sessions", payload)


def _retrieve_stripe_checkout_session(session_id: str) -> dict[str, Any]:
    encoded_session_id = urllib.parse.quote(session_id, safe="")
    return _stripe_request("GET", f"/v1/checkout/sessions/{encoded_session_id}")


def _retrieve_stripe_subscription(subscription_id: str) -> dict[str, Any]:
    encoded_subscription_id = urllib.parse.quote(subscription_id, safe="")
    return _stripe_request("GET", f"/v1/subscriptions/{encoded_subscription_id}")


def _retrieve_stripe_customer(customer_id: str) -> dict[str, Any]:
    encoded_customer_id = urllib.parse.quote(customer_id, safe="")
    return _stripe_request("GET", f"/v1/customers/{encoded_customer_id}")


def _create_stripe_customer_portal_session(*, customer_id: str, return_url: str) -> dict[str, Any]:
    return _stripe_request(
        "POST",
        "/v1/billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": return_url,
        },
    )


def _list_stripe_checkout_sessions_by_email(email: str, *, limit: int = 100) -> list[dict[str, Any]]:
    normalized = _normalize_email(email)
    if not normalized:
        return []
    payload = {
        "limit": str(max(1, min(limit, 100))),
        "status": "complete",
        "customer_details[email]": normalized,
    }
    response = _stripe_request("GET", "/v1/checkout/sessions", payload)
    items = response.get("data")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _stripe_object_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return str(value or "").strip()


def _stripe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _has_vision_plan_metadata(metadata: dict[str, Any]) -> bool:
    return any(
        str(key).startswith("vision_pack_") or key in {"vision_user_id", "vision_user_email"}
        for key in metadata
    )


def _vision_pack_from_metadata(metadata: dict[str, Any]) -> dict[str, Any] | None:
    pack = _pack_by_exact_id(metadata.get("vision_pack_id"))
    if not pack:
        return None

    expected_metadata = {
        "vision_pack_vision_credits": int(pack.get("vision_credits") or 0),
        "vision_pack_video_credits": int(pack.get("video_credits") or 0),
        "vision_pack_image_credits": int(pack.get("image_credits") or 0),
    }
    for key, expected_value in expected_metadata.items():
        if key not in metadata:
            continue
        try:
            actual_value = int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            return None
        if actual_value != expected_value:
            return None
    metadata_pack_name = str(metadata.get("vision_pack_name") or "").strip()
    if metadata_pack_name and metadata_pack_name != str(pack.get("name") or "").strip():
        return None
    return pack


def _credits_for_validated_pack(pack: dict[str, Any] | None) -> tuple[int, int, int]:
    exact_pack = _pack_by_exact_id(str((pack or {}).get("id") or ""))
    if not exact_pack:
        return 0, 0, 0
    return (
        max(int(exact_pack.get("vision_credits") or 0), 0),
        max(int(exact_pack.get("video_credits") or 0), 0),
        max(int(exact_pack.get("image_credits") or 0), 0),
    )


def _stripe_optional_int(value: Any) -> tuple[bool, int | None]:
    if value is None or value == "":
        return True, None
    try:
        return True, int(value)
    except (TypeError, ValueError):
        return False, None


def _stripe_amounts_match_pack(
    value: dict[str, Any],
    pack: dict[str, Any],
    *,
    subtotal_keys: tuple[str, ...],
    total_keys: tuple[str, ...],
    require_amount: bool,
) -> bool:
    expected_currency = str(pack.get("currency") or "").strip().lower()
    actual_currency = str(value.get("currency") or "").strip().lower()
    if actual_currency and actual_currency != expected_currency:
        return False
    if require_amount and not actual_currency:
        return False

    subtotal_present = False
    for key in subtotal_keys:
        if value.get(key) is None:
            continue
        valid, amount = _stripe_optional_int(value.get(key))
        if not valid or not _pack_accepts_price_cents(pack, amount):
            return False
        subtotal_present = True
        break

    total_present = False
    first_total: int | None = None
    for key in total_keys:
        if value.get(key) is None:
            continue
        valid, amount = _stripe_optional_int(value.get(key))
        if not valid or amount is None or amount < 0:
            return False
        if first_total is None:
            first_total = amount
        total_present = True

    if not subtotal_present and total_present:
        # Old Stripe objects did not always include a separate subtotal. In that
        # compatibility case, the exact paid total must identify the Vision plan.
        return _pack_accepts_price_cents(pack, first_total)
    if require_amount and not subtotal_present:
        return False
    return subtotal_present or total_present or not require_amount


def _subscription_price_matches_pack(
    subscription: dict[str, Any],
    pack: dict[str, Any],
    *,
    require_price: bool,
) -> bool:
    items_container = subscription.get("items")
    items = (items_container.get("data") or []) if isinstance(items_container, dict) else []
    items = [item for item in items if isinstance(item, dict)]
    if not items:
        return not require_price
    if len(items) != 1:
        return False

    item = items[0]
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    if not price and isinstance(item.get("plan"), dict):
        price = item["plan"]
    recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
    if not recurring and price.get("interval"):
        recurring = {
            "interval": price.get("interval"),
            "interval_count": price.get("interval_count"),
        }

    currency = str(price.get("currency") or "").strip().lower()
    if currency and currency != str(pack.get("currency") or "").strip().lower():
        return False
    valid_amount, unit_amount = _stripe_optional_int(price.get("unit_amount"))
    if not valid_amount:
        return False
    if unit_amount is not None and not _pack_accepts_price_cents(pack, unit_amount):
        return False
    interval = str(recurring.get("interval") or "").strip().lower()
    if interval and interval != "month":
        return False
    valid_interval_count, interval_count = _stripe_optional_int(recurring.get("interval_count"))
    if not valid_interval_count or (interval_count is not None and interval_count != 1):
        return False
    valid_quantity, quantity = _stripe_optional_int(item.get("quantity"))
    if not valid_quantity or (quantity is not None and quantity != 1):
        return False
    if require_price:
        return bool(currency and unit_amount is not None and interval == "month")
    return True


def _checkout_session_email(session: dict[str, Any]) -> str:
    customer_details = session.get("customer_details") if isinstance(session.get("customer_details"), dict) else {}
    return _normalize_email(customer_details.get("email") or session.get("customer_email"))


def _validated_vision_checkout_session(
    session: dict[str, Any],
    *,
    expected_email: str | None = None,
    allow_legacy: bool = False,
) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(session, dict):
        return None
    if session.get("object") not in {None, "checkout.session"}:
        return None
    if session.get("mode") != "subscription" or not _stripe_object_id(session.get("subscription")):
        return None
    if not _stripe_object_id(session.get("customer")):
        return None
    if session.get("status") != "complete" or session.get("payment_status") not in {"paid", "no_payment_required"}:
        return None

    email = _checkout_session_email(session)
    normalized_expected_email = _normalize_email(expected_email)
    if "@" not in email or (normalized_expected_email and email != normalized_expected_email):
        return None

    metadata = _stripe_metadata(session)
    metadata_pack = _vision_pack_from_metadata(metadata)
    has_vision_metadata = _has_vision_plan_metadata(metadata)
    if has_vision_metadata and not metadata_pack:
        return None

    legacy = metadata_pack is None
    if legacy and not allow_legacy:
        return None
    pack = metadata_pack or _pack_by_exact_id("studio")
    if not pack:
        return None
    if not _stripe_amounts_match_pack(
        session,
        pack,
        subtotal_keys=("amount_subtotal",),
        total_keys=("amount_total",),
        require_amount=legacy,
    ):
        return None

    metadata_email = _normalize_email(metadata.get("vision_user_email"))
    if metadata_email and metadata_email != email:
        return None
    metadata_user_id = str(metadata.get("vision_user_id") or "").strip()
    client_reference_id = str(session.get("client_reference_id") or "").strip()
    if metadata_user_id and client_reference_id and metadata_user_id != client_reference_id:
        return None
    return pack, legacy


def _validated_vision_subscription(
    subscription: dict[str, Any],
    *,
    expected_pack: dict[str, Any] | None = None,
    expected_customer_id: str | None = None,
    expected_email: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(subscription, dict):
        return None
    if subscription.get("object") not in {None, "subscription"}:
        return None
    if not _stripe_object_id(subscription):
        return None
    customer_id = _stripe_object_id(subscription.get("customer"))
    if not customer_id:
        return None
    if expected_customer_id and customer_id and customer_id != expected_customer_id:
        return None
    status = str(subscription.get("status") or "").strip().lower()
    if status not in {"active", "trialing", "past_due", "canceled", "unpaid", "paused", "incomplete", "incomplete_expired"}:
        return None

    metadata = _stripe_metadata(subscription)
    metadata_pack = _vision_pack_from_metadata(metadata)
    has_vision_metadata = _has_vision_plan_metadata(metadata)
    if has_vision_metadata and not metadata_pack:
        return None
    if metadata_pack and expected_pack and metadata_pack.get("id") != expected_pack.get("id"):
        return None

    legacy = metadata_pack is None
    if legacy and not allow_legacy:
        return None
    pack = metadata_pack or expected_pack or (_pack_by_exact_id("studio") if allow_legacy else None)
    if not pack or not _subscription_price_matches_pack(subscription, pack, require_price=legacy):
        return None
    metadata_email = _normalize_email(metadata.get("vision_user_email"))
    if metadata_email and expected_email and metadata_email != _normalize_email(expected_email):
        return None
    return pack


def _stripe_timestamp_iso(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _subscription_period_timestamp(subscription: dict[str, Any], key: str) -> int | None:
    direct = subscription.get(key)
    try:
        if direct is not None:
            return int(direct)
    except (TypeError, ValueError):
        pass
    items = ((subscription.get("items") or {}).get("data") or []) if isinstance(subscription.get("items"), dict) else []
    candidates: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(int(item.get(key)))
        except (TypeError, ValueError):
            continue
    return max(candidates) if candidates else None


def _subscription_summary(subscription: dict[str, Any], *, customer_id: str | None = None) -> dict[str, Any]:
    status = str(subscription.get("status") or "unknown").strip().lower() or "unknown"
    items = ((subscription.get("items") or {}).get("data") or []) if isinstance(subscription.get("items"), dict) else []
    first_item = next((item for item in items if isinstance(item, dict)), {})
    price = first_item.get("price") if isinstance(first_item.get("price"), dict) else {}
    recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
    current_period_start = _subscription_period_timestamp(subscription, "current_period_start")
    current_period_end = _subscription_period_timestamp(subscription, "current_period_end")
    resolved_customer_id = customer_id or _stripe_object_id(subscription.get("customer"))
    return {
        "id": _stripe_object_id(subscription),
        "customer_id": resolved_customer_id or None,
        "status": status,
        "active": status in {"active", "trialing"},
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")),
        "current_period_start": current_period_start,
        "current_period_start_iso": _stripe_timestamp_iso(current_period_start),
        "current_period_end": current_period_end,
        "current_period_end_iso": _stripe_timestamp_iso(current_period_end),
        "canceled_at": subscription.get("canceled_at"),
        "canceled_at_iso": _stripe_timestamp_iso(subscription.get("canceled_at")),
        "price_id": _stripe_object_id(price) or None,
        "currency": str(price.get("currency") or "").lower() or None,
        "interval": str(recurring.get("interval") or "").lower() or None,
    }


def _stripe_billing_context_for_user(
    *,
    user: dict[str, Any],
    access_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not os.environ.get("STRIPE_SECRET_KEY", "").strip():
        return {"customer_id": None, "subscription": None, "lookup_failed": True}
    email = _normalize_email(user.get("email"))
    if not email:
        return {"customer_id": None, "subscription": None, "lookup_failed": False}

    sessions: list[dict[str, Any]] = []
    seen_session_ids: set[str] = set()
    lookup_failed = False
    recent_access_sessions = list(reversed(list((access_entry or {}).get("stripe_sessions") or [])))[:10]
    anchored_session_ids = {
        str(session_id or "").strip()
        for session_id in recent_access_sessions
        if str(session_id or "").strip() and not str(session_id or "").strip().startswith("invoice:")
    }
    for session_id in recent_access_sessions:
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id or clean_session_id.startswith("invoice:") or clean_session_id in seen_session_ids:
            continue
        try:
            session = _retrieve_stripe_checkout_session(clean_session_id)
        except RuntimeError:
            lookup_failed = True
            continue
        if isinstance(session, dict) and _stripe_object_id(session) == clean_session_id:
            sessions.append(session)
            seen_session_ids.add(clean_session_id)

    try:
        listed_sessions = _list_stripe_checkout_sessions_by_email(email, limit=20)
    except RuntimeError:
        lookup_failed = True
        listed_sessions = []
    for session in listed_sessions:
        session_id = _stripe_object_id(session)
        if session_id and session_id not in seen_session_ids:
            sessions.append(session)
            seen_session_ids.add(session_id)

    sessions.sort(key=lambda item: int(item.get("created") or 0), reverse=True)
    fallback_customer_id: str | None = None
    fallback_subscription: dict[str, Any] | None = None
    for session in sessions:
        validated_session = _validated_vision_checkout_session(
            session,
            expected_email=email,
            allow_legacy=_stripe_object_id(session) in anchored_session_ids,
        )
        if not validated_session:
            continue
        session_pack, session_is_legacy = validated_session
        customer_id = _stripe_object_id(session.get("customer")) or None
        subscription_value = session.get("subscription")
        subscription_id = _stripe_object_id(subscription_value)
        if not subscription_id:
            continue
        if isinstance(subscription_value, dict):
            subscription = subscription_value
        else:
            try:
                subscription = _retrieve_stripe_subscription(subscription_id)
            except RuntimeError:
                lookup_failed = True
                continue
        if not isinstance(subscription, dict):
            continue
        subscription_pack = _validated_vision_subscription(
            subscription,
            expected_pack=session_pack,
            expected_customer_id=customer_id,
            expected_email=email,
            # A validated legacy checkout is the compatibility anchor for an
            # old subscription without Vision metadata. Current subscriptions
            # are expected to carry the metadata copied at checkout.
            allow_legacy=session_is_legacy,
        )
        if not subscription_pack:
            continue
        summary = _subscription_summary(subscription, customer_id=customer_id)
        resolved_customer_id = customer_id or str(summary.get("customer_id") or "").strip() or None
        fallback_customer_id = fallback_customer_id or resolved_customer_id
        if summary.get("active"):
            return {"customer_id": resolved_customer_id, "subscription": summary, "lookup_failed": lookup_failed}
        if fallback_subscription is None:
            fallback_subscription = summary

    return {
        "customer_id": fallback_customer_id,
        "subscription": fallback_subscription,
        "lookup_failed": lookup_failed,
    }


def _stripe_billing_cache_ttl_seconds() -> int:
    try:
        return max(15, min(300, int(os.environ.get("STRIPE_BILLING_CACHE_TTL_SECONDS", "60"))))
    except ValueError:
        return 60


def _invalidate_stripe_billing_cache(email: str | None) -> None:
    normalized = _normalize_email(email)
    if not normalized:
        return
    with STRIPE_BILLING_CACHE_LOCK:
        STRIPE_BILLING_CACHE.pop(normalized, None)


def _cached_stripe_billing_context_for_user(
    *,
    user: dict[str, Any],
    access_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    email = _normalize_email(user.get("email"))
    if not email:
        return {"customer_id": None, "subscription": None, "lookup_failed": False}
    now = datetime.now(timezone.utc).timestamp()
    with STRIPE_BILLING_CACHE_LOCK:
        cached = STRIPE_BILLING_CACHE.get(email)
        if cached and float(cached.get("expires_at") or 0) > now:
            return copy.deepcopy(cached.get("context") or {})
    context = _stripe_billing_context_for_user(user=user, access_entry=access_entry)
    with STRIPE_BILLING_CACHE_LOCK:
        STRIPE_BILLING_CACHE[email] = {
            "expires_at": now + _stripe_billing_cache_ttl_seconds(),
            "context": copy.deepcopy(context),
        }
    return context


def _validate_checkout_session_for_user(
    session: dict[str, Any],
    user: dict[str, Any],
    *,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    user_id = str(user.get("id") or "").strip()
    user_email = _normalize_email(user.get("email"))
    validated_session = _validated_vision_checkout_session(
        session,
        expected_email=user_email,
        allow_legacy=allow_legacy,
    )
    if not validated_session:
        raise HTTPException(status_code=409, detail="This Stripe session is not a valid Vision Studio checkout.")
    pack, _ = validated_session
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    client_reference_id = str(session.get("client_reference_id") or "").strip()
    metadata_user_id = str(metadata.get("vision_user_id") or "").strip()
    metadata_user_email = _normalize_email(metadata.get("vision_user_email"))
    checkout_email = _checkout_session_email(session)

    if not user_id or not user_email:
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before confirming checkout.")
    if checkout_email != user_email:
        raise HTTPException(status_code=403, detail="This checkout belongs to a different Vision account.")
    if client_reference_id and client_reference_id != user_id:
        raise HTTPException(status_code=403, detail="This checkout belongs to a different Vision account.")
    if metadata_user_id and metadata_user_id != user_id:
        raise HTTPException(status_code=403, detail="This checkout belongs to a different Vision account.")
    if metadata_user_email and metadata_user_email != user_email:
        raise HTTPException(status_code=403, detail="This checkout belongs to a different Vision account.")
    if not client_reference_id and not metadata_user_id and not metadata_user_email:
        # Compatibility for sessions created before account binding was deployed:
        # possession of the session id is insufficient; the verified checkout email must match.
        if checkout_email != user_email:
            raise HTTPException(status_code=403, detail="This checkout belongs to a different Vision account.")

    return pack


def _subscription_value_from_invoice(invoice: dict[str, Any]) -> Any:
    subscription = invoice.get("subscription")
    parent = invoice.get("parent")
    if not subscription and isinstance(parent, dict):
        parent_subscription_details = parent.get("subscription_details")
        if isinstance(parent_subscription_details, dict):
            subscription = parent_subscription_details.get("subscription")
    if subscription:
        return subscription

    lines_container = invoice.get("lines")
    lines = (lines_container.get("data") or []) if isinstance(lines_container, dict) else []
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_parent = line.get("parent")
        if not isinstance(line_parent, dict):
            continue
        details = line_parent.get("subscription_item_details")
        if isinstance(details, dict) and details.get("subscription"):
            return details.get("subscription")
    return None


def _subscription_metadata_sources_from_invoice(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    parent = invoice.get("parent")
    if isinstance(parent, dict):
        parent_subscription_details = parent.get("subscription_details")
        if isinstance(parent_subscription_details, dict) and isinstance(parent_subscription_details.get("metadata"), dict):
            sources.append(parent_subscription_details["metadata"])
    subscription_details = invoice.get("subscription_details")
    if isinstance(subscription_details, dict) and isinstance(subscription_details.get("metadata"), dict):
        sources.append(subscription_details["metadata"])
    subscription = _subscription_value_from_invoice(invoice)
    if isinstance(subscription, dict):
        metadata = _stripe_metadata(subscription)
        if metadata:
            sources.append(metadata)
    return sources


def _canonical_vision_pack_metadata(
    pack: dict[str, Any],
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        str(key): value
        for key, value in (source_metadata or {}).items()
        if str(key).startswith("vision_tracking_") or key in {"vision_user_id", "vision_user_email"}
    }
    metadata.update(
        {
            "vision_pack_id": str(pack.get("id") or ""),
            "vision_pack_name": str(pack.get("name") or ""),
            "vision_pack_vision_credits": str(int(pack.get("vision_credits") or 0)),
            "vision_pack_video_credits": str(int(pack.get("video_credits") or 0)),
            "vision_pack_image_credits": str(int(pack.get("image_credits") or 0)),
        }
    )
    return metadata


def _email_from_stripe_invoice(invoice: dict[str, Any]) -> str | None:
    for key in ("customer_email", "account_email"):
        value = invoice.get(key)
        if value:
            return str(value)
    customer = invoice.get("customer")
    if isinstance(customer, dict):
        return str(customer.get("email") or "") or None
    if isinstance(customer, str) and customer:
        try:
            fetched_customer = _retrieve_stripe_customer(customer)
        except RuntimeError:
            return None
        return str(fetched_customer.get("email") or "") or None
    return None


def _validated_vision_invoice(
    invoice: dict[str, Any],
    *,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(invoice, dict):
        return None
    if invoice.get("object") not in {None, "invoice"}:
        return None
    if str(invoice.get("status") or "").strip().lower() != "paid":
        return None
    if not str(invoice.get("billing_reason") or "").strip().lower().startswith("subscription_"):
        return None

    subscription_value = _subscription_value_from_invoice(invoice)
    subscription_id = _stripe_object_id(subscription_value)
    if not subscription_id:
        return None
    email = _normalize_email(_email_from_stripe_invoice(invoice))
    if "@" not in email:
        return None

    metadata_sources = _subscription_metadata_sources_from_invoice(invoice)
    pack: dict[str, Any] | None = None
    source_metadata: dict[str, Any] = {}
    for metadata in metadata_sources:
        candidate_pack = _vision_pack_from_metadata(metadata)
        if _has_vision_plan_metadata(metadata) and not candidate_pack:
            return None
        if candidate_pack:
            if pack and candidate_pack.get("id") != pack.get("id"):
                return None
            pack = candidate_pack
            source_metadata.update(metadata)
        metadata_email = _normalize_email(metadata.get("vision_user_email"))
        if metadata_email and metadata_email != email:
            return None

    subscription: dict[str, Any] | None = subscription_value if isinstance(subscription_value, dict) else None
    if subscription is None:
        try:
            fetched_subscription = _retrieve_stripe_subscription(subscription_id)
        except RuntimeError:
            fetched_subscription = None
        if isinstance(fetched_subscription, dict) and _stripe_object_id(fetched_subscription) == subscription_id:
            subscription = fetched_subscription

    if subscription is not None:
        subscription_metadata = _stripe_metadata(subscription)
        candidate_pack = _validated_vision_subscription(
            subscription,
            expected_pack=pack,
            expected_customer_id=_stripe_object_id(invoice.get("customer")) or None,
            expected_email=email,
            allow_legacy=allow_legacy and pack is None,
        )
        if not candidate_pack:
            return None
        if pack and candidate_pack.get("id") != pack.get("id"):
            return None
        pack = candidate_pack
        if subscription_metadata:
            source_metadata.update(subscription_metadata)

    legacy = pack is None
    if legacy and not allow_legacy:
        return None
    pack = pack or _pack_by_exact_id("studio")
    if not pack:
        return None
    if not _stripe_amounts_match_pack(
        invoice,
        pack,
        subtotal_keys=("subtotal", "subtotal_excluding_tax"),
        total_keys=("total", "amount_paid", "amount_due"),
        require_amount=legacy,
    ):
        return None
    return {
        "pack": pack,
        "email": email,
        "subscription": subscription,
        "metadata": _canonical_vision_pack_metadata(pack, source_metadata),
        "legacy": legacy,
    }


def _restore_access_for_email(*, email: str, current_access_id: str | None, current_user_id: str | None) -> dict[str, Any] | None:
    sessions = _list_stripe_checkout_sessions_by_email(email)
    restored_entry: dict[str, Any] | None = None
    current_entry = ACCESS.get(str(current_access_id)) if current_access_id else None
    anchored_session_ids = {
        str(session_id or "").strip()
        for session_id in list((current_entry or {}).get("stripe_sessions") or [])
        if str(session_id or "").strip() and not str(session_id or "").strip().startswith("invoice:")
    }
    for session in sessions:
        session_id = str(session.get("id") or "").strip()
        validated_session = _validated_vision_checkout_session(
            session,
            expected_email=email,
            allow_legacy=session_id in anchored_session_ids,
        )
        if not validated_session:
            continue
        pack, session_is_legacy = validated_session
        if not session_id:
            continue
        subscription_value = session.get("subscription")
        subscription_id = _stripe_object_id(subscription_value)
        try:
            subscription = (
                subscription_value
                if isinstance(subscription_value, dict)
                else _retrieve_stripe_subscription(subscription_id)
            )
        except RuntimeError:
            continue
        if not isinstance(subscription, dict):
            continue
        subscription_pack = _validated_vision_subscription(
            subscription,
            expected_pack=pack,
            expected_customer_id=_stripe_object_id(session.get("customer")) or None,
            expected_email=email,
            allow_legacy=session_is_legacy,
        )
        if not subscription_pack or not _subscription_summary(subscription).get("active"):
            continue
        vision_credits, video_credits, image_credits = _credits_for_validated_pack(subscription_pack)
        if vision_credits <= 0 and video_credits <= 0 and image_credits <= 0:
            continue
        restored_entry = ACCESS.apply_paid_session(
            session_id=session_id,
            email=email,
            current_access_id=current_access_id,
            current_user_id=current_user_id,
            vision_credits=vision_credits,
            video_credits=video_credits,
            image_credits=image_credits,
        )
    return restored_entry


def _access_summary(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {
            "has_access": False,
            "admin": False,
            "vision_credits_remaining": 0,
            "vision_credits_purchased": 0,
            "video_remaining": 0,
            "image_remaining": 0,
            "access_id": None,
        }
    is_admin = bool(entry.get("admin"))
    vision_remaining = None if is_admin else int(entry.get("vision_credits_remaining", 0))
    vision_purchased = None if is_admin else int(entry.get("vision_credits_purchased", 0))
    video_remaining = None if is_admin else int(entry.get("video_remaining", 0))
    image_remaining = None if is_admin else int(entry.get("image_remaining", 0))
    return {
        "has_access": is_admin or (vision_remaining or 0) > 0 or (video_remaining or 0) > 0 or (image_remaining or 0) > 0,
        "admin": is_admin,
        "vision_credits_remaining": vision_remaining,
        "vision_credits_purchased": vision_purchased,
        "video_remaining": video_remaining,
        "image_remaining": image_remaining,
        "access_id": entry.get("id"),
    }


def _without_paid_access(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("admin"):
        return summary
    return {
        **summary,
        "has_access": False,
        "vision_credits_remaining": 0,
        "vision_credits_purchased": 0,
        "video_remaining": 0,
        "image_remaining": 0,
    }


def _account_entitlement_summary(
    entry: dict[str, Any] | None,
    user: dict[str, Any] | None,
    *,
    billing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _access_summary(entry)
    if summary.get("admin") or not summary.get("has_access") or not entry:
        return summary

    bound_user_id = str(entry.get("user_id") or "").strip()
    bound_email = _normalize_email(entry.get("email"))
    current_user_id = str((user or {}).get("id") or "").strip()
    current_email = _normalize_email((user or {}).get("email"))
    if bound_user_id and bound_user_id != current_user_id:
        return _without_paid_access(summary)
    if bound_email and current_email and bound_email != current_email:
        return _without_paid_access(summary)

    if not user:
        return summary
    if billing_context is not None:
        context = billing_context
    else:
        if not os.environ.get("STRIPE_SECRET_KEY", "").strip():
            return summary
        try:
            context = _cached_stripe_billing_context_for_user(user=user, access_entry=entry)
        except Exception:
            return summary
    subscription = context.get("subscription") if isinstance(context, dict) else None
    # A successfully resolved Vision subscription is authoritative even when a
    # different Stripe lookup failed. Do not let a partial outage resurrect a
    # subscription we already know is canceled or otherwise inactive.
    if isinstance(subscription, dict) and not subscription.get("active"):
        return _without_paid_access(summary)
    return summary


def _request_entitlement(
    request: Request,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    access = _access_from_request(request)
    user = _user_from_request(request)
    billing_context = None
    if user and access and not access.get("admin"):
        try:
            billing_context = _cached_stripe_billing_context_for_user(user=user, access_entry=access)
        except Exception:
            billing_context = None
    return user, access, _account_entitlement_summary(access, user, billing_context=billing_context)


def _pack_summary(pack_id: str | None = None) -> dict[str, Any]:
    return copy.deepcopy(_pack_by_id(pack_id))


def _access_token_payload(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("admin"):
        return {"admin": True}
    return {"access_id": entry["id"]}


def _access_token_for_entry(entry: dict[str, Any]) -> str:
    return _sign_access_token(_access_token_payload(entry))


def _admin_access_entry() -> dict[str, Any]:
    return {
        "id": "admin",
        "admin": True,
        "vision_credits_remaining": None,
        "vision_credits_purchased": None,
        "video_remaining": None,
        "image_remaining": None,
    }


def _user_summary(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {
            "authenticated": False,
            "user_id": None,
            "email": None,
            "signup_discount_percent": _signup_discount_percent(),
        }
    return {
        "authenticated": True,
        "user_id": user.get("id"),
        "email": user.get("email"),
        "signup_discount_percent": _signup_discount_percent(),
    }


def _user_token_for_user(user: dict[str, Any] | None) -> str | None:
    user_id = str((user or {}).get("id") or "").strip()
    if not user_id:
        return None
    return _sign_user_token({
        "user_id": user_id,
        "email": _normalize_email((user or {}).get("email")) or None,
    })


def _request_user_token(request: Request) -> str | None:
    header_token = request.headers.get("x-vision-user", "").strip()
    if header_token:
        return header_token
    cookie_token = request.cookies.get(_user_cookie_name())
    return cookie_token or None


def _user_from_request(request: Request) -> dict[str, Any] | None:
    token = _request_user_token(request)
    payload = _verify_user_token(token)
    if not payload:
        return None
    user_id = str(payload.get("user_id") or "")
    if not user_id:
        return None
    stored_user = USERS.get(user_id)
    if stored_user:
        return stored_user
    token_email = _normalize_email(payload.get("email"))
    if "@" not in token_email:
        return None
    # The signed identity remains usable across an ephemeral local user-store
    # restart; paid access is still resolved from the persistent access store.
    return {
        "id": user_id,
        "email": token_email,
    }


def _request_access_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    header_token = request.headers.get("x-vision-access", "").strip()
    if header_token:
        return header_token
    cookie_token = request.cookies.get(_access_cookie_name())
    return cookie_token or None


def _test_admin_from_request(request: Request) -> dict[str, Any] | None:
    provided = request.headers.get("x-vision-test-admin", "").strip()
    if not provided:
        return None
    configured = (
        os.environ.get("VISION_TEST_ADMIN_TOKEN", "").strip()
        or os.environ.get("VISION_ADMIN_TOKEN", "").strip()
    )
    if configured and hmac.compare_digest(provided, configured):
        return _admin_access_entry()
    return None


def _access_from_request(request: Request) -> dict[str, Any] | None:
    test_admin = _test_admin_from_request(request)
    if test_admin:
        return test_admin

    token = _request_access_token(request)
    payload = _verify_access_token(token)
    if payload:
        if payload.get("admin"):
            return _admin_access_entry()
        access_id = str(payload.get("access_id") or "")
        if access_id:
            entry = ACCESS.get(access_id)
            if entry:
                return entry

    user = _user_from_request(request)
    if not user:
        return None
    user_entry = ACCESS.find_by_user_id(str(user.get("id")))
    if user_entry:
        return user_entry
    email_entry = ACCESS.find_by_email(user.get("email"))
    if email_entry:
        attached = ACCESS.attach_user(
            email_entry["id"],
            user_id=str(user.get("id")),
            email=str(user.get("email") or ""),
        )
        return attached or email_entry
    return None



def _candidate_generation_routes(prompt: str, quality: str, job_id: str, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    seedance_state = seedance_status()
    google_state = _google_status()
    kling_api_state = kling_api_status()
    kling_state = kling_session_bridge_status()
    generation_settings = settings or {}
    default_provider = _normalize_generation_provider(generation_settings.get("provider")) if generation_settings.get("provider") else _default_generation_provider()
    quality_candidates = _quality_candidates_for_prompt(quality)
    selected_duration = _normalize_duration_seconds(generation_settings.get("duration_seconds"))
    selected_resolution = _normalize_resolution(generation_settings.get("resolution"))
    selected_aspect_ratio = _normalize_aspect_ratio(generation_settings.get("aspect_ratio"))
    selected_sound = bool(generation_settings.get("sound_enabled"))
    google_resolution = selected_resolution if selected_resolution in {"720p", "1080p", "4k"} else "720p"
    seedance_resolution = selected_resolution if selected_resolution in {"480p", "720p", "1080p"} else "1080p"
    allowed_providers = {
        "auto": {"google", "seedance", "kling"},
        "google": {"google"},
        "seedance": {"seedance"},
        "kling": {"kling"},
    }.get(default_provider, {"google", "seedance", "kling"})
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for candidate_quality in quality_candidates:
        provider_priority = _provider_priority_for_prompt(prompt, candidate_quality)
        if default_provider == "auto" and _kling_api_first_enabled() and kling_api_state.get("ready"):
            provider_priority = ["kling", *[provider for provider in provider_priority if provider != "kling"]]
        for provider_name in provider_priority:
            if provider_name not in allowed_providers:
                continue
            if (
                default_provider == "auto"
                and provider_name == "google"
                and selected_resolution != "4k"
                and selected_duration not in {4, 6, 8}
                and seedance_state.get("ready")
            ):
                continue
            if provider_name == "google" and google_state["video"].get("ready"):
                model_name = _google_video_model_for_quality(candidate_quality)
                if model_name:
                    route = {
                        "provider": "google_veo",
                        "quality": candidate_quality,
                        "model": model_name,
                        "fallback_models": _google_fallback_models_for_quality(candidate_quality),
                        "aspect_ratio": selected_aspect_ratio,
                        "resolution": google_resolution or _google_resolution_for_quality(candidate_quality),
                        "duration": selected_duration or _google_duration_for_quality(candidate_quality),
                    }
                    route_key = (route["provider"], route["quality"], route["model"])
                    if route_key not in seen:
                        seen.add(route_key)
                        routes.append(route)
            if provider_name == "seedance" and seedance_state.get("ready"):
                model_name = _seedance_model_for_quality(candidate_quality)
                if model_name:
                    route = {
                        "provider": "byteplus_seedance",
                        "quality": candidate_quality,
                        "model": model_name,
                        "aspect_ratio": selected_aspect_ratio,
                        "resolution": seedance_resolution or _seedance_resolution_for_quality(candidate_quality),
                        "duration": selected_duration,
                    }
                    route_key = (route["provider"], route["quality"], route["model"])
                    if route_key not in seen:
                        seen.add(route_key)
                        routes.append(route)
            if provider_name == "kling" and kling_api_state.get("ready"):
                route = {
                    "provider": "kling_api",
                    "quality": candidate_quality,
                    "model": os.environ.get("KLING_API_VIDEO_MODEL", "kling-v3-omni"),
                    "resolution": selected_resolution,
                    "duration": selected_duration,
                    "aspect_ratio": selected_aspect_ratio,
                    "sound_enabled": selected_sound,
                }
                route_key = (route["provider"], route["quality"], route["model"])
                if route_key not in seen:
                    seen.add(route_key)
                    routes.append(route)
            if provider_name == "kling" and kling_state.get("ready"):
                route = {
                    "provider": "kling_web_session_bridge",
                    "quality": candidate_quality,
                    "model": os.environ.get("WORLDSIM_KLING_MODEL", "kling-2.6-pro"),
                    "resolution": "1080p",
                    "duration": selected_duration,
                    "aspect_ratio": selected_aspect_ratio,
                    "sound_enabled": selected_sound,
                }
                route_key = (route["provider"], route["quality"], route["model"])
                if route_key not in seen:
                    seen.add(route_key)
                    routes.append(route)

    if routes:
        return routes

    if default_provider == "seedance":
        raise RuntimeError("Seedance is not ready yet for this Vision deployment.")
    if default_provider == "google":
        raise RuntimeError("Google Veo is not ready yet for this Vision deployment.")
    if default_provider == "kling":
        raise RuntimeError("Kling is not ready yet for this Vision deployment.")
    raise SessionBridgeNotReadyError("No ready generation provider is available for Vision right now.")


def _select_generation_route(prompt: str, quality: str, job_id: str) -> dict[str, str]:
    return _candidate_generation_routes(prompt, quality, job_id)[0]


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
ACCESS_FILE = RUNTIME_ROOT / "access.json"
OUTPUT_ROOT = VISION_ROOT / "generated"
DISABLE_FILE = RUNTIME_ROOT / "gateway.disabled"
USERS_FILE = RUNTIME_ROOT / "users.json"
TRACKING_DEBUG_EVENTS_FILE = RUNTIME_ROOT / "tracking_events.debug.jsonl"

for path in (RUNTIME_ROOT, OUTPUT_ROOT):
    path.mkdir(parents=True, exist_ok=True)

APP.mount("/generated", StaticFiles(directory=str(OUTPUT_ROOT)), name="generated")


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=5000)
    quality: str | None = Field(default=None, min_length=4, max_length=16)
    provider: str | None = Field(default=None, min_length=4, max_length=16)
    mode: str | None = Field(default="image", min_length=5, max_length=16)
    duration_seconds: int | None = Field(default=None, ge=3, le=15)
    resolution: str | None = Field(default=None, min_length=2, max_length=8)
    aspect_ratio: str | None = Field(default=None, min_length=3, max_length=16)
    sound_enabled: bool | None = False


class CreateCheckoutSessionRequest(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    pack_id: str | None = Field(default=None, max_length=32)
    return_path: str | None = Field(default=None, max_length=512)
    tracking: dict[str, Any] | None = None


class ConfirmCheckoutRequest(BaseModel):
    session_id: str = Field(min_length=10, max_length=255)


class CreateCustomerPortalSessionRequest(BaseModel):
    return_path: str | None = Field(default=None, max_length=512)


class AdminUnlockRequest(BaseModel):
    token: str = Field(min_length=8, max_length=255)


class ImprovePromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1500)
    mode: str | None = Field(default="image", min_length=5, max_length=16)


class RequestAuthCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class VerifyAuthCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=4, max_length=12)


class TrackEventRequest(BaseModel):
    event_name: str = Field(min_length=3, max_length=64)
    event_id: str | None = Field(default=None, max_length=128)
    event_time: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    anonymous_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    page_path: str | None = Field(default=None, max_length=512)
    page_url: str | None = Field(default=None, max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)
    utm_source: str | None = Field(default=None, max_length=256)
    utm_medium: str | None = Field(default=None, max_length=256)
    utm_campaign: str | None = Field(default=None, max_length=256)
    utm_content: str | None = Field(default=None, max_length=256)
    utm_term: str | None = Field(default=None, max_length=256)
    gclid: str | None = Field(default=None, max_length=512)
    fbclid: str | None = Field(default=None, max_length=512)
    ttclid: str | None = Field(default=None, max_length=512)
    plan_id: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, max_length=16)
    value: float | None = None
    job_id: str | None = Field(default=None, max_length=128)
    asset_id: str | None = Field(default=None, max_length=512)
    media_type: str | None = Field(default=None, max_length=32)
    platform_context: str | None = Field(default="web", max_length=64)
    checkout_session_id: str | None = Field(default=None, max_length=255)
    customer_email: str | None = Field(default=None, max_length=320)
    first_touch: dict[str, Any] | None = None
    last_touch: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


CANONICAL_TRACKING_EVENTS = {
    "LandingViewed",
    "StudioViewed",
    "PackSelected",
    "CheckoutStarted",
    "PurchaseCompleted",
    "PromptImproved",
    "GenerateStarted",
    "GenerateCompleted",
    "AssetDownloaded",
    "ViewerOpened",
    "ReferenceUploaded",
    "LoginStarted",
    "AccessCodeRequested",
    "AccessGranted",
}

TRACKING_CORE_FIELDS = [
    "event_name",
    "event_id",
    "event_time",
    "session_id",
    "anonymous_id",
    "user_id",
    "page_path",
    "page_url",
    "referrer",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "gclid",
    "fbclid",
    "ttclid",
    "plan_id",
    "currency",
    "value",
    "job_id",
    "asset_id",
    "media_type",
    "platform_context",
    "checkout_session_id",
    "customer_email",
]

TRACKING_ATTRIBUTION_FIELDS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "gclid",
    "fbclid",
    "ttclid",
    "page_path",
    "page_url",
    "referrer",
]

TRACKING_PII_FIELD_TOKENS = {"email", "phone", "name"}


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _safe_tracking_string(value: Any, max_length: int = 2048) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _safe_tracking_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, raw_value in value.items():
        if raw_value is None:
            continue
        key_text = str(key)[:80]
        lowered_key = key_text.lower()
        if any(token in lowered_key for token in TRACKING_PII_FIELD_TOKENS):
            continue
        if isinstance(raw_value, (str, int, float, bool)):
            safe[key_text] = raw_value
        elif isinstance(raw_value, dict):
            safe[key_text] = _safe_tracking_dict(raw_value)
        elif isinstance(raw_value, list):
            safe[key_text] = [item for item in raw_value if isinstance(item, (str, int, float, bool))][:20]
    return safe


def _sha256_normalized(value: str | None) -> str | None:
    normalized = _normalize_email(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class TrackingStoreUnavailable(RuntimeError):
    pass


def _is_usable_database_url(value: str) -> bool:
    clean = value.strip()
    if not clean:
        return False
    if clean.startswith("<") and clean.endswith(">"):
        return False
    if clean.startswith(("postgres://", "postgresql://", "sqlite:///")):
        return True
    return "=" in clean and any(part in clean for part in ("host=", "dbname=", "user="))


def _tracking_database_url() -> str:
    for env_name in ("TRACKING_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(env_name, "").strip()
        if _is_usable_database_url(value):
            return value
    return ""


def _safe_tracking_error(value: str) -> str:
    text = str(value or "")
    database_url = _tracking_database_url()
    if database_url:
        text = text.replace(database_url, "[database-url]")
    text = re.sub(r"(postgres(?:ql)?://)[^@\s]+@", r"\1[credentials]@", text)
    return text[:500]


def _tracking_cookie_session_name() -> str:
    return os.environ.get("VISION_TRACKING_SESSION_COOKIE", "vision_tracking_session_id").strip() or "vision_tracking_session_id"


def _tracking_cookie_anonymous_name() -> str:
    return os.environ.get("VISION_TRACKING_ANONYMOUS_COOKIE", "vision_tracking_anonymous_id").strip() or "vision_tracking_anonymous_id"


def _tracking_attribution_key(event: dict[str, Any]) -> str | None:
    anonymous_id = _safe_tracking_string(event.get("anonymous_id"), 128)
    session_id = _safe_tracking_string(event.get("session_id"), 128)
    if anonymous_id:
        return f"anon:{anonymous_id}"
    if session_id:
        return f"session:{session_id}"
    return None


def _touch_from_event(event: dict[str, Any]) -> dict[str, Any]:
    touch = {key: event.get(key) for key in TRACKING_ATTRIBUTION_FIELDS if event.get(key)}
    if touch and not touch.get("captured_at"):
        touch["captured_at"] = event.get("event_time") or _now_iso()
    return touch


def _scrub_tracking_event(event: dict[str, Any]) -> dict[str, Any]:
    clean = dict(event)
    raw_email = clean.pop("customer_email", None)
    email_hash = clean.get("customer_email_hash") or _sha256_normalized(raw_email)
    if email_hash:
        clean["customer_email_hash"] = email_hash
    clean["first_touch"] = _safe_tracking_dict(clean.get("first_touch"))
    clean["last_touch"] = _safe_tracking_dict(clean.get("last_touch"))
    clean["payload"] = _safe_tracking_dict(clean.get("payload"))
    return clean


class TrackingEventStore:
    def append(self, event: dict[str, Any]) -> bool:
        raise NotImplementedError

    def get_attribution(self, *, session_id: str | None, anonymous_id: str | None) -> dict[str, Any] | None:
        return None


class UnconfiguredTrackingEventStore(TrackingEventStore):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def append(self, event: dict[str, Any]) -> bool:
        raise TrackingStoreUnavailable(self.reason)


class SqliteTrackingEventStore(TrackingEventStore):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vision_tracking_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_name TEXT NOT NULL,
                    event_time TEXT,
                    received_at TEXT,
                    session_id TEXT,
                    anonymous_id TEXT,
                    user_id TEXT,
                    page_path TEXT,
                    page_url TEXT,
                    referrer TEXT,
                    utm_source TEXT,
                    utm_medium TEXT,
                    utm_campaign TEXT,
                    utm_content TEXT,
                    utm_term TEXT,
                    gclid TEXT,
                    fbclid TEXT,
                    ttclid TEXT,
                    plan_id TEXT,
                    currency TEXT,
                    value REAL,
                    job_id TEXT,
                    asset_id TEXT,
                    media_type TEXT,
                    platform_context TEXT,
                    checkout_session_id TEXT,
                    customer_email_hash TEXT,
                    first_touch TEXT NOT NULL DEFAULT '{}',
                    last_touch TEXT NOT NULL DEFAULT '{}',
                    payload TEXT NOT NULL DEFAULT '{}',
                    event_json TEXT NOT NULL DEFAULT '{}',
                    ip TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_time ON vision_tracking_events(event_time)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_name_time ON vision_tracking_events(event_name, event_time)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_session ON vision_tracking_events(session_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_anonymous ON vision_tracking_events(anonymous_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_checkout ON vision_tracking_events(checkout_session_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_user ON vision_tracking_events(user_id)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vision_attribution (
                    attribution_key TEXT PRIMARY KEY,
                    session_id TEXT,
                    anonymous_id TEXT,
                    user_id TEXT,
                    first_touch TEXT NOT NULL DEFAULT '{}',
                    last_touch TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_attribution_session ON vision_attribution(session_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_vision_attribution_anonymous ON vision_attribution(anonymous_id)")

    def append(self, event: dict[str, Any]) -> bool:
        clean = _scrub_tracking_event(event)
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO vision_tracking_events (
                    event_id, event_name, event_time, received_at, session_id, anonymous_id, user_id,
                    page_path, page_url, referrer, utm_source, utm_medium, utm_campaign, utm_content,
                    utm_term, gclid, fbclid, ttclid, plan_id, currency, value, job_id, asset_id,
                    media_type, platform_context, checkout_session_id, customer_email_hash,
                    first_touch, last_touch, payload, event_json, ip, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean.get("event_id"),
                    clean.get("event_name"),
                    clean.get("event_time"),
                    clean.get("received_at"),
                    clean.get("session_id"),
                    clean.get("anonymous_id"),
                    clean.get("user_id"),
                    clean.get("page_path"),
                    clean.get("page_url"),
                    clean.get("referrer"),
                    clean.get("utm_source"),
                    clean.get("utm_medium"),
                    clean.get("utm_campaign"),
                    clean.get("utm_content"),
                    clean.get("utm_term"),
                    clean.get("gclid"),
                    clean.get("fbclid"),
                    clean.get("ttclid"),
                    clean.get("plan_id"),
                    clean.get("currency"),
                    clean.get("value"),
                    clean.get("job_id"),
                    clean.get("asset_id"),
                    clean.get("media_type"),
                    clean.get("platform_context"),
                    clean.get("checkout_session_id"),
                    clean.get("customer_email_hash"),
                    json.dumps(clean.get("first_touch") or {}, ensure_ascii=False),
                    json.dumps(clean.get("last_touch") or {}, ensure_ascii=False),
                    json.dumps(clean.get("payload") or {}, ensure_ascii=False),
                    json.dumps(clean, ensure_ascii=False),
                    clean.get("ip"),
                    clean.get("user_agent"),
                ),
            )
            self._upsert_attribution(connection, clean)
            return cursor.rowcount > 0

    def _upsert_attribution(self, connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        attribution_key = _tracking_attribution_key(event)
        if not attribution_key:
            return
        existing = connection.execute(
            "SELECT first_touch FROM vision_attribution WHERE attribution_key = ?",
            (attribution_key,),
        ).fetchone()
        current_touch = _touch_from_event(event)
        first_touch = event.get("first_touch") or current_touch
        if existing:
            try:
                first_touch = json.loads(existing["first_touch"] or "{}") or first_touch
            except json.JSONDecodeError:
                pass
        last_touch = event.get("last_touch") or current_touch
        now = _now_iso()
        connection.execute(
            """
            INSERT INTO vision_attribution (
                attribution_key, session_id, anonymous_id, user_id, first_touch, last_touch,
                first_seen_at, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(attribution_key) DO UPDATE SET
                session_id = COALESCE(excluded.session_id, vision_attribution.session_id),
                anonymous_id = COALESCE(excluded.anonymous_id, vision_attribution.anonymous_id),
                user_id = COALESCE(excluded.user_id, vision_attribution.user_id),
                last_touch = excluded.last_touch,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (
                attribution_key,
                event.get("session_id"),
                event.get("anonymous_id"),
                event.get("user_id"),
                json.dumps(first_touch or {}, ensure_ascii=False),
                json.dumps(last_touch or {}, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )

    def get_attribution(self, *, session_id: str | None, anonymous_id: str | None) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT first_touch, last_touch
                FROM vision_attribution
                WHERE (? IS NOT NULL AND anonymous_id = ?)
                   OR (? IS NOT NULL AND session_id = ?)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (anonymous_id, anonymous_id, session_id, session_id),
            ).fetchone()
        if not row:
            return None
        try:
            first_touch = json.loads(row["first_touch"] or "{}")
        except json.JSONDecodeError:
            first_touch = {}
        try:
            last_touch = json.loads(row["last_touch"] or "{}")
        except json.JSONDecodeError:
            last_touch = {}
        return {"first_touch": first_touch, "last_touch": last_touch}


class PostgresTrackingEventStore(TrackingEventStore):
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise TrackingStoreUnavailable("psycopg is required for Postgres tracking storage.") from exc
        self.database_url = database_url
        self.psycopg = psycopg
        self.Jsonb = Jsonb
        self.lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        return self.psycopg.connect(self.database_url, autocommit=True)

    def _execute_optional(self, cursor: Any, statement: str, label: str) -> None:
        try:
            cursor.execute(statement)
        except Exception as exc:
            print(f"[vision] optional tracking schema step failed ({label}): {exc}")

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vision_tracking_events (
                        id BIGSERIAL PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        event_name TEXT NOT NULL,
                        event_time TIMESTAMPTZ,
                        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        session_id TEXT,
                        anonymous_id TEXT,
                        user_id TEXT,
                        page_path TEXT,
                        page_url TEXT,
                        referrer TEXT,
                        utm_source TEXT,
                        utm_medium TEXT,
                        utm_campaign TEXT,
                        utm_content TEXT,
                        utm_term TEXT,
                        gclid TEXT,
                        fbclid TEXT,
                        ttclid TEXT,
                        plan_id TEXT,
                        currency TEXT,
                        value NUMERIC(12, 2),
                        job_id TEXT,
                        asset_id TEXT,
                        media_type TEXT,
                        platform_context TEXT,
                        checkout_session_id TEXT,
                        customer_email_hash TEXT,
                        first_touch JSONB NOT NULL DEFAULT '{}'::jsonb,
                        last_touch JSONB NOT NULL DEFAULT '{}'::jsonb,
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        event_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        ip TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                for statement in (
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS event_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS event_name TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS event_time TIMESTAMPTZ",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS session_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS anonymous_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS user_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS page_path TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS page_url TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS referrer TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS utm_source TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS utm_medium TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS utm_campaign TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS utm_content TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS utm_term TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS gclid TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS fbclid TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS ttclid TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS plan_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS currency TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS value NUMERIC(12, 2)",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS job_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS asset_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS media_type TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS platform_context TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS checkout_session_id TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS customer_email_hash TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS first_touch JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS last_touch JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS event_json JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS ip TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS user_agent TEXT",
                    "ALTER TABLE vision_tracking_events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ):
                    self._execute_optional(cursor, statement, "events column migration")
                for statement in (
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_tracking_events_event_id_unique ON vision_tracking_events(event_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_time ON vision_tracking_events(event_time)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_name_time ON vision_tracking_events(event_name, event_time)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_session ON vision_tracking_events(session_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_anonymous ON vision_tracking_events(anonymous_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_checkout ON vision_tracking_events(checkout_session_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_tracking_events_user ON vision_tracking_events(user_id)",
                ):
                    self._execute_optional(cursor, statement, "events index migration")
                for statement in (
                    """
                    CREATE TABLE IF NOT EXISTS vision_attribution (
                        attribution_key TEXT PRIMARY KEY,
                        session_id TEXT,
                        anonymous_id TEXT,
                        user_id TEXT,
                        first_touch JSONB NOT NULL DEFAULT '{}'::jsonb,
                        last_touch JSONB NOT NULL DEFAULT '{}'::jsonb,
                        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """,
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS attribution_key TEXT",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS session_id TEXT",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS anonymous_id TEXT",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS user_id TEXT",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS first_touch JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS last_touch JSONB NOT NULL DEFAULT '{}'::jsonb",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE vision_attribution ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_attribution_key_unique ON vision_attribution(attribution_key)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_attribution_session ON vision_attribution(session_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_attribution_anonymous ON vision_attribution(anonymous_id)",
                ):
                    self._execute_optional(cursor, statement, "attribution schema migration")

    def append(self, event: dict[str, Any]) -> bool:
        clean = _scrub_tracking_event(event)
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM vision_tracking_events WHERE event_id = %s LIMIT 1",
                    (clean.get("event_id"),),
                )
                if cursor.fetchone() is not None:
                    stored = False
                else:
                    cursor.execute(
                        """
                        INSERT INTO vision_tracking_events (
                            event_id, event_name, event_time, received_at, session_id, anonymous_id, user_id,
                            page_path, page_url, referrer, utm_source, utm_medium, utm_campaign, utm_content,
                            utm_term, gclid, fbclid, ttclid, plan_id, currency, value, job_id, asset_id,
                            media_type, platform_context, checkout_session_id, customer_email_hash,
                            first_touch, last_touch, payload, event_json, ip, user_agent
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            clean.get("event_id"),
                            clean.get("event_name"),
                            clean.get("event_time"),
                            clean.get("received_at"),
                            clean.get("session_id"),
                            clean.get("anonymous_id"),
                            clean.get("user_id"),
                            clean.get("page_path"),
                            clean.get("page_url"),
                            clean.get("referrer"),
                            clean.get("utm_source"),
                            clean.get("utm_medium"),
                            clean.get("utm_campaign"),
                            clean.get("utm_content"),
                            clean.get("utm_term"),
                            clean.get("gclid"),
                            clean.get("fbclid"),
                            clean.get("ttclid"),
                            clean.get("plan_id"),
                            clean.get("currency"),
                            clean.get("value"),
                            clean.get("job_id"),
                            clean.get("asset_id"),
                            clean.get("media_type"),
                            clean.get("platform_context"),
                            clean.get("checkout_session_id"),
                            clean.get("customer_email_hash"),
                            self.Jsonb(clean.get("first_touch") or {}),
                            self.Jsonb(clean.get("last_touch") or {}),
                            self.Jsonb(clean.get("payload") or {}),
                            self.Jsonb(clean),
                            clean.get("ip"),
                            clean.get("user_agent"),
                        ),
                    )
                    stored = True
                try:
                    self._upsert_attribution(cursor, clean)
                except Exception as exc:
                    print(f"[vision] attribution sidecar update failed: {exc}")
                return stored

    def _upsert_attribution(self, cursor: Any, event: dict[str, Any]) -> None:
        attribution_key = _tracking_attribution_key(event)
        if not attribution_key:
            return
        cursor.execute(
            "SELECT first_touch FROM vision_attribution WHERE attribution_key = %s",
            (attribution_key,),
        )
        row = cursor.fetchone()
        current_touch = _touch_from_event(event)
        first_touch = event.get("first_touch") or current_touch
        if row and row[0]:
            first_touch = row[0]
        last_touch = event.get("last_touch") or current_touch
        cursor.execute(
            """
            INSERT INTO vision_attribution (
                attribution_key, session_id, anonymous_id, user_id, first_touch, last_touch,
                first_seen_at, last_seen_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ON CONFLICT (attribution_key) DO UPDATE SET
                session_id = COALESCE(EXCLUDED.session_id, vision_attribution.session_id),
                anonymous_id = COALESCE(EXCLUDED.anonymous_id, vision_attribution.anonymous_id),
                user_id = COALESCE(EXCLUDED.user_id, vision_attribution.user_id),
                last_touch = EXCLUDED.last_touch,
                last_seen_at = EXCLUDED.last_seen_at,
                updated_at = EXCLUDED.updated_at
            """,
            (
                attribution_key,
                event.get("session_id"),
                event.get("anonymous_id"),
                event.get("user_id"),
                self.Jsonb(first_touch or {}),
                self.Jsonb(last_touch or {}),
            ),
        )

    def get_attribution(self, *, session_id: str | None, anonymous_id: str | None) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT first_touch, last_touch
                    FROM vision_attribution
                    WHERE (%s IS NOT NULL AND anonymous_id = %s)
                       OR (%s IS NOT NULL AND session_id = %s)
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (anonymous_id, anonymous_id, session_id, session_id),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return {"first_touch": row[0] or {}, "last_touch": row[1] or {}}


def _create_tracking_store() -> TrackingEventStore:
    database_url = _tracking_database_url()
    if not database_url:
        return UnconfiguredTrackingEventStore("TRACKING_DATABASE_URL or DATABASE_URL is required for first-party tracking.")
    try:
        if database_url.startswith("sqlite:///"):
            return SqliteTrackingEventStore(Path(database_url.removeprefix("sqlite:///")).expanduser())
        return PostgresTrackingEventStore(database_url)
    except Exception as exc:
        return UnconfiguredTrackingEventStore(f"Tracking storage is unavailable: {exc}")


def _tracking_storage_label() -> str:
    tracking = globals().get("TRACKING")
    if isinstance(tracking, PostgresTrackingEventStore):
        return "postgres"
    if isinstance(tracking, SqliteTrackingEventStore):
        return "sqlite"
    if isinstance(tracking, UnconfiguredTrackingEventStore):
        return "unavailable"
    database_url = _tracking_database_url()
    if not database_url:
        return "unconfigured"
    if database_url.startswith("sqlite:///"):
        return "sqlite"
    return "postgres"


def _tracking_storage_ready() -> bool:
    return not isinstance(globals().get("TRACKING"), UnconfiguredTrackingEventStore)


def _tracking_storage_error() -> str:
    tracking = globals().get("TRACKING")
    if isinstance(tracking, UnconfiguredTrackingEventStore):
        return _safe_tracking_error(tracking.reason)
    return ""


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
                    job["status"] = "queued"
                    job["message"] = "Resuming generation after a gateway restart."
                    job["error"] = None
                    job["recovery_count"] = int(job.get("recovery_count") or 0) + 1
            self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")

    def create(
        self,
        prompt: str,
        quality: str,
        *,
        mode: str,
        charged_access_id: str | None,
        charged_mode: str | None,
        charged_amount: int | None = None,
        charged_credit_type: str | None = None,
        credit_cost: dict[str, Any] | None = None,
        generation_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            job_id = uuid.uuid4().hex[:12]
            now = _now_iso()
            job = {
                "id": job_id,
                "prompt": prompt,
                "provider": "auto",
                "mode": mode,
                "quality": quality,
                "status": "queued",
                "message": "Queued inside Vision.",
                "created_at": now,
                "updated_at": now,
                "output_url": None,
                "output_path": None,
                "output_type": mode,
                "error": None,
                "charged_access_id": charged_access_id,
                "charged_mode": charged_mode,
                "charged_amount": charged_amount,
                "charged_credit_type": charged_credit_type,
                "credit_cost": credit_cost or {},
                "generation_settings": generation_settings or {},
                "credit_refunded": False,
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
            job["updated_at"] = _now_iso()
            self.save()
            return dict(job)

    def pending_ids(self) -> list[str]:
        with self.lock:
            return [job_id for job_id, job in self.jobs.items() if job.get("status") == "queued"]


class PostgresJobsStore:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise TrackingStoreUnavailable("psycopg is required for persistent generation jobs.") from exc
        self.database_url = database_url
        self.psycopg = psycopg
        self.Jsonb = Jsonb
        self.lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        return self.psycopg.connect(self.database_url)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vision_generation_jobs (
                        job_id TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def create(
        self,
        prompt: str,
        quality: str,
        *,
        mode: str,
        charged_access_id: str | None,
        charged_mode: str | None,
        charged_amount: int | None = None,
        charged_credit_type: str | None = None,
        credit_cost: dict[str, Any] | None = None,
        generation_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            job_id = uuid.uuid4().hex[:12]
            now = _now_iso()
            job = {
                "id": job_id,
                "prompt": prompt,
                "provider": "auto",
                "mode": mode,
                "quality": quality,
                "status": "queued",
                "message": "Queued inside Vision.",
                "created_at": now,
                "updated_at": now,
                "output_url": None,
                "output_path": None,
                "output_type": mode,
                "error": None,
                "charged_access_id": charged_access_id,
                "charged_mode": charged_mode,
                "charged_amount": charged_amount,
                "charged_credit_type": charged_credit_type,
                "credit_cost": credit_cost or {},
                "generation_settings": generation_settings or {},
                "credit_refunded": False,
            }
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO vision_generation_jobs (job_id, payload) VALUES (%s, %s)",
                        (job_id, self.Jsonb(job)),
                    )
            return dict(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM vision_generation_jobs WHERE job_id = %s", (job_id,))
                row = cursor.fetchone()
        if not row:
            return None
        return dict(row[0])

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT payload FROM vision_generation_jobs WHERE job_id = %s FOR UPDATE",
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise KeyError(job_id)
                    job = dict(row[0])
                    job.update(changes)
                    job["updated_at"] = _now_iso()
                    cursor.execute(
                        """
                        UPDATE vision_generation_jobs
                        SET payload = %s, updated_at = NOW()
                        WHERE job_id = %s
                        """,
                        (self.Jsonb(job), job_id),
                    )
            return dict(job)

    def pending_ids(self) -> list[str]:
        active = {"queued", "preparing", "generating", "downloading"}
        recovered: list[str] = []
        with self.lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT job_id, payload
                        FROM vision_generation_jobs
                        WHERE payload->>'status' = ANY(%s)
                        ORDER BY created_at ASC
                        """,
                        (list(active),),
                    )
                    for job_id, payload in cursor.fetchall():
                        job = dict(payload)
                        job["status"] = "queued"
                        job["message"] = "Resuming generation after a gateway restart."
                        job["error"] = None
                        job["recovery_count"] = int(job.get("recovery_count") or 0) + 1
                        job["updated_at"] = _now_iso()
                        cursor.execute(
                            "UPDATE vision_generation_jobs SET payload = %s, updated_at = NOW() WHERE job_id = %s",
                            (self.Jsonb(job), job_id),
                        )
                        recovered.append(str(job_id))
        return recovered


def _create_jobs_store() -> JobsStore | PostgresJobsStore:
    database_url = _tracking_database_url()
    if database_url:
        try:
            return PostgresJobsStore(database_url)
        except Exception as exc:
            print(f"[vision] persistent job storage unavailable; using local fallback: {_safe_tracking_error(str(exc))}")
    return JobsStore(JOBS_FILE)


class AccessStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.entries: dict[str, dict[str, Any]] = {}
        self.applied_sessions: dict[str, str] = {}
        self.notified_sessions: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "entries" in raw:
            self.entries = raw.get("entries", {})
            self.applied_sessions = raw.get("applied_sessions", {})
            self.notified_sessions = set(raw.get("notified_sessions", []))
            return
        if isinstance(raw, dict):
            self.entries = raw
            self.applied_sessions = {}

    def save(self) -> None:
        payload = {
            "entries": self.entries,
            "applied_sessions": self.applied_sessions,
            "notified_sessions": sorted(self.notified_sessions),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, access_id: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            return dict(entry) if entry else None

    def summary(self, access_id: str) -> dict[str, Any]:
        entry = self.get(access_id)
        return _access_summary(entry)

    def find_by_email(self, email: str | None) -> dict[str, Any] | None:
        normalized = _normalize_email(email)
        if not normalized:
            return None
        with self.lock:
            for entry in self.entries.values():
                if _normalize_email(entry.get("email")) == normalized:
                    return dict(entry)
        return None

    def find_by_user_id(self, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        with self.lock:
            for entry in self.entries.values():
                if str(entry.get("user_id") or "") == str(user_id):
                    return dict(entry)
        return None

    def attach_user(self, access_id: str, *, user_id: str, email: str | None) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            if not entry:
                return None
            entry["user_id"] = user_id
            if email:
                entry["email"] = _normalize_email(email)
            entry["updated_at"] = _now_iso()
            self.save()
            return dict(entry)

    def apply_paid_session(
        self,
        *,
        session_id: str,
        email: str | None,
        current_access_id: str | None,
        current_user_id: str | None,
        vision_credits: int | None = None,
        video_credits: int | None = None,
        image_credits: int | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            existing_access_id = self.applied_sessions.get(session_id)
            if existing_access_id and existing_access_id in self.entries:
                return dict(self.entries[existing_access_id])

            if current_access_id and current_access_id in self.entries and not self.entries[current_access_id].get("admin"):
                entry = self.entries[current_access_id]
            elif current_user_id:
                entry = next(
                    (candidate for candidate in self.entries.values() if str(candidate.get("user_id") or "") == str(current_user_id)),
                    None,
                )
                if entry is None and email:
                    entry = next(
                        (candidate for candidate in self.entries.values() if _normalize_email(candidate.get("email")) == _normalize_email(email)),
                        None,
                    )
            else:
                entry = next(
                    (candidate for candidate in self.entries.values() if _normalize_email(candidate.get("email")) == _normalize_email(email)),
                    None,
                ) if email else None

            if entry is None:
                access_id = uuid.uuid4().hex[:16]
                entry = {
                    "id": access_id,
                    "admin": False,
                    "email": _normalize_email(email) if email else None,
                    "user_id": current_user_id,
                    "vision_credits_remaining": 0,
                    "vision_credits_purchased": 0,
                    "video_remaining": 0,
                    "image_remaining": 0,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "stripe_sessions": [],
                }
                self.entries[access_id] = entry

            resolved_vision_credits = max(int(vision_credits if vision_credits is not None else _pack_vision_credits()), 0)
            resolved_video_credits = max(int(video_credits if video_credits is not None else _pack_video_credits()), 0)
            resolved_image_credits = max(int(image_credits if image_credits is not None else _pack_image_credits()), 0)
            if resolved_vision_credits > 0:
                resolved_video_credits = 0
                resolved_image_credits = 0
            entry["vision_credits_remaining"] = int(entry.get("vision_credits_remaining", 0)) + resolved_vision_credits
            entry["vision_credits_purchased"] = int(entry.get("vision_credits_purchased", 0)) + resolved_vision_credits
            entry["video_remaining"] = int(entry.get("video_remaining", 0)) + resolved_video_credits
            entry["image_remaining"] = int(entry.get("image_remaining", 0)) + resolved_image_credits
            entry["updated_at"] = _now_iso()
            if email:
                entry["email"] = _normalize_email(email)
            if current_user_id:
                entry["user_id"] = current_user_id
            sessions = entry.setdefault("stripe_sessions", [])
            if session_id not in sessions:
                sessions.append(session_id)
            self.applied_sessions[session_id] = entry["id"]
            self.save()
            return dict(entry)

    def consume(self, access_id: str, mode: str, *, amount: int = 1) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            if not entry:
                return None
            requested_amount = max(int(amount or 1), 1)
            remaining_vision_credits = int(entry.get("vision_credits_remaining", 0) or 0)
            purchased_vision_credits = int(entry.get("vision_credits_purchased", 0) or 0)
            if remaining_vision_credits > 0 or purchased_vision_credits > 0:
                if remaining_vision_credits < requested_amount:
                    return None
                entry["vision_credits_remaining"] = remaining_vision_credits - requested_amount
                entry["updated_at"] = _now_iso()
                self.save()
                return {
                    "entry": dict(entry),
                    "charge": {
                        "type": "vision_credits",
                        "amount": requested_amount,
                    },
                }
            key = "image_remaining" if mode == "image" else "video_remaining"
            remaining = int(entry.get(key, 0))
            if remaining <= 0:
                return None
            entry[key] = remaining - 1
            entry["updated_at"] = _now_iso()
            self.save()
            return {
                "entry": dict(entry),
                "charge": {
                    "type": key,
                    "amount": 1,
                },
            }

    def refund(self, access_id: str, mode: str, *, amount: int = 1, credit_type: str | None = None) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            if not entry:
                return None
            if credit_type == "vision_credits":
                entry["vision_credits_remaining"] = int(entry.get("vision_credits_remaining", 0)) + max(int(amount or 1), 1)
            else:
                key = "image_remaining" if mode == "image" else "video_remaining"
                entry[key] = int(entry.get(key, 0)) + max(int(amount or 1), 1)
            entry["updated_at"] = _now_iso()
            self.save()
            return dict(entry)

    def claim_notification(self, session_id: str) -> bool:
        with self.lock:
            if session_id in self.notified_sessions:
                return False
            self.notified_sessions.add(session_id)
            self.save()
            return True


class PostgresAccessStore:
    def __init__(self, database_url: str, *, seed_path: Path | None = None) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise TrackingStoreUnavailable("psycopg is required for Postgres access storage.") from exc
        self.database_url = database_url
        self.psycopg = psycopg
        self.dict_row = dict_row
        self.lock = threading.Lock()
        self._ensure_schema()
        self._import_json_seed(seed_path)

    def _connect(self):
        return self.psycopg.connect(self.database_url, row_factory=self.dict_row)

    def _execute_optional(self, cursor: Any, statement: str, label: str) -> None:
        try:
            cursor.execute(statement)
        except Exception as exc:
            print(f"[vision] optional access schema step failed ({label}): {_safe_access_error(str(exc))}")

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vision_access_accounts (
                        access_id TEXT PRIMARY KEY,
                        admin BOOLEAN NOT NULL DEFAULT FALSE,
                        email TEXT,
                        user_id TEXT,
                        vision_credits_remaining BIGINT NOT NULL DEFAULT 0,
                        vision_credits_purchased BIGINT NOT NULL DEFAULT 0,
                        video_remaining INTEGER NOT NULL DEFAULT 0,
                        image_remaining INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                for statement in (
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS access_id TEXT",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS admin BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS email TEXT",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS user_id TEXT",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS vision_credits_remaining BIGINT NOT NULL DEFAULT 0",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS vision_credits_purchased BIGINT NOT NULL DEFAULT 0",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS video_remaining INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS image_remaining INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE vision_access_accounts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_access_accounts_id_unique ON vision_access_accounts(access_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_access_accounts_email ON vision_access_accounts(email)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_access_accounts_user ON vision_access_accounts(user_id)",
                ):
                    self._execute_optional(cursor, statement, "accounts migration")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vision_access_sessions (
                        session_id TEXT PRIMARY KEY,
                        access_id TEXT NOT NULL REFERENCES vision_access_accounts(access_id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                for statement in (
                    "ALTER TABLE vision_access_sessions ADD COLUMN IF NOT EXISTS session_id TEXT",
                    "ALTER TABLE vision_access_sessions ADD COLUMN IF NOT EXISTS access_id TEXT",
                    "ALTER TABLE vision_access_sessions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_access_sessions_session_unique ON vision_access_sessions(session_id)",
                    "CREATE INDEX IF NOT EXISTS idx_vision_access_sessions_access ON vision_access_sessions(access_id)",
                ):
                    self._execute_optional(cursor, statement, "sessions migration")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vision_access_notifications (
                        session_id TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                for statement in (
                    "ALTER TABLE vision_access_notifications ADD COLUMN IF NOT EXISTS session_id TEXT",
                    "ALTER TABLE vision_access_notifications ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_access_notifications_session_unique ON vision_access_notifications(session_id)",
                ):
                    self._execute_optional(cursor, statement, "notifications migration")

    def _row_to_entry(self, row: dict[str, Any] | None, sessions: list[str] | None = None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": str(row.get("access_id") or ""),
            "admin": bool(row.get("admin")),
            "email": row.get("email"),
            "user_id": row.get("user_id"),
            "vision_credits_remaining": int(row.get("vision_credits_remaining") or 0),
            "vision_credits_purchased": int(row.get("vision_credits_purchased") or 0),
            "video_remaining": int(row.get("video_remaining") or 0),
            "image_remaining": int(row.get("image_remaining") or 0),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "stripe_sessions": list(sessions or []),
        }

    def _sessions_for_access(self, cursor: Any, access_id: str) -> list[str]:
        cursor.execute(
            "SELECT session_id FROM vision_access_sessions WHERE access_id = %s ORDER BY created_at ASC",
            (access_id,),
        )
        return [str(row.get("session_id") or "") for row in cursor.fetchall() if row.get("session_id")]

    def _get_with_cursor(self, cursor: Any, access_id: str) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT * FROM vision_access_accounts WHERE access_id = %s LIMIT 1",
            (access_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_entry(row, self._sessions_for_access(cursor, access_id))

    def _import_json_seed(self, seed_path: Path | None) -> None:
        if not seed_path or not seed_path.exists():
            return
        try:
            raw = json.loads(seed_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[vision] access json seed skipped: {exc}")
            return
        entries = raw.get("entries") if isinstance(raw, dict) and isinstance(raw.get("entries"), dict) else raw
        if not isinstance(entries, dict):
            return
        applied_sessions = raw.get("applied_sessions", {}) if isinstance(raw, dict) else {}
        notified_sessions = raw.get("notified_sessions", []) if isinstance(raw, dict) else []
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                for access_id, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    clean_id = str(entry.get("id") or access_id or "").strip()
                    if not clean_id or clean_id == "admin":
                        continue
                    cursor.execute(
                        """
                        INSERT INTO vision_access_accounts (
                            access_id, admin, email, user_id, vision_credits_remaining,
                            vision_credits_purchased, video_remaining, image_remaining, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()), COALESCE(%s::timestamptz, NOW()))
                        ON CONFLICT (access_id) DO NOTHING
                        """,
                        (
                            clean_id,
                            bool(entry.get("admin")),
                            _normalize_email(entry.get("email")),
                            str(entry.get("user_id") or "") or None,
                            int(entry.get("vision_credits_remaining") or 0),
                            int(entry.get("vision_credits_purchased") or 0),
                            int(entry.get("video_remaining") or 0),
                            int(entry.get("image_remaining") or 0),
                            entry.get("created_at") or None,
                            entry.get("updated_at") or None,
                        ),
                    )
                    for session_id in entry.get("stripe_sessions") or []:
                        clean_session_id = str(session_id or "").strip()
                        if clean_session_id:
                            cursor.execute(
                                """
                                INSERT INTO vision_access_sessions (session_id, access_id)
                                VALUES (%s, %s)
                                ON CONFLICT (session_id) DO NOTHING
                                """,
                                (clean_session_id, clean_id),
                            )
                for session_id, access_id in applied_sessions.items():
                    clean_session_id = str(session_id or "").strip()
                    clean_access_id = str(access_id or "").strip()
                    if clean_session_id and clean_access_id:
                        cursor.execute(
                            """
                            INSERT INTO vision_access_sessions (session_id, access_id)
                            VALUES (%s, %s)
                            ON CONFLICT (session_id) DO NOTHING
                            """,
                            (clean_session_id, clean_access_id),
                        )
                for session_id in notified_sessions:
                    clean_session_id = str(session_id or "").strip()
                    if clean_session_id:
                        cursor.execute(
                            "INSERT INTO vision_access_notifications (session_id) VALUES (%s) ON CONFLICT DO NOTHING",
                            (clean_session_id,),
                        )

    def get(self, access_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                return self._get_with_cursor(cursor, str(access_id))

    def summary(self, access_id: str) -> dict[str, Any]:
        entry = self.get(access_id)
        return _access_summary(entry)

    def find_by_email(self, email: str | None) -> dict[str, Any] | None:
        normalized = _normalize_email(email)
        if not normalized:
            return None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM vision_access_accounts
                    WHERE email = %s AND admin = FALSE
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (normalized,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_entry(row, self._sessions_for_access(cursor, str(row["access_id"])))

    def find_by_user_id(self, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM vision_access_accounts
                    WHERE user_id = %s AND admin = FALSE
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (str(user_id),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_entry(row, self._sessions_for_access(cursor, str(row["access_id"])))

    def attach_user(self, access_id: str, *, user_id: str, email: str | None) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE vision_access_accounts
                    SET user_id = %s,
                        email = COALESCE(%s, email),
                        updated_at = NOW()
                    WHERE access_id = %s
                    RETURNING *
                    """,
                    (str(user_id), _normalize_email(email) or None, str(access_id)),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_entry(row, self._sessions_for_access(cursor, str(row["access_id"])))

    def _select_paid_account(
        self,
        cursor: Any,
        *,
        email: str | None,
        current_access_id: str | None,
        current_user_id: str | None,
    ) -> dict[str, Any] | None:
        if current_access_id:
            cursor.execute(
                "SELECT * FROM vision_access_accounts WHERE access_id = %s AND admin = FALSE FOR UPDATE",
                (str(current_access_id),),
            )
            row = cursor.fetchone()
            if row:
                return row
        if current_user_id:
            cursor.execute(
                """
                SELECT * FROM vision_access_accounts
                WHERE user_id = %s AND admin = FALSE
                ORDER BY updated_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (str(current_user_id),),
            )
            row = cursor.fetchone()
            if row:
                return row
        normalized = _normalize_email(email)
        if normalized:
            cursor.execute(
                """
                SELECT * FROM vision_access_accounts
                WHERE email = %s AND admin = FALSE
                ORDER BY updated_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (normalized,),
            )
            row = cursor.fetchone()
            if row:
                return row
        return None

    def apply_paid_session(
        self,
        *,
        session_id: str,
        email: str | None,
        current_access_id: str | None,
        current_user_id: str | None,
        vision_credits: int | None = None,
        video_credits: int | None = None,
        image_credits: int | None = None,
    ) -> dict[str, Any]:
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            raise ValueError("Stripe session id is required.")
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT access_id FROM vision_access_sessions WHERE session_id = %s LIMIT 1",
                    (clean_session_id,),
                )
                existing_session = cursor.fetchone()
                if existing_session:
                    existing = self._get_with_cursor(cursor, str(existing_session["access_id"]))
                    if existing:
                        return existing

                account = self._select_paid_account(
                    cursor,
                    email=email,
                    current_access_id=current_access_id,
                    current_user_id=current_user_id,
                )
                if account:
                    access_id = str(account["access_id"])
                else:
                    access_id = uuid.uuid4().hex[:16]
                    cursor.execute(
                        """
                        INSERT INTO vision_access_accounts (
                            access_id, admin, email, user_id, vision_credits_remaining,
                            vision_credits_purchased, video_remaining, image_remaining, created_at, updated_at
                        ) VALUES (%s, FALSE, %s, %s, 0, 0, 0, 0, NOW(), NOW())
                        """,
                        (access_id, _normalize_email(email) or None, str(current_user_id or "") or None),
                    )

                cursor.execute(
                    """
                    INSERT INTO vision_access_sessions (session_id, access_id)
                    VALUES (%s, %s)
                    ON CONFLICT (session_id) DO NOTHING
                    RETURNING access_id
                    """,
                    (clean_session_id, access_id),
                )
                inserted_session = cursor.fetchone()
                if not inserted_session:
                    cursor.execute(
                        "SELECT access_id FROM vision_access_sessions WHERE session_id = %s LIMIT 1",
                        (clean_session_id,),
                    )
                    existing_session = cursor.fetchone()
                    existing = self._get_with_cursor(cursor, str(existing_session["access_id"])) if existing_session else None
                    if existing:
                        return existing

                resolved_vision_credits = max(int(vision_credits if vision_credits is not None else _pack_vision_credits()), 0)
                resolved_video_credits = max(int(video_credits if video_credits is not None else _pack_video_credits()), 0)
                resolved_image_credits = max(int(image_credits if image_credits is not None else _pack_image_credits()), 0)
                if resolved_vision_credits > 0:
                    resolved_video_credits = 0
                    resolved_image_credits = 0
                cursor.execute(
                    """
                    UPDATE vision_access_accounts
                    SET email = COALESCE(%s, email),
                        user_id = COALESCE(%s, user_id),
                        vision_credits_remaining = vision_credits_remaining + %s,
                        vision_credits_purchased = vision_credits_purchased + %s,
                        video_remaining = video_remaining + %s,
                        image_remaining = image_remaining + %s,
                        updated_at = NOW()
                    WHERE access_id = %s
                    RETURNING *
                    """,
                    (
                        _normalize_email(email) or None,
                        str(current_user_id or "") or None,
                        resolved_vision_credits,
                        resolved_vision_credits,
                        resolved_video_credits,
                        resolved_image_credits,
                        access_id,
                    ),
                )
                row = cursor.fetchone()
                return self._row_to_entry(row, self._sessions_for_access(cursor, access_id)) or {
                    "id": access_id,
                    "admin": False,
                    "email": _normalize_email(email) or None,
                    "user_id": current_user_id,
                    "vision_credits_remaining": 0,
                    "vision_credits_purchased": 0,
                    "video_remaining": 0,
                    "image_remaining": 0,
                    "stripe_sessions": [clean_session_id],
                }

    def consume(self, access_id: str, mode: str, *, amount: int = 1) -> dict[str, Any] | None:
        requested_amount = max(int(amount or 1), 1)
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE vision_access_accounts
                    SET vision_credits_remaining = vision_credits_remaining - %s,
                        updated_at = NOW()
                    WHERE access_id = %s
                      AND (vision_credits_remaining > 0 OR vision_credits_purchased > 0)
                      AND vision_credits_remaining >= %s
                    RETURNING *
                    """,
                    (requested_amount, str(access_id), requested_amount),
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "entry": self._row_to_entry(row, self._sessions_for_access(cursor, str(access_id))),
                        "charge": {"type": "vision_credits", "amount": requested_amount},
                    }
                key = "image_remaining" if mode == "image" else "video_remaining"
                cursor.execute(
                    f"""
                    UPDATE vision_access_accounts
                    SET {key} = {key} - 1,
                        updated_at = NOW()
                    WHERE access_id = %s
                      AND vision_credits_remaining = 0
                      AND vision_credits_purchased = 0
                      AND {key} > 0
                    RETURNING *
                    """,
                    (str(access_id),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "entry": self._row_to_entry(row, self._sessions_for_access(cursor, str(access_id))),
                    "charge": {"type": key, "amount": 1},
                }

    def refund(self, access_id: str, mode: str, *, amount: int = 1, credit_type: str | None = None) -> dict[str, Any] | None:
        refunded_amount = max(int(amount or 1), 1)
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                if credit_type == "vision_credits":
                    cursor.execute(
                        """
                        UPDATE vision_access_accounts
                        SET vision_credits_remaining = vision_credits_remaining + %s,
                            updated_at = NOW()
                        WHERE access_id = %s
                        RETURNING *
                        """,
                        (refunded_amount, str(access_id)),
                    )
                else:
                    key = "image_remaining" if mode == "image" else "video_remaining"
                    cursor.execute(
                        f"""
                        UPDATE vision_access_accounts
                        SET {key} = {key} + %s,
                            updated_at = NOW()
                        WHERE access_id = %s
                        RETURNING *
                        """,
                        (refunded_amount, str(access_id)),
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_entry(row, self._sessions_for_access(cursor, str(access_id)))

    def claim_notification(self, session_id: str) -> bool:
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            return False
        with self.lock, self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO vision_access_notifications (session_id)
                    VALUES (%s)
                    ON CONFLICT (session_id) DO NOTHING
                    RETURNING session_id
                    """,
                    (clean_session_id,),
                )
                return cursor.fetchone() is not None


def _access_database_url() -> str:
    for env_name in ("ACCESS_DATABASE_URL", "TRACKING_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(env_name, "").strip()
        if _is_usable_database_url(value) and not value.startswith("sqlite:///"):
            return value
    return ""


def _safe_access_error(value: str) -> str:
    text = str(value or "")
    for env_name in ("ACCESS_DATABASE_URL", "TRACKING_DATABASE_URL", "DATABASE_URL"):
        database_url = os.environ.get(env_name, "").strip()
        if database_url:
            text = text.replace(database_url, "[database-url]")
    text = re.sub(r"(postgres(?:ql)?://)[^@\s]+@", r"\1[credentials]@", text)
    return text[:500]


def _create_access_store() -> AccessStore | PostgresAccessStore:
    mode = os.environ.get("VISION_ACCESS_STORAGE", os.environ.get("ACCESS_STORAGE", "auto")).strip().lower()
    database_url = _access_database_url()
    if mode in {"json", "local", "local_json", "file"}:
        return AccessStore(ACCESS_FILE)
    if database_url:
        try:
            return PostgresAccessStore(database_url, seed_path=ACCESS_FILE)
        except Exception as exc:
            print(f"[vision] Postgres access storage unavailable, falling back to runtime json: {_safe_access_error(str(exc))}")
    elif mode in {"postgres", "postgresql", "db"}:
        print("[vision] Postgres access storage requested but no ACCESS_DATABASE_URL/TRACKING_DATABASE_URL/DATABASE_URL is configured.")
    return AccessStore(ACCESS_FILE)


def _access_storage_label() -> str:
    access = globals().get("ACCESS")
    if isinstance(access, PostgresAccessStore):
        return "postgres"
    if isinstance(access, AccessStore):
        return "runtime_json"
    return "unknown"


class AuthCodeRateLimited(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"Wait {self.retry_after} seconds before requesting another code.")


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.users: dict[str, dict[str, Any]] = {}
        self.pending_codes: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        self.users = raw.get("users", {}) if isinstance(raw.get("users"), dict) else {}
        self.pending_codes = raw.get("pending_codes", {}) if isinstance(raw.get("pending_codes"), dict) else {}

    def save(self) -> None:
        payload = {
            "users": self.users,
            "pending_codes": self.pending_codes,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        with self.lock:
            user = self.users.get(str(user_id))
            return dict(user) if user else None

    def find_by_email(self, email: str | None) -> dict[str, Any] | None:
        normalized = _normalize_email(email)
        if not normalized:
            return None
        with self.lock:
            for user in self.users.values():
                if _normalize_email(user.get("email")) == normalized:
                    return dict(user)
        return None

    def create_or_get(self, email: str) -> dict[str, Any]:
        normalized = _normalize_email(email)
        if not normalized:
            raise ValueError("A valid email is required.")
        with self.lock:
            existing = next(
                (user for user in self.users.values() if _normalize_email(user.get("email")) == normalized),
                None,
            )
            if existing:
                existing["last_login_at"] = _now_iso()
                self.save()
                return dict(existing)
            user_id = uuid.uuid4().hex[:16]
            user = {
                "id": user_id,
                "email": normalized,
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
            }
            self.users[user_id] = user
            self.save()
            return dict(user)

    def issue_code(self, email: str) -> str:
        normalized = _normalize_email(email)
        if not normalized:
            raise ValueError("A valid email is required.")
        with self.lock:
            now = datetime.now(timezone.utc).timestamp()
            existing = self.pending_codes.get(normalized) or {}
            try:
                last_issued_at = float(existing.get("issued_at_epoch") or 0)
            except (TypeError, ValueError):
                last_issued_at = 0
            retry_after = _auth_code_resend_seconds() - int(now - last_issued_at)
            if last_issued_at > 0 and retry_after > 0:
                raise AuthCodeRateLimited(retry_after)
            code = f"{secrets.randbelow(1_000_000):06d}"
            expires_at = now + (_auth_code_ttl_minutes() * 60)
            self.pending_codes[normalized] = {
                "email": normalized,
                "code_hash": _hash_auth_code(normalized, code),
                "expires_at": expires_at,
                "issued_at": _now_iso(),
                "issued_at_epoch": now,
                "attempts": 0,
            }
            self.save()
        return code

    def verify_code(self, email: str, code: str) -> dict[str, Any] | None:
        normalized = _normalize_email(email)
        submitted = (code or "").strip()
        if not normalized or not submitted:
            return None
        with self.lock:
            record = self.pending_codes.get(normalized)
            if not record:
                return None
            if float(record.get("expires_at") or 0) < datetime.now(timezone.utc).timestamp():
                self.pending_codes.pop(normalized, None)
                self.save()
                return None
            if record.get("locked"):
                return None
            expected_hash = str(record.get("code_hash") or "")
            submitted_hash = _hash_auth_code(normalized, submitted)
            if not expected_hash or not hmac.compare_digest(expected_hash, submitted_hash):
                attempts = int(record.get("attempts") or 0) + 1
                if attempts >= _auth_code_max_attempts():
                    record["attempts"] = attempts
                    record["locked"] = True
                    record["code_hash"] = ""
                else:
                    record["attempts"] = attempts
                self.save()
                return None
            self.pending_codes.pop(normalized, None)
            existing = next(
                (user for user in self.users.values() if _normalize_email(user.get("email")) == normalized),
                None,
            )
            if existing:
                existing["last_login_at"] = _now_iso()
                self.save()
                return dict(existing)
            user_id = uuid.uuid4().hex[:16]
            user = {
                "id": user_id,
                "email": normalized,
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
            }
            self.users[user_id] = user
            self.save()
            return dict(user)


JOBS = _create_jobs_store()
ACCESS = _create_access_store()
USERS = UserStore(USERS_FILE)
TRACKING = _create_tracking_store()
QUEUE: queue.Queue[str] = queue.Queue()


def _tracking_config() -> dict[str, Any]:
    return {
        "tracking_enabled": _env_enabled("TRACKING_ENABLED", True),
        "tracking_storage": _tracking_storage_label(),
        "tracking_storage_ready": _tracking_storage_ready(),
        "tracking_storage_error": _tracking_storage_error(),
        "meta_pixel_enabled": _env_enabled("META_PIXEL_ENABLED", False),
        "meta_capi_enabled": _env_enabled("META_CAPI_ENABLED", False),
        "meta_pixel_id": os.environ.get("META_PIXEL_ID", "").strip(),
        "tiktok_pixel_enabled": _env_enabled("TIKTOK_PIXEL_ENABLED", False),
        "tiktok_events_api_enabled": _env_enabled("TIKTOK_EVENTS_API_ENABLED", False),
        "tiktok_pixel_id": os.environ.get("TIKTOK_PIXEL_ID", "").strip(),
        "google_tag_enabled": _env_enabled("GOOGLE_TAG_ENABLED", False),
        "google_enhanced_conversions_enabled": _env_enabled("GOOGLE_ENHANCED_CONVERSIONS_ENABLED", False),
        "google_tag_id": os.environ.get("GOOGLE_TAG_ID", "").strip(),
        "google_ads_conversion_label": os.environ.get("GOOGLE_ADS_CONVERSION_LABEL", "").strip(),
    }


def _normalize_tracking_event(payload: TrackEventRequest | dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    if isinstance(payload, TrackEventRequest):
        raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    else:
        raw = dict(payload)
    event_name = str(raw.get("event_name") or "").strip()
    if event_name not in CANONICAL_TRACKING_EVENTS:
        raise HTTPException(status_code=400, detail=f"Unsupported tracking event: {event_name}")

    event: dict[str, Any] = {
        "event_name": event_name,
        "event_id": _safe_tracking_string(raw.get("event_id"), 128) or uuid.uuid4().hex,
        "event_time": _safe_tracking_string(raw.get("event_time"), 64) or _now_iso(),
        "received_at": _now_iso(),
    }
    for field in TRACKING_CORE_FIELDS:
        if field in {"event_name", "event_id", "event_time"}:
            continue
        value = raw.get(field)
        if field == "value":
            try:
                event[field] = float(value) if value is not None and value != "" else None
            except (TypeError, ValueError):
                event[field] = None
        else:
            event[field] = _safe_tracking_string(value, 2048)

    if request is not None:
        event["session_id"] = event.get("session_id") or _safe_tracking_string(request.cookies.get(_tracking_cookie_session_name()), 128) or uuid.uuid4().hex
        event["anonymous_id"] = event.get("anonymous_id") or _safe_tracking_string(request.cookies.get(_tracking_cookie_anonymous_name()), 128) or uuid.uuid4().hex

    user = _user_from_request(request) if request is not None else None
    if user and not event.get("user_id"):
        event["user_id"] = str(user.get("id") or user.get("user_id") or "")

    if request is not None:
        event["ip"] = request.client.host if request.client else None
        event["user_agent"] = request.headers.get("user-agent")

    event["first_touch"] = _safe_tracking_dict(raw.get("first_touch"))
    event["last_touch"] = _safe_tracking_dict(raw.get("last_touch"))
    event["payload"] = _safe_tracking_dict(raw.get("payload"))
    email_hash = event.get("customer_email_hash") or _sha256_normalized(event.get("customer_email"))
    if email_hash:
        event["customer_email_hash"] = email_hash
    return event


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def _meta_event_name(event_name: str) -> str | None:
    return {
        "CheckoutStarted": "InitiateCheckout",
        "PurchaseCompleted": "Purchase",
    }.get(event_name)


def _tiktok_event_name(event_name: str) -> str | None:
    return {
        "LandingViewed": "ViewContent",
        "StudioViewed": "ViewContent",
        "CheckoutStarted": "InitiateCheckout",
        "PurchaseCompleted": "Purchase",
    }.get(event_name)


def _send_meta_capi_event(event: dict[str, Any]) -> None:
    if not _env_enabled("META_CAPI_ENABLED", False):
        return
    pixel_id = os.environ.get("META_PIXEL_ID", "").strip()
    access_token = os.environ.get("META_CAPI_ACCESS_TOKEN", "").strip()
    meta_event = _meta_event_name(str(event.get("event_name") or ""))
    if not pixel_id or not access_token or not meta_event:
        return
    user_data: dict[str, Any] = {
        "client_ip_address": event.get("ip"),
        "client_user_agent": event.get("user_agent"),
    }
    email_hash = _sha256_normalized(event.get("customer_email"))
    if email_hash:
        user_data["em"] = [email_hash]
    if event.get("fbclid"):
        user_data["fbc"] = event.get("fbclid")
    payload = {
        "data": [
            {
                "event_name": meta_event,
                "event_time": int(datetime.now(timezone.utc).timestamp()),
                "event_id": event.get("event_id"),
                "action_source": "website",
                "event_source_url": event.get("page_url"),
                "user_data": {key: value for key, value in user_data.items() if value},
                "custom_data": {
                    "currency": event.get("currency"),
                    "value": event.get("value"),
                    "content_name": event.get("plan_id"),
                    "order_id": event.get("checkout_session_id"),
                },
            }
        ],
    }
    url = f"https://graph.facebook.com/v19.0/{urllib.parse.quote(pixel_id)}/events?access_token={urllib.parse.quote(access_token)}"
    _request_json(url, payload)


def _send_tiktok_events_api_event(event: dict[str, Any]) -> None:
    if not _env_enabled("TIKTOK_EVENTS_API_ENABLED", False):
        return
    pixel_code = os.environ.get("TIKTOK_PIXEL_ID", "").strip()
    access_token = os.environ.get("TIKTOK_EVENTS_API_ACCESS_TOKEN", "").strip()
    tiktok_event = _tiktok_event_name(str(event.get("event_name") or ""))
    if not pixel_code or not access_token or not tiktok_event:
        return
    payload = {
        "event_source": "web",
        "event_source_id": pixel_code,
        "data": [
            {
                "event": tiktok_event,
                "event_time": int(datetime.now(timezone.utc).timestamp()),
                "event_id": event.get("event_id"),
                "context": {
                    "page": {"url": event.get("page_url"), "referrer": event.get("referrer")},
                    "user": {
                        "ip": event.get("ip"),
                        "user_agent": event.get("user_agent"),
                        "ttclid": event.get("ttclid"),
                        "email": event.get("customer_email_hash") or _sha256_normalized(event.get("customer_email")),
                    },
                },
                "properties": {
                    "currency": event.get("currency"),
                    "value": event.get("value"),
                    "content_id": event.get("plan_id"),
                    "order_id": event.get("checkout_session_id"),
                },
            }
        ],
    }
    _request_json("https://business-api.tiktok.com/open_api/v1.3/event/track/", payload, {"Access-Token": access_token})


def _send_ads_events_async(event: dict[str, Any]) -> None:
    def _worker() -> None:
        for sender in (_send_meta_capi_event, _send_tiktok_events_api_event):
            try:
                sender(event)
            except Exception as exc:
                print(f"[vision] ads event delivery failed: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


def _record_tracking_event(event: dict[str, Any], *, dispatch_ads: bool = True) -> bool:
    if not _env_enabled("TRACKING_ENABLED", True):
        return False
    try:
        stored = TRACKING.append(event)
    except Exception as exc:
        print(f"[vision] first-party tracking storage failed: {exc}")
        if _env_enabled("TRACKING_DEBUG_JSONL_ENABLED", False):
            _append_tracking_debug_event(event)
        return False
    if stored and _env_enabled("TRACKING_DEBUG_JSONL_ENABLED", False):
        _append_tracking_debug_event(event)
    if stored and dispatch_ads:
        _send_ads_events_async(event)
    return stored


def _append_tracking_debug_event(event: dict[str, Any]) -> None:
    try:
        TRACKING_DEBUG_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with TRACKING_DEBUG_EVENTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_scrub_tracking_event(event), ensure_ascii=False) + "\n")
    except OSError:
        return


def _tracking_metadata(payload: dict[str, Any] | None, event_id: str | None = None) -> dict[str, str]:
    raw = payload or {}
    metadata_keys = [
        "event_id",
        "session_id",
        "anonymous_id",
        "page_path",
        "page_url",
        "referrer",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "gclid",
        "fbclid",
        "ttclid",
    ]
    metadata: dict[str, str] = {}
    for key in metadata_keys:
        value = event_id if key == "event_id" and event_id else raw.get(key)
        clean = _safe_tracking_string(value, 480)
        if clean:
            metadata[f"vision_tracking_{key}"] = clean
    return metadata


def _tracking_context_from_request(payload: dict[str, Any] | None, request: Request) -> dict[str, Any]:
    context = dict(payload or {})
    session_id = _safe_tracking_string(context.get("session_id"), 128) or _safe_tracking_string(request.cookies.get(_tracking_cookie_session_name()), 128)
    anonymous_id = _safe_tracking_string(context.get("anonymous_id"), 128) or _safe_tracking_string(request.cookies.get(_tracking_cookie_anonymous_name()), 128)
    if session_id:
        context["session_id"] = session_id
    if anonymous_id:
        context["anonymous_id"] = anonymous_id
    try:
        server_attribution = TRACKING.get_attribution(session_id=session_id, anonymous_id=anonymous_id)
    except Exception:
        server_attribution = None
    if server_attribution:
        first_touch = server_attribution.get("first_touch") or {}
        last_touch = server_attribution.get("last_touch") or {}
        context.setdefault("first_touch", first_touch)
        context.setdefault("last_touch", last_touch)
        for key in TRACKING_ATTRIBUTION_FIELDS:
            if not context.get(key) and last_touch.get(key):
                context[key] = last_touch.get(key)
    return context


def _set_tracking_cookies(response: Response, request: Request, event: dict[str, Any]) -> None:
    settings = _cookie_settings(request)
    session_id = _safe_tracking_string(event.get("session_id"), 128)
    anonymous_id = _safe_tracking_string(event.get("anonymous_id"), 128)
    if session_id:
        response.set_cookie(key=_tracking_cookie_session_name(), value=session_id, **settings)
    if anonymous_id:
        response.set_cookie(key=_tracking_cookie_anonymous_name(), value=anonymous_id, **settings)


def _stripe_signature_is_valid(payload: bytes, signature_header: str, secret: str) -> bool:
    parts: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key, []).append(value)
    timestamp = parts.get("t", [""])[0]
    signatures = parts.get("v1", [])
    if not timestamp or not signatures:
        return False
    try:
        signed_at = int(timestamp)
        tolerance = max(30, min(900, int(os.environ.get("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))))
    except (TypeError, ValueError):
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - signed_at) > tolerance:
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


def _purchase_tracking_event(*, session: dict[str, Any], entry: dict[str, Any], platform_context: str) -> dict[str, Any]:
    metadata = session.get("metadata") or {}
    session_pack = _pack_summary(metadata.get("vision_pack_id"))
    customer_details = session.get("customer_details") or {}
    checkout_session_id = str(session.get("id") or "")
    customer_email = customer_details.get("email") or session.get("customer_email") or entry.get("email")
    amount_total = session.get("amount_total")
    try:
        value = float(amount_total) / 100 if amount_total is not None else float(session_pack.get("price_cents") or 0) / 100
    except (TypeError, ValueError):
        value = None
    payload = {
        "event_name": "PurchaseCompleted",
        "event_id": f"stripe:{checkout_session_id}:PurchaseCompleted",
        "event_time": _now_iso(),
        "session_id": metadata.get("vision_tracking_session_id"),
        "anonymous_id": metadata.get("vision_tracking_anonymous_id"),
        "user_id": entry.get("user_id"),
        "page_path": metadata.get("vision_tracking_page_path"),
        "page_url": metadata.get("vision_tracking_page_url"),
        "referrer": metadata.get("vision_tracking_referrer"),
        "utm_source": metadata.get("vision_tracking_utm_source"),
        "utm_medium": metadata.get("vision_tracking_utm_medium"),
        "utm_campaign": metadata.get("vision_tracking_utm_campaign"),
        "utm_content": metadata.get("vision_tracking_utm_content"),
        "utm_term": metadata.get("vision_tracking_utm_term"),
        "gclid": metadata.get("vision_tracking_gclid"),
        "fbclid": metadata.get("vision_tracking_fbclid"),
        "ttclid": metadata.get("vision_tracking_ttclid"),
        "plan_id": metadata.get("vision_pack_id") or session_pack.get("id"),
        "currency": str(session.get("currency") or session_pack.get("currency") or _pack_currency()).upper(),
        "value": value,
        "checkout_session_id": checkout_session_id,
        "customer_email": customer_email,
        "platform_context": platform_context,
        "payload": {
            "stripe_payment_status": session.get("payment_status"),
            "stripe_status": session.get("status"),
            "checkout_event_id": metadata.get("vision_tracking_event_id"),
        },
    }
    return _normalize_tracking_event(payload)


def _refund_job_credit(job: dict[str, Any] | None) -> None:
    if not job:
        return
    access_id = job.get("charged_access_id")
    charged_mode = job.get("charged_mode")
    if not access_id or not charged_mode or job.get("credit_refunded"):
        return
    refunded = ACCESS.refund(
        str(access_id),
        str(charged_mode),
        amount=int(job.get("charged_amount") or 1),
        credit_type=str(job.get("charged_credit_type") or ""),
    )
    if refunded is not None:
        JOBS.update(job["id"], credit_refunded=True)


def _process_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    output_dir = OUTPUT_ROOT / job_id
    try:
        generation_settings = job.get("generation_settings") if isinstance(job.get("generation_settings"), dict) else {}
        if job.get("mode") == "image":
            requested_aspect_ratio = _normalize_aspect_ratio(generation_settings.get("aspect_ratio"))
            route = _select_image_route("kling")
            JOBS.update(
                job_id,
                provider=route["provider"],
                status="preparing",
                message="Shaping the still image inside Vision.",
            )
            JOBS.update(job_id, status="generating", message="Building the still frame inside Vision.")
            output_image = generate_kling_image(
                prompt=job["prompt"],
                output_dir=output_dir,
                quality="studio",
                aspect_ratio=requested_aspect_ratio,
            )
            output_image = _fit_image_to_aspect_ratio(output_image, requested_aspect_ratio)
            JOBS.update(
                job_id,
                status="ready",
                message="Ready.",
                output_path=str(output_image),
                output_url=_public_output_url(job_id, output_image.name),
                output_type="image",
                error=None,
            )
            return

        routes = _candidate_generation_routes(str(job.get("prompt") or ""), str(job.get("quality") or "auto"), job_id, generation_settings)
        attempt_log: list[dict[str, Any]] = []
        output_video = None
        last_error: Exception | None = None
        for index, route in enumerate(routes, start=1):
            JOBS.update(
                job_id,
                provider=route["provider"],
                quality=route["quality"],
                route_attempts=attempt_log,
                status="preparing",
                message="Shaping the cinematic direction inside Vision.",
            )
            try:
                JOBS.update(job_id, status="generating", message="Building your cinematic render inside Vision.")
                if route["provider"] == "byteplus_seedance":
                    output_video = generate_seedance_video(
                        prompt=job["prompt"],
                        output_dir=output_dir,
                        model=route["model"],
                        duration=int(route.get("duration", 5)),
                        aspect_ratio=str(route.get("aspect_ratio") or "16:9"),
                        resolution=route["resolution"],
                    )
                elif route["provider"] == "google_veo":
                    output_video = generate_google_veo_video(
                        prompt=job["prompt"],
                        output_dir=output_dir,
                        model=route["model"],
                        duration=int(route.get("duration", 6)),
                        aspect_ratio=route["aspect_ratio"],
                        resolution=route.get("resolution"),
                        fallback_models=route.get("fallback_models", ""),
                    )
                elif route["provider"] == "kling_api":
                    output_video = generate_kling_api_video(
                        prompt=job["prompt"],
                        output_dir=output_dir,
                        model=route.get("model"),
                        duration=int(route.get("duration", 5)),
                        aspect_ratio=str(route.get("aspect_ratio") or "16:9"),
                        resolution=str(route.get("resolution") or "720p"),
                        sound_enabled=bool(route.get("sound_enabled")),
                        quality=str(route.get("quality") or "studio"),
                    )
                else:
                    lane_state = kling_session_bridge_status()
                    if not lane_state.get("ready"):
                        prepare_kling_session_bridge()
                    output_video = generate_kling_session_bridge(
                        prompt=job["prompt"],
                        output_dir=output_dir,
                    )
                attempt_log.append(
                    {
                        "attempt": index,
                        "provider": route["provider"],
                        "quality": route["quality"],
                        "model": route.get("model"),
                        "status": "success",
                    }
                )
                JOBS.update(job_id, route_attempts=attempt_log)
                break
            except Exception as exc:
                last_error = exc
                attempt_log.append(
                    {
                        "attempt": index,
                        "provider": route["provider"],
                        "quality": route["quality"],
                        "model": route.get("model"),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                JOBS.update(job_id, route_attempts=attempt_log)
                continue

        if output_video is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Vision could not open a premium render lane for this prompt.")
        JOBS.update(job_id, status="downloading", message="Finishing and importing your result into Vision.")
        JOBS.update(
            job_id,
            status="ready",
            message="Ready.",
            output_path=str(output_video),
            output_url=_public_output_url(job_id, output_video.name),
            output_type="video",
            route_attempts=attempt_log,
            error=None,
        )
    except SessionBridgeNotReadyError as exc:
        _refund_job_credit(job)
        JOBS.update(
            job_id,
            status="failed",
            message="Vision could not open a render lane right now.",
            error=str(exc) if str(exc) else None,
        )
    except RuntimeError as exc:
        _refund_job_credit(job)
        JOBS.update(
            job_id,
            status="failed",
            message="Vision could not complete the render before import.",
            error=str(exc) if str(exc) else None,
        )
    except Exception as exc:
        recovered = None
        if output_dir.exists():
            patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp"] if job.get("mode") == "image" else ["*.mp4"]
            for pattern in patterns:
                recovered = next(output_dir.glob(pattern), None)
                if recovered:
                    break
        if recovered and recovered.exists():
            JOBS.update(
                job_id,
                status="ready",
                message="Ready.",
                output_path=str(recovered),
                output_url=_public_output_url(job_id, recovered.name),
                output_type="image" if job.get("mode") == "image" else "video",
                error=None,
            )
            return
        _refund_job_credit(job)
        JOBS.update(
            job_id,
            status="failed",
            message="Vision could not complete the render before import.",
            error=str(exc),
        )


def _worker_loop() -> None:
    while True:
        job_id = QUEUE.get()
        keepalive_stop = threading.Event()
        keepalive_worker = threading.Thread(
            target=_job_keepalive_loop,
            args=(keepalive_stop,),
            daemon=True,
        )
        keepalive_worker.start()
        try:
            try:
                _process_job(job_id)
            except Exception as exc:
                job = JOBS.get(job_id)
                _refund_job_credit(job)
                try:
                    JOBS.update(
                        job_id,
                        status="failed",
                        message="Vision could not complete the render before import.",
                        error=str(exc),
                    )
                except Exception:
                    pass
        finally:
            keepalive_stop.set()
            QUEUE.task_done()


def _job_keepalive_url() -> str:
    configured = os.environ.get("VISION_GATEWAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return f"{configured}/api/health"
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if hostname:
        return f"https://{hostname}/api/health"
    return ""


def _job_keepalive_loop(stop: threading.Event) -> None:
    url = _job_keepalive_url()
    if not url:
        return
    while not stop.wait(240):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                response.read(64)
        except Exception:
            continue


for recovered_job_id in JOBS.pending_ids():
    QUEUE.put(recovered_job_id)


WORKER = threading.Thread(target=_worker_loop, daemon=True)
WORKER.start()


@APP.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@APP.get("/api/engine/status")
def engine_status() -> dict[str, Any]:
    return {
        "kling_api": kling_api_status(),
        "kling_session_bridge": kling_session_bridge_status(),
        "kling_image_bridge": kling_image_status(),
        "openai_image": openai_image_status(),
        "seedance": seedance_status(),
        "google": _google_status(),
        "default_provider": _default_generation_provider(),
        "default_image_provider": _default_image_provider(),
        "default_quality": _default_generation_quality(),
        "access_storage": _access_storage_label(),
    }


@APP.post("/api/engine/prepare")
def engine_prepare() -> JSONResponse:
    try:
        state = prepare_kling_session_bridge()
        return JSONResponse({"ok": state.get("ready", False), "message": state.get("message", "Kling session bridge inspected.")})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)})


@APP.get("/api/access/me")
def access_me(request: Request) -> dict[str, Any]:
    user = _user_from_request(request)
    access = _access_from_request(request)
    if user and not _access_summary(access)["has_access"]:
        try:
            restored = _restore_access_for_email(
                email=str(user.get("email") or ""),
                current_access_id=access.get("id") if access and not access.get("admin") else None,
                current_user_id=str(user.get("id") or ""),
            )
        except Exception:
            restored = None
        if restored:
            access = restored
    access_summary = _access_summary(access)
    billing_context: dict[str, Any] = {"customer_id": None, "subscription": None, "lookup_failed": False}
    if user:
        try:
            billing_context = _cached_stripe_billing_context_for_user(user=user, access_entry=access)
        except Exception:
            billing_context = {"customer_id": None, "subscription": None, "lookup_failed": True}
    access_summary = _account_entitlement_summary(access, user, billing_context=billing_context)
    return {
        "user": _user_summary(user),
        "user_token": _user_token_for_user(user),
        "access": access_summary,
        "subscription": billing_context.get("subscription"),
        "pack": _pack_summary_for_access(access_summary),
        "packs": _packs_summary_for_access(access_summary),
    }


@APP.post("/api/auth/request-code")
def request_auth_code(payload: RequestAuthCodeRequest) -> dict[str, Any]:
    normalized = _normalize_email(payload.email)
    if "@" not in normalized:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    try:
        code = USERS.issue_code(normalized)
        _send_auth_code_email(email=normalized, code=code)
    except AuthCodeRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Vision could not send the access code right now: {exc}") from exc
    return {
        "ok": True,
        "email": normalized,
        "message": "A Vision access code is on its way.",
        "packs": _packs_summary_for_access(None),
    }


@APP.post("/api/auth/verify-code")
def verify_auth_code(payload: VerifyAuthCodeRequest, request: Request) -> JSONResponse:
    normalized = _normalize_email(payload.email)
    user = USERS.verify_code(normalized, payload.code)
    if not user:
        raise HTTPException(status_code=401, detail="That Vision access code is invalid or expired.")

    current_access = _access_from_request(request)
    attached_entry = None
    if current_access and not current_access.get("admin"):
        current_email = _normalize_email(current_access.get("email"))
        if not current_email or current_email == normalized:
            attached_entry = ACCESS.attach_user(
                str(current_access["id"]),
                user_id=str(user["id"]),
                email=normalized,
            )
    if attached_entry is None:
        email_entry = ACCESS.find_by_email(normalized)
        if email_entry:
            attached_entry = ACCESS.attach_user(
                str(email_entry["id"]),
                user_id=str(user["id"]),
                email=normalized,
            )
    if attached_entry is None:
        attached_entry = ACCESS.find_by_user_id(str(user["id"]))
    if attached_entry is None or not _access_summary(attached_entry)["has_access"]:
        try:
            attached_entry = _restore_access_for_email(
                email=normalized,
                current_access_id=current_access.get("id") if current_access and not current_access.get("admin") else None,
                current_user_id=str(user["id"]),
            )
        except Exception:
            attached_entry = None

    billing_context: dict[str, Any] = {"customer_id": None, "subscription": None, "lookup_failed": False}
    if attached_entry:
        try:
            billing_context = _cached_stripe_billing_context_for_user(user=user, access_entry=attached_entry)
        except Exception:
            billing_context = {"customer_id": None, "subscription": None, "lookup_failed": True}
    access_summary = _account_entitlement_summary(attached_entry, user, billing_context=billing_context)
    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(user),
            "access": access_summary,
            "subscription": billing_context.get("subscription"),
            "pack": _pack_summary_for_access(access_summary),
            "packs": _packs_summary_for_access(access_summary),
            "user_token": _user_token_for_user(user),
            "access_token": _access_token_for_entry(attached_entry) if attached_entry else None,
        }
    )
    _set_user_cookie(response, request, {"user_id": user["id"], "email": user.get("email")})
    if attached_entry:
        _set_access_cookie(response, request, _access_token_payload(attached_entry))
    return response


@APP.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    access_summary = _access_summary(None)
    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(None),
            "user_token": None,
            "access": access_summary,
            "pack": _pack_summary_for_access(access_summary),
            "packs": _packs_summary_for_access(access_summary),
        }
    )
    _clear_user_cookie(response, request)
    _clear_access_cookie(response, request)
    return response


@APP.post("/api/prompt/improve")
def improve_prompt(payload: ImprovePromptRequest, request: Request) -> dict[str, Any]:
    user, access, summary = _request_entitlement(request)
    if not user and not (access and access.get("admin")):
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before improving a prompt.")
    if not summary["has_access"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_required",
                "message": "Start Vision Studio to improve this prompt.",
                "access": summary,
                "pack": _pack_summary_for_access(summary),
                "packs": _packs_summary_for_access(summary),
            },
        )
    mode = _normalize_mode(payload.mode)
    result = improve_vision_prompt(prompt=payload.prompt.strip(), mode=mode)
    return {
        "ok": True,
        "mode": mode,
        **result,
    }


@APP.get("/api/tracking/config")
def tracking_config() -> dict[str, Any]:
    return _tracking_config()


@APP.post("/api/track")
def track_event(payload: TrackEventRequest, request: Request) -> JSONResponse:
    event = _normalize_tracking_event(payload, request)
    stored = _record_tracking_event(event)
    response = JSONResponse(
        {
            "ok": True,
            "stored": stored,
            "event_id": event["event_id"],
        }
    )
    _set_tracking_cookies(response, request, event)
    return response


@APP.post("/api/admin/unlock")
def admin_unlock(payload: AdminUnlockRequest, request: Request) -> JSONResponse:
    configured = os.environ.get("VISION_ADMIN_TOKEN", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="Admin unlock is not configured for this deployment.")
    if payload.token.strip() != configured:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    entry = _admin_access_entry()
    access_summary = _access_summary(entry)
    response = JSONResponse(
        {
            "ok": True,
            "access": access_summary,
            "pack": _pack_summary_for_access(access_summary),
            "packs": _packs_summary_for_access(access_summary),
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    return response


@APP.post("/api/checkout/session")
def create_checkout_session(payload: CreateCheckoutSessionRequest, request: Request) -> dict[str, Any]:
    user = _user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before starting checkout.")
    resolved_email = _normalize_email(user.get("email"))
    if "@" not in resolved_email:
        raise HTTPException(status_code=409, detail="Your Vision account does not have a valid checkout email.")
    selected_pack = _pack_by_exact_id(payload.pack_id or "studio")
    if not selected_pack:
        raise HTTPException(status_code=400, detail="Select a valid Vision plan.")
    try:
        billing_context = _stripe_billing_context_for_user(
            user=user,
            access_entry=ACCESS.find_by_user_id(str(user.get("id") or "")) or ACCESS.find_by_email(resolved_email),
        )
    except Exception:
        billing_context = {"customer_id": None, "subscription": None, "lookup_failed": True}
    existing_subscription = billing_context.get("subscription") if isinstance(billing_context.get("subscription"), dict) else None
    existing_customer_id = str(billing_context.get("customer_id") or "").strip()
    if existing_subscription and existing_subscription.get("active") and existing_customer_id:
        try:
            portal = _create_stripe_customer_portal_session(
                customer_id=existing_customer_id,
                return_url=_frontend_return_url(request, payload.return_path, default="/studio/"),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        portal_url = str(portal.get("url") or "").strip()
        if not portal_url:
            raise HTTPException(status_code=502, detail="Stripe did not return a customer portal URL.")
        return {
            "session_id": None,
            "url": portal_url,
            "destination": "billing_portal",
            "subscription": existing_subscription,
            "pack": _public_pack(selected_pack),
            "packs": _packs_summary_for_access(None),
        }
    if billing_context.get("lookup_failed") and not existing_subscription:
        raise HTTPException(
            status_code=503,
            detail="Vision could not verify your existing Stripe subscriptions. Please try again before starting a new checkout.",
        )
    try:
        session = _create_stripe_checkout_session(
            request=request,
            email=resolved_email,
            user_id=str(user.get("id") or ""),
            pack_id=str(selected_pack.get("id") or "studio"),
            return_path=payload.return_path,
            customer_id=str(billing_context.get("customer_id") or "") or None,
            tracking=_tracking_context_from_request(payload.tracking, request),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    checkout_url = session.get("url")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Stripe did not return a hosted checkout URL.")
    return {
        "session_id": session.get("id"),
        "url": checkout_url,
        "pack": _public_pack(selected_pack),
        "packs": _packs_summary_for_access(None),
    }


@APP.post("/api/billing/portal")
def create_customer_portal_session(
    request: Request,
    payload: CreateCustomerPortalSessionRequest | None = None,
) -> dict[str, Any]:
    user = _user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before managing billing.")
    access = ACCESS.find_by_user_id(str(user.get("id") or "")) or ACCESS.find_by_email(user.get("email"))
    try:
        billing_context = _stripe_billing_context_for_user(user=user, access_entry=access)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    customer_id = str(billing_context.get("customer_id") or "").strip()
    if not customer_id:
        if billing_context.get("lookup_failed"):
            raise HTTPException(status_code=503, detail="Vision could not verify your Stripe billing account right now.")
        raise HTTPException(status_code=404, detail="No Stripe billing account is linked to this Vision account yet.")
    try:
        portal = _create_stripe_customer_portal_session(
            customer_id=customer_id,
            return_url=_frontend_return_url(request, payload.return_path if payload else None, default="/studio/"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    portal_url = str(portal.get("url") or "").strip()
    if not portal_url:
        raise HTTPException(status_code=502, detail="Stripe did not return a customer portal URL.")
    return {
        "url": portal_url,
        "subscription": billing_context.get("subscription"),
    }


@APP.post("/api/checkout/confirm")
def confirm_checkout(payload: ConfirmCheckoutRequest, request: Request) -> JSONResponse:
    current_user = _user_from_request(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before confirming checkout.")
    normalized_session_id = payload.session_id.strip()
    email = _normalize_email(current_user.get("email"))
    current_access = ACCESS.find_by_user_id(str(current_user.get("id") or "")) or ACCESS.find_by_email(email)
    anchored_session_ids = {
        str(session_id or "").strip()
        for session_id in list((current_access or {}).get("stripe_sessions") or [])
    }
    allow_legacy = normalized_session_id in anchored_session_ids
    try:
        session = _retrieve_stripe_checkout_session(normalized_session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if _stripe_object_id(session) != normalized_session_id:
        raise HTTPException(status_code=409, detail="Stripe returned an unexpected checkout session.")
    session_pack = _validate_checkout_session_for_user(session, current_user, allow_legacy=allow_legacy)
    _invalidate_stripe_billing_cache(email)
    current_access_id = current_access.get("id") if current_access and not current_access.get("admin") else None
    vision_credits, video_credits, image_credits = _credits_for_validated_pack(session_pack)
    entry = ACCESS.apply_paid_session(
        session_id=normalized_session_id,
        email=email,
        current_access_id=current_access_id,
        current_user_id=str(current_user.get("id")),
        vision_credits=vision_credits,
        video_credits=video_credits,
        image_credits=image_credits,
    )
    if ACCESS.claim_notification(normalized_session_id):
        _notify_purchase_async(session=session, entry=entry)
    access_summary = _access_summary(entry)
    try:
        subscription_value = session.get("subscription")
        subscription_id = _stripe_object_id(subscription_value)
        subscription = subscription_value if isinstance(subscription_value, dict) else _retrieve_stripe_subscription(subscription_id)
        validated_subscription_pack = _validated_vision_subscription(
            subscription,
            expected_pack=session_pack,
            expected_customer_id=_stripe_object_id(session.get("customer")) or None,
            expected_email=email,
            allow_legacy=allow_legacy,
        )
        subscription_summary = (
            _subscription_summary(
                subscription,
                customer_id=_stripe_object_id(session.get("customer")) or None,
            )
            if validated_subscription_pack
            else None
        )
    except Exception:
        subscription_summary = None
    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(current_user),
            "user_token": _user_token_for_user(current_user),
            "access": access_summary,
            "subscription": subscription_summary,
            "pack": _pack_summary_for_access(access_summary, str(session_pack.get("id") or "")),
            "packs": _packs_summary_for_access(access_summary),
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    _set_user_cookie(
        response,
        request,
        {"user_id": current_user["id"], "email": current_user.get("email")},
    )
    return response


@APP.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook is not configured for this deployment.")

    raw_body = await request.body()
    signature = request.headers.get("stripe-signature", "")
    if not _stripe_signature_is_valid(raw_body, signature, secret):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook payload.") from exc

    event_type = str(event.get("type") or "")
    subscription_invoice_events = {"invoice.paid", "invoice.payment_succeeded"}
    if event_type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded", *subscription_invoice_events}:
        return {"ok": True, "handled": False}

    event_object = ((event.get("data") or {}).get("object") or {})
    if not isinstance(event_object, dict):
        return {"ok": True, "handled": False}

    if event_type in subscription_invoice_events:
        invoice = event_object
        invoice_id = str(invoice.get("id") or "").strip()
        if not invoice_id:
            return {"ok": True, "handled": False}
        # The first subscription invoice is covered by checkout.session.completed.
        if str(invoice.get("billing_reason") or "") == "subscription_create":
            return {"ok": True, "handled": False, "invoice_id": invoice_id, "reason": "initial_subscription_invoice"}
        if str(invoice.get("status") or "") not in {"paid"}:
            return {"ok": True, "handled": False, "invoice_id": invoice_id}
        validated_invoice = _validated_vision_invoice(invoice)
        if not validated_invoice:
            return {
                "ok": True,
                "handled": False,
                "invoice_id": invoice_id,
                "reason": "not_a_vision_studio_invoice",
            }
        email = str(validated_invoice["email"])
        invoice_pack = validated_invoice["pack"]
        _invalidate_stripe_billing_cache(email)
        known_user = USERS.find_by_email(email)
        vision_credits, video_credits, image_credits = _credits_for_validated_pack(invoice_pack)
        entry = ACCESS.apply_paid_session(
            session_id=f"invoice:{invoice_id}",
            email=email,
            current_access_id=None,
            current_user_id=str(known_user.get("id")) if known_user else None,
            vision_credits=vision_credits,
            video_credits=video_credits,
            image_credits=image_credits,
        )
        if ACCESS.claim_notification(f"invoice:{invoice_id}"):
            invoice_session = {
                "id": f"invoice:{invoice_id}",
                "metadata": validated_invoice["metadata"],
                "amount_total": invoice.get("amount_paid") or invoice.get("total"),
                "currency": invoice.get("currency"),
                "customer_email": email,
                "payment_status": "paid",
            }
            _notify_purchase_async(session=invoice_session, entry=entry)
        return {"ok": True, "handled": True, "invoice_id": invoice_id}

    session = event_object

    session_id = str(session.get("id") or "").strip()
    if not session_id:
        return {"ok": True, "handled": False}

    validated_session = _validated_vision_checkout_session(session, allow_legacy=False)
    if not validated_session:
        return {
            "ok": True,
            "handled": False,
            "session_id": session_id,
            "reason": "not_a_vision_studio_checkout",
        }
    session_pack, _ = validated_session
    email = _checkout_session_email(session)
    _invalidate_stripe_billing_cache(email)
    known_user = USERS.find_by_email(email)
    metadata_user_id = str(_stripe_metadata(session).get("vision_user_id") or "").strip()
    vision_credits, video_credits, image_credits = _credits_for_validated_pack(session_pack)
    entry = ACCESS.apply_paid_session(
        session_id=session_id,
        email=email,
        current_access_id=None,
        current_user_id=metadata_user_id or (str(known_user.get("id")) if known_user else None),
        vision_credits=vision_credits,
        video_credits=video_credits,
        image_credits=image_credits,
    )
    if ACCESS.claim_notification(session_id):
        _notify_purchase_async(session=session, entry=entry)
    tracking_event = _purchase_tracking_event(session=session, entry=entry, platform_context="stripe_webhook")
    stored = _record_tracking_event(tracking_event)
    return {
        "ok": True,
        "handled": True,
        "stored": stored,
        "event_id": tracking_event["event_id"],
        "session_id": session_id,
    }


@APP.post("/api/jobs")
def create_job(payload: CreateJobRequest, request: Request) -> dict[str, Any]:
    mode = _normalize_mode(payload.mode)
    duration_seconds = _normalize_duration_seconds(payload.duration_seconds)
    requested_resolution = _normalize_resolution(payload.resolution)
    resolution = "2k" if mode == "image" else requested_resolution
    aspect_ratio = _normalize_aspect_ratio(payload.aspect_ratio)
    sound_enabled = bool(payload.sound_enabled) if mode == "video" else False
    provider = "kling" if mode == "image" else _normalize_generation_provider(payload.provider)
    if mode == "video" and provider == "openai":
        provider = "auto"
    generation_settings = {
        "duration_seconds": duration_seconds if mode == "video" else None,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "sound_enabled": sound_enabled,
        "provider": provider if provider != "auto" else None,
    }
    credit_cost = _vision_credit_cost(
        mode,
        duration_seconds=duration_seconds,
        resolution=resolution,
        sound_enabled=sound_enabled,
    )
    normalized_quality = "studio" if mode == "image" else _normalize_quality(payload.quality)
    if mode != "image" and normalized_quality == "auto":
        normalized_quality = _quality_from_generation_settings(mode, resolution, sound_enabled)
    requested_quality = _effective_job_quality(mode, normalized_quality)
    user, access, summary = _request_entitlement(request)
    if not user and not (access and access.get("admin")):
        raise HTTPException(status_code=401, detail="Authenticate your Vision account before creating an image.")
    if not summary["has_access"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_required",
                "message": "Start Vision Studio to turn this idea into a cinematic result.",
                "access": summary,
                "pack": _pack_summary_for_access(summary),
                "packs": _packs_summary_for_access(summary),
            },
        )

    charged_access_id: str | None = None
    charged_mode: str | None = None
    charged_amount: int | None = None
    charged_credit_type: str | None = None
    if not summary["admin"]:
        access_id = summary["access_id"]
        consumed = ACCESS.consume(str(access_id), mode, amount=int(credit_cost["amount"])) if access_id else None
        if consumed is None:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "insufficient_credits",
                    "message": "Manage your Vision Studio plan to keep creating.",
                    "access": summary,
                    "pack": _pack_summary_for_access(summary),
                    "packs": _packs_summary_for_access(summary),
                    "credit_cost": credit_cost,
                },
            )
        charged_access_id = str(access_id)
        charged_mode = mode
        charge = consumed.get("charge") if isinstance(consumed, dict) else None
        charged_amount = int(charge.get("amount") if isinstance(charge, dict) else credit_cost["amount"])
        charged_credit_type = str(charge.get("type") if isinstance(charge, dict) else "vision_credits")

    # Authorization and credit consumption must complete before this can call
    # any prompt-enhancement provider.
    prompt_bundle = _auto_enhance_job_prompt(payload.prompt.strip(), mode)
    job = JOBS.create(
        str(prompt_bundle["prompt"]),
        requested_quality,
        mode=mode,
        charged_access_id=charged_access_id,
        charged_mode=charged_mode,
        charged_amount=charged_amount,
        charged_credit_type=charged_credit_type,
        credit_cost=credit_cost,
        generation_settings=generation_settings,
    )
    job = JOBS.update(
        job["id"],
        source_prompt=prompt_bundle.get("source_prompt"),
        prompt_summary=prompt_bundle.get("prompt_summary"),
        prompt_provider=prompt_bundle.get("prompt_provider"),
        prompt_model=prompt_bundle.get("prompt_model"),
        prompt_enhanced=bool(prompt_bundle.get("prompt_enhanced")),
        prompt_enhancement_error=prompt_bundle.get("prompt_enhancement_error"),
    )
    QUEUE.put(job["id"])
    return job


@APP.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@APP.get("/api/assets/status")
def get_asset_status(path: str) -> JSONResponse:
    resolved = _resolve_generated_asset_file(path)
    if not resolved:
        raise HTTPException(status_code=400, detail="Asset path must point to /generated/...")

    asset_path, asset_file = resolved
    available = asset_file.is_file()
    payload: dict[str, Any] = {
        "path": asset_path,
        "available": available,
        "missing": not available,
    }
    if available:
        stat_result = asset_file.stat()
        payload["size_bytes"] = stat_result.st_size
        payload["filename"] = asset_file.name
        payload["content_type"] = mimetypes.guess_type(asset_file.name)[0] or "application/octet-stream"
    return JSONResponse(payload)


if (VISION_ROOT / "assets").exists():
    APP.mount("/assets", StaticFiles(directory=str(VISION_ROOT / "assets")), name="assets")


if (VISION_ROOT / "index.html").exists():
    @APP.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(VISION_ROOT / "index.html")


    @APP.get("/studio", include_in_schema=False)
    def frontend_studio() -> FileResponse:
        return FileResponse(VISION_ROOT / "index.html")


    @APP.get("/studio/", include_in_schema=False)
    def frontend_studio_trailing() -> FileResponse:
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
    default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    host = os.environ.get("VISION_GATEWAY_HOST", default_host)
    port = int(os.environ.get("PORT", os.environ.get("VISION_GATEWAY_PORT", "8787")))
    uvicorn.run(APP, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
