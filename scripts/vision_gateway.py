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
from pydantic import BaseModel, Field

from run_google_nano_banana2 import generate_image as generate_google_image
from run_google_nano_banana2 import status as google_image_status
from run_google_prompt_enhancer import improve_prompt as improve_vision_prompt
from run_google_prompt_enhancer import status as google_prompt_status
from run_google_veo31 import generate_video as generate_google_veo_video
from run_google_veo31 import status as google_video_status
from run_seedance_modelark import generate_video as generate_seedance_video
from run_seedance_modelark import status as seedance_status
from vision_kling_session_bridge import SessionBridgeNotReadyError
from vision_kling_session_bridge import generate as generate_kling_session_bridge
from vision_kling_session_bridge import generate_image as generate_kling_image
from vision_kling_session_bridge import prepare as prepare_kling_session_bridge
from vision_kling_session_bridge import status_image as kling_image_status
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
    requested = os.environ.get("VISION_GATEWAY_DEFAULT_GENERATION_PROVIDER", "auto").strip().lower()
    return requested if requested in {"auto", "seedance", "google", "kling"} else "auto"


def _normalize_quality(value: str | None) -> str:
    if not value:
        return "auto"
    normalized = value.strip().lower()
    return normalized if normalized in {"auto", "fast", "studio", "director"} else _default_generation_quality()


def _normalize_mode(value: str | None) -> str:
    if not value:
        return "video"
    normalized = value.strip().lower()
    return normalized if normalized in {"video", "image"} else "video"


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


def _select_image_route() -> dict[str, str]:
    kling_state = kling_image_status()
    if kling_state.get("ready"):
        return {
            "provider": "kling_image",
            "model": os.environ.get("VISION_KLING_IMAGE_MODEL", "kling-image-web"),
            "fallback_models": "",
        }
    google_state = _google_status()
    image_state = google_state["image"]
    if image_state.get("ready"):
        return {
            "provider": "google_image",
            "model": str(image_state.get("model") or os.environ.get("GOOGLE_IMAGE_MODEL", "imagen-4.0-generate-001")),
            "fallback_models": str(image_state.get("fallback_models") or os.environ.get("GOOGLE_IMAGE_FALLBACK_MODELS", "imagen-4.0-fast-generate-001")),
        }
    raise RuntimeError("Google image generation is not ready yet for this Vision deployment.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_pack_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "starter",
            "name": "Vision Starter",
            "subtitle": "Short videos + images",
            "description": "Good for testing Vision with premium image and video creation.",
            "price_cents": 1490,
            "currency": "eur",
            "vision_credits": 500000,
            "credit_label": "500.000 Vision credits",
            "video_credits": 5,
            "image_credits": 50,
            "video_label": "Up to 5 videos",
            "duration_label": "Videos up to 15 seconds",
            "image_label": "Up to 50 images",
            "example_label": "Examples: up to 5 short videos or 50 images.",
            "badge": "",
            "cta_label": "Start creating",
            "features": [
                "Improve Prompt included",
                "No watermark exports",
                "480p, 720p, 1080p, or 4K-ready output",
                "Private downloads",
            ],
        },
        {
            "id": "creator",
            "name": "Vision Creator",
            "subtitle": "Most popular for creators",
            "description": "Best for creators, social clips, and prompt testing.",
            "price_cents": 3990,
            "currency": "eur",
            "vision_credits": 2000000,
            "credit_label": "2.000.000 Vision credits",
            "video_credits": 20,
            "image_credits": 200,
            "video_label": "Up to 20 videos",
            "duration_label": "Videos up to 15 seconds",
            "image_label": "Up to 200 images",
            "example_label": "Examples: up to 10 standard 10s videos or 200 images.",
            "badge": "Most popular",
            "cta_label": "Choose Creator",
            "features": [
                "Sound on/off control",
                "Full HD video generation",
                "Premium cinematic prompt refinement",
                "No watermark exports",
            ],
        },
        {
            "id": "pro",
            "name": "Vision Pro",
            "subtitle": "Premium generation for campaigns",
            "description": "For heavier creation, longer clips, premium outputs, and campaign work.",
            "price_cents": 8990,
            "currency": "eur",
            "vision_credits": 5000000,
            "credit_label": "5.000.000 Vision credits",
            "video_credits": 50,
            "image_credits": 500,
            "video_label": "Up to 50 videos",
            "duration_label": "Videos up to 15 seconds",
            "image_label": "Up to 500 images",
            "example_label": "Examples: up to 25 standard 10s videos, premium clips, or 500 images.",
            "badge": "Premium generation",
            "cta_label": "Go Pro",
            "features": [
                "480p, 720p, 1080p, and 4K-ready outputs",
                "Sound on/off control",
                "Advanced cinematic refinement",
                "Campaign-ready no watermark exports",
            ],
        },
    ]


def _format_pack_price_display(price_cents: int, currency: str) -> str:
    amount = price_cents / 100
    if currency.lower() == "eur":
        return f"€{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{amount:.2f} {currency.upper()}"


def _packs_summary() -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for pack in _default_pack_catalog():
        summary = copy.deepcopy(pack)
        summary["price_display"] = _format_pack_price_display(int(summary["price_cents"]), str(summary["currency"]))
        packs.append(summary)
    return packs


def _pack_by_id(pack_id: str | None) -> dict[str, Any]:
    normalized = str(pack_id or "").strip().lower()
    packs = _packs_summary()
    for pack in packs:
        if str(pack.get("id")) == normalized:
            return pack
    return packs[0]


def _pack_price_cents() -> int:
    return int(_pack_summary().get("price_cents") or 1490)


def _pack_currency() -> str:
    return str(_pack_summary().get("currency") or "eur").strip().lower() or "eur"


def _pack_video_credits() -> int:
    return max(int(_pack_summary().get("video_credits") or 5), 1)


def _pack_image_credits() -> int:
    return max(int(_pack_summary().get("image_credits") or 50), 0)


def _pack_name() -> str:
    return str(_pack_summary().get("name") or "Vision Starter").strip() or "Vision Starter"


def _pack_description() -> str:
    return str(_pack_summary().get("description") or f"{_pack_video_credits()} videos + {_pack_image_credits()} images").strip() or f"{_pack_video_credits()} videos + {_pack_image_credits()} images"


def _access_cookie_name() -> str:
    return os.environ.get("VISION_ACCESS_COOKIE_NAME", "vision_access").strip() or "vision_access"


def _access_secret() -> str:
    return os.environ.get("VISION_ACCESS_SECRET", "vision-dev-access-secret").strip()


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


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _hash_auth_code(email: str, code: str) -> str:
    normalized = _normalize_email(email)
    return hashlib.sha256(f"{normalized}:{code}:{_user_secret()}".encode("utf-8")).hexdigest()


def _sign_user_token(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
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
    return payload if isinstance(payload, dict) else None


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


def _send_email(*, recipients: list[str], subject: str, body_lines: list[str], sender: str | None = None) -> None:
    host = os.environ.get("VISION_NOTIFY_SMTP_HOST", "").strip()
    if not recipients or not host:
        return

    port = int(os.environ.get("VISION_NOTIFY_SMTP_PORT", "587"))
    username = os.environ.get("VISION_NOTIFY_SMTP_USERNAME", "").strip()
    password = os.environ.get("VISION_NOTIFY_SMTP_PASSWORD", "").strip()
    use_ssl = os.environ.get("VISION_NOTIFY_SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
    use_starttls = os.environ.get("VISION_NOTIFY_SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes", "on"}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender or _notification_sender()
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
        f"Legacy capacity: {record.get('video_credits')} videos + {record.get('image_credits')} images",
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
        "Use the access code below to enter your Vision Studio and return to your pack whenever you want.",
        "",
        f"Access code: {code}",
        "",
        f"This code expires in {_auth_code_ttl_minutes()} minutes.",
        "It keeps your access secure and helps you recover your videos and images from any device.",
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
    response.set_cookie(
        key=_user_cookie_name(),
        value=_sign_user_token(payload),
        **_cookie_settings(request),
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


def _stripe_request(method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    method_name = method.upper()
    url = f"https://api.stripe.com{path}"
    encoded_data: bytes | None = None
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{_stripe_secret_key()}:".encode("utf-8")).decode("ascii"),
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
    email: str | None,
    pack_id: str | None,
    tracking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frontend_base = _frontend_base_url(request)
    pack = _pack_by_id(pack_id)
    payload: dict[str, Any] = {
        "mode": "payment",
        "success_url": f"{frontend_base}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{frontend_base}/?checkout=cancel",
        "allow_promotion_codes": "true",
        "billing_address_collection": "auto",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": str(pack.get("currency") or "eur"),
        "line_items[0][price_data][unit_amount]": str(pack.get("price_cents") or 1490),
        "line_items[0][price_data][product_data][name]": str(pack.get("name") or "Vision Starter"),
        "line_items[0][price_data][product_data][description]": str(pack.get("description") or ""),
        "metadata[vision_pack_id]": str(pack.get("id") or "starter"),
        "metadata[vision_pack_name]": str(pack.get("name") or "Vision Starter"),
        "metadata[vision_pack_vision_credits]": str(pack.get("vision_credits") or ""),
        "metadata[vision_pack_video_credits]": str(pack.get("video_credits") or 5),
        "metadata[vision_pack_image_credits]": str(pack.get("image_credits") or 50),
    }
    for key, value in _tracking_metadata(tracking).items():
        payload[f"metadata[{key}]"] = value
    if email:
        payload["customer_email"] = email
    return _stripe_request("POST", "/v1/checkout/sessions", payload)


def _retrieve_stripe_checkout_session(session_id: str) -> dict[str, Any]:
    encoded_session_id = urllib.parse.quote(session_id, safe="")
    return _stripe_request("GET", f"/v1/checkout/sessions/{encoded_session_id}")


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


def _credits_from_session(session: dict[str, Any]) -> tuple[int, int]:
    metadata = session.get("metadata") or {}
    session_pack = _pack_by_id(metadata.get("vision_pack_id"))
    try:
        video_credits = int(metadata.get("vision_pack_video_credits") or session_pack.get("video_credits") or _pack_video_credits())
    except (TypeError, ValueError):
        video_credits = int(session_pack.get("video_credits") or _pack_video_credits())
    try:
        image_credits = int(metadata.get("vision_pack_image_credits") or session_pack.get("image_credits") or _pack_image_credits())
    except (TypeError, ValueError):
        image_credits = int(session_pack.get("image_credits") or _pack_image_credits())
    return max(video_credits, 0), max(image_credits, 0)


def _restore_access_for_email(*, email: str, current_access_id: str | None, current_user_id: str | None) -> dict[str, Any] | None:
    sessions = _list_stripe_checkout_sessions_by_email(email)
    restored_entry: dict[str, Any] | None = None
    for session in sessions:
        if session.get("status") != "complete" or session.get("payment_status") != "paid":
            continue
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        video_credits, image_credits = _credits_from_session(session)
        if video_credits <= 0 and image_credits <= 0:
            continue
        restored_entry = ACCESS.apply_paid_session(
            session_id=session_id,
            email=email,
            current_access_id=current_access_id,
            current_user_id=current_user_id,
            video_credits=video_credits,
            image_credits=image_credits,
        )
    return restored_entry


def _access_summary(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {
            "has_access": False,
            "admin": False,
            "video_remaining": 0,
            "image_remaining": 0,
            "access_id": None,
        }
    is_admin = bool(entry.get("admin"))
    video_remaining = None if is_admin else int(entry.get("video_remaining", 0))
    image_remaining = None if is_admin else int(entry.get("image_remaining", 0))
    return {
        "has_access": is_admin or (video_remaining or 0) > 0 or (image_remaining or 0) > 0,
        "admin": is_admin,
        "video_remaining": video_remaining,
        "image_remaining": image_remaining,
        "access_id": entry.get("id"),
    }


def _pack_summary(pack_id: str | None = None) -> dict[str, Any]:
    return copy.deepcopy(_pack_by_id(pack_id))


def _access_token_payload(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("admin"):
        return {"admin": True}
    return {"access_id": entry["id"]}


def _access_token_for_entry(entry: dict[str, Any]) -> str:
    return _sign_access_token(_access_token_payload(entry))


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
    return USERS.get(user_id)


def _request_access_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    header_token = request.headers.get("x-vision-access", "").strip()
    if header_token:
        return header_token
    cookie_token = request.cookies.get(_access_cookie_name())
    return cookie_token or None


def _access_from_request(request: Request) -> dict[str, Any] | None:
    token = _request_access_token(request)
    payload = _verify_access_token(token)
    if payload:
        if payload.get("admin"):
            return {
                "id": "admin",
                "admin": True,
                "video_remaining": None,
                "image_remaining": None,
            }
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



def _candidate_generation_routes(prompt: str, quality: str, job_id: str) -> list[dict[str, str]]:
    seedance_state = seedance_status()
    google_state = _google_status()
    kling_state = kling_session_bridge_status()
    default_provider = _default_generation_provider()
    quality_candidates = _quality_candidates_for_prompt(quality)
    allowed_providers = {
        "auto": {"google", "seedance", "kling"},
        "google": {"google"},
        "seedance": {"seedance"},
        "kling": {"kling"},
    }.get(default_provider, {"google", "seedance", "kling"})
    routes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for candidate_quality in quality_candidates:
        for provider_name in _provider_priority_for_prompt(prompt, candidate_quality):
            if provider_name not in allowed_providers:
                continue
            if provider_name == "google" and google_state["video"].get("ready"):
                model_name = _google_video_model_for_quality(candidate_quality)
                if model_name:
                    route = {
                        "provider": "google_veo",
                        "quality": candidate_quality,
                        "model": model_name,
                        "fallback_models": _google_fallback_models_for_quality(candidate_quality),
                        "aspect_ratio": "16:9",
                        "resolution": _google_resolution_for_quality(candidate_quality),
                        "duration": _google_duration_for_quality(candidate_quality),
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
                        "resolution": _seedance_resolution_for_quality(candidate_quality),
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
    mode: str | None = Field(default="video", min_length=5, max_length=16)


class CreateCheckoutSessionRequest(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    pack_id: str | None = Field(default=None, max_length=32)
    tracking: dict[str, Any] | None = None


class ConfirmCheckoutRequest(BaseModel):
    session_id: str = Field(min_length=10, max_length=255)


class AdminUnlockRequest(BaseModel):
    token: str = Field(min_length=8, max_length=255)


class ImprovePromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1500)
    mode: str | None = Field(default="video", min_length=5, max_length=16)


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
                    job["status"] = "failed"
                    job["message"] = "Generation was interrupted before Vision could import the result."
                    job["error"] = "Gateway restarted before completion."

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
                    "video_remaining": 0,
                    "image_remaining": 0,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "stripe_sessions": [],
                }
                self.entries[access_id] = entry

            entry["video_remaining"] = int(entry.get("video_remaining", 0)) + max(int(video_credits if video_credits is not None else _pack_video_credits()), 0)
            entry["image_remaining"] = int(entry.get("image_remaining", 0)) + max(int(image_credits if image_credits is not None else _pack_image_credits()), 0)
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

    def consume(self, access_id: str, mode: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            if not entry:
                return None
            key = "image_remaining" if mode == "image" else "video_remaining"
            remaining = int(entry.get(key, 0))
            if remaining <= 0:
                return None
            entry[key] = remaining - 1
            entry["updated_at"] = _now_iso()
            self.save()
            return dict(entry)

    def refund(self, access_id: str, mode: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(access_id)
            if not entry:
                return None
            key = "image_remaining" if mode == "image" else "video_remaining"
            entry[key] = int(entry.get(key, 0)) + 1
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
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(timezone.utc).timestamp() + (_auth_code_ttl_minutes() * 60)
        with self.lock:
            self.pending_codes[normalized] = {
                "email": normalized,
                "code_hash": _hash_auth_code(normalized, code),
                "expires_at": expires_at,
                "issued_at": _now_iso(),
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
            if record.get("code_hash") != _hash_auth_code(normalized, submitted):
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


JOBS = JobsStore(JOBS_FILE)
ACCESS = AccessStore(ACCESS_FILE)
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
    refunded = ACCESS.refund(str(access_id), str(charged_mode))
    if refunded is not None:
        JOBS.update(job["id"], credit_refunded=True)


def _process_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    output_dir = OUTPUT_ROOT / job_id
    try:
        if job.get("mode") == "image":
            route = _select_image_route()
            JOBS.update(
                job_id,
                provider=route["provider"],
                status="preparing",
                message="Shaping the still image inside Vision.",
            )
            JOBS.update(job_id, status="generating", message="Building the still frame inside Vision.")
            if route["provider"] == "kling_image":
                output_image = generate_kling_image(
                    prompt=job["prompt"],
                    output_dir=output_dir,
                    quality=str(job.get("quality") or "studio"),
                )
            else:
                output_image = generate_google_image(
                    prompt=job["prompt"],
                    output_dir=output_dir,
                    model=route["model"],
                    fallback_models=route.get("fallback_models", ""),
                )
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

        routes = _candidate_generation_routes(str(job.get("prompt") or ""), str(job.get("quality") or "auto"), job_id)
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
                        duration=5,
                        aspect_ratio="16:9",
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
        "kling_image_bridge": kling_image_status(),
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
    return {
        "user": _user_summary(user),
        "access": _access_summary(access),
        "pack": _pack_summary(),
        "packs": _packs_summary(),
    }


@APP.post("/api/auth/request-code")
def request_auth_code(payload: RequestAuthCodeRequest) -> dict[str, Any]:
    normalized = _normalize_email(payload.email)
    if "@" not in normalized:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    try:
        code = USERS.issue_code(normalized)
        _send_auth_code_email(email=normalized, code=code)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Vision could not send the access code right now: {exc}") from exc
    return {
        "ok": True,
        "email": normalized,
        "message": "A Vision access code is on its way.",
        "packs": _packs_summary(),
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

    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(user),
            "access": _access_summary(attached_entry),
            "pack": _pack_summary(),
            "packs": _packs_summary(),
            "access_token": _access_token_for_entry(attached_entry) if attached_entry else None,
        }
    )
    _set_user_cookie(response, request, {"user_id": user["id"]})
    if attached_entry:
        _set_access_cookie(response, request, _access_token_payload(attached_entry))
    return response


@APP.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(None),
            "access": _access_summary(None),
            "pack": _pack_summary(),
            "packs": _packs_summary(),
        }
    )
    _clear_user_cookie(response, request)
    _clear_access_cookie(response, request)
    return response


@APP.post("/api/prompt/improve")
def improve_prompt(payload: ImprovePromptRequest) -> dict[str, Any]:
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
    entry = {
        "id": "admin",
        "admin": True,
        "video_remaining": None,
        "image_remaining": None,
    }
    response = JSONResponse(
        {
            "ok": True,
            "access": _access_summary(entry),
            "pack": _pack_summary(),
            "packs": _packs_summary(),
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    return response


@APP.post("/api/checkout/session")
def create_checkout_session(payload: CreateCheckoutSessionRequest, request: Request) -> dict[str, Any]:
    user = _user_from_request(request)
    resolved_email = (payload.email or "").strip() or (str(user.get("email") or "") if user else "")
    selected_pack = _pack_summary(payload.pack_id)
    try:
        session = _create_stripe_checkout_session(
            request=request,
            email=resolved_email or None,
            pack_id=str(selected_pack.get("id") or "starter"),
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
        "pack": selected_pack,
        "packs": _packs_summary(),
    }


@APP.post("/api/checkout/confirm")
def confirm_checkout(payload: ConfirmCheckoutRequest, request: Request) -> JSONResponse:
    try:
        session = _retrieve_stripe_checkout_session(payload.session_id.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if session.get("status") != "complete" or session.get("payment_status") != "paid":
        raise HTTPException(status_code=409, detail="Payment is not completed yet for this Vision pack.")

    customer_details = session.get("customer_details") or {}
    session_pack = _pack_summary((session.get("metadata") or {}).get("vision_pack_id"))
    email = customer_details.get("email") or session.get("customer_email")
    current_access = _access_from_request(request)
    current_user = _user_from_request(request)
    current_access_id = current_access.get("id") if current_access and not current_access.get("admin") else None
    normalized_session_id = payload.session_id.strip()
    entry = ACCESS.apply_paid_session(
        session_id=normalized_session_id,
        email=email,
        current_access_id=current_access_id,
        current_user_id=str(current_user.get("id")) if current_user else None,
        video_credits=_credits_from_session(session)[0],
        image_credits=_credits_from_session(session)[1],
    )
    if ACCESS.claim_notification(normalized_session_id):
        _notify_purchase_async(session=session, entry=entry)
    response = JSONResponse(
        {
            "ok": True,
            "user": _user_summary(current_user),
            "access": _access_summary(entry),
            "pack": session_pack,
            "packs": _packs_summary(),
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    if current_user:
        _set_user_cookie(response, request, {"user_id": current_user["id"]})
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
    if event_type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return {"ok": True, "handled": False}

    session = ((event.get("data") or {}).get("object") or {})
    if not isinstance(session, dict):
        return {"ok": True, "handled": False}

    session_id = str(session.get("id") or "").strip()
    if not session_id:
        return {"ok": True, "handled": False}

    if event_type == "checkout.session.completed" and session.get("payment_status") not in {"paid", "no_payment_required"}:
        return {"ok": True, "handled": False, "session_id": session_id}

    customer_details = session.get("customer_details") or {}
    email = customer_details.get("email") or session.get("customer_email")
    known_user = USERS.find_by_email(email)
    entry = ACCESS.apply_paid_session(
        session_id=session_id,
        email=email,
        current_access_id=None,
        current_user_id=str(known_user.get("id")) if known_user else None,
        video_credits=_credits_from_session(session)[0],
        image_credits=_credits_from_session(session)[1],
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
    prompt_bundle = _auto_enhance_job_prompt(payload.prompt.strip(), mode)
    requested_quality = _effective_job_quality(mode, _normalize_quality(payload.quality))
    access = _access_from_request(request)
    summary = _access_summary(access)
    if not summary["has_access"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_required",
                "message": "Unlock a Vision pack to turn this idea into a cinematic result.",
                "access": summary,
                "pack": _pack_summary(),
                "packs": _packs_summary(),
            },
        )

    charged_access_id: str | None = None
    charged_mode: str | None = None
    if not summary["admin"]:
        access_id = summary["access_id"]
        consumed = ACCESS.consume(str(access_id), mode) if access_id else None
        if consumed is None:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "insufficient_credits",
                    "message": f"Unlock another Vision pack to keep creating more {mode}s inside Vision.",
                    "access": summary,
                    "pack": _pack_summary(),
                    "packs": _packs_summary(),
                },
            )
        charged_access_id = str(access_id)
        charged_mode = mode

    job = JOBS.create(
        str(prompt_bundle["prompt"]),
        requested_quality,
        mode=mode,
        charged_access_id=charged_access_id,
        charged_mode=charged_mode,
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
    host = os.environ.get("VISION_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("VISION_GATEWAY_PORT", "8787")))
    uvicorn.run(APP, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
