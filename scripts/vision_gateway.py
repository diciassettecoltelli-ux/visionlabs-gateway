from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import secrets
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
        return ["studio", "director", "fast"]
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
    prompt_state = _prompt_status()
    return {
        "ready": bool(image_state.get("ready") or video_state.get("ready") or prompt_state.get("ready")),
        "image": image_state,
        "video": video_state,
        "prompt": prompt_state,
    }


def _select_image_route() -> dict[str, str]:
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


def _pack_price_cents() -> int:
    try:
        return max(99, int(os.environ.get("VISION_PACK_PRICE_CENTS", "199")))
    except ValueError:
        return 199


def _pack_currency() -> str:
    currency = os.environ.get("VISION_PACK_CURRENCY", "eur").strip().lower()
    return currency or "eur"


def _pack_video_credits() -> int:
    try:
        return max(1, int(os.environ.get("VISION_PACK_VIDEO_CREDITS", "1")))
    except ValueError:
        return 1


def _pack_image_credits() -> int:
    try:
        return max(0, int(os.environ.get("VISION_PACK_IMAGE_CREDITS", "5")))
    except ValueError:
        return 5


def _pack_name() -> str:
    return os.environ.get("VISION_PACK_NAME", "Vision Pack").strip() or "Vision Pack"


def _pack_description() -> str:
    return os.environ.get(
        "VISION_PACK_DESCRIPTION",
        f"{_pack_video_credits()} videos + {_pack_image_credits()} images",
    ).strip() or f"{_pack_video_credits()} videos + {_pack_image_credits()} images"


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


def _prompt_status() -> dict[str, Any]:
    return google_prompt_status()


def _write_purchase_notification(record: dict[str, Any]) -> None:
    log_path = _notification_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send_purchase_notification_email(record: dict[str, Any]) -> None:
    recipients = _notification_recipients()
    host = os.environ.get("VISION_NOTIFY_SMTP_HOST", "").strip()
    if not recipients or not host:
        return

    port = int(os.environ.get("VISION_NOTIFY_SMTP_PORT", "587"))
    username = os.environ.get("VISION_NOTIFY_SMTP_USERNAME", "").strip()
    password = os.environ.get("VISION_NOTIFY_SMTP_PASSWORD", "").strip()
    use_ssl = os.environ.get("VISION_NOTIFY_SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
    use_starttls = os.environ.get("VISION_NOTIFY_SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes", "on"}

    message = EmailMessage()
    message["Subject"] = f"New Vision Pack purchase · {record.get('email') or 'unknown email'}"
    message["From"] = _notification_sender()
    message["To"] = ", ".join(recipients)
    body = [
        "A new Vision Pack purchase has been confirmed.",
        "",
        f"Email: {record.get('email') or 'not provided'}",
        f"Pack: {record.get('pack_name')}",
        f"Credits: {record.get('video_credits')} videos + {record.get('image_credits')} images",
        f"Amount: {record.get('amount_total')} {str(record.get('currency') or '').upper()}",
        f"Access ID: {record.get('access_id')}",
        f"Checkout session: {record.get('session_id')}",
        f"Purchased at: {record.get('confirmed_at')}",
    ]
    message.set_content("\n".join(body))

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


def _notify_purchase_async(*, session: dict[str, Any], entry: dict[str, Any]) -> None:
    record = {
        "session_id": session.get("id"),
        "email": entry.get("email"),
        "access_id": entry.get("id"),
        "pack_name": _pack_name(),
        "video_credits": _pack_video_credits(),
        "image_credits": _pack_image_credits(),
        "amount_total": (session.get("amount_total") or _pack_price_cents()) / 100,
        "currency": session.get("currency") or _pack_currency(),
        "confirmed_at": _now_iso(),
    }

    def _worker() -> None:
        try:
            _write_purchase_notification(record)
            _send_purchase_notification_email(record)
        except Exception as exc:
            print(f"[vision] purchase notification failed: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


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


def _strip_none_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _stripe_secret_key() -> str:
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("Stripe checkout is not configured yet.")
    return secret


def _stripe_request(method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.stripe.com{path}"
    encoded_data: bytes | None = None
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{_stripe_secret_key()}:".encode("utf-8")).decode("ascii"),
    }
    if data is not None:
        encoded_data = urllib.parse.urlencode(_strip_none_values(data), doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=encoded_data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Stripe API error ({exc.code}): {body}") from exc


def _create_stripe_checkout_session(*, request: Request, email: str | None) -> dict[str, Any]:
    frontend_base = _frontend_base_url(request)
    payload: dict[str, Any] = {
        "mode": "payment",
        "success_url": f"{frontend_base}/studio/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{frontend_base}/studio/?checkout=cancel",
        "allow_promotion_codes": "true",
        "billing_address_collection": "auto",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": _pack_currency(),
        "line_items[0][price_data][unit_amount]": str(_pack_price_cents()),
        "line_items[0][price_data][product_data][name]": _pack_name(),
        "line_items[0][price_data][product_data][description]": _pack_description(),
        "metadata[vision_pack_name]": _pack_name(),
        "metadata[vision_pack_video_credits]": str(_pack_video_credits()),
        "metadata[vision_pack_image_credits]": str(_pack_image_credits()),
    }
    if email:
        payload["customer_email"] = email
    return _stripe_request("POST", "/v1/checkout/sessions", payload)


def _retrieve_stripe_checkout_session(session_id: str) -> dict[str, Any]:
    encoded_session_id = urllib.parse.quote(session_id, safe="")
    return _stripe_request("GET", f"/v1/checkout/sessions/{encoded_session_id}")


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


def _pack_summary() -> dict[str, Any]:
    price_cents = _pack_price_cents()
    currency = _pack_currency()
    return {
        "name": _pack_name(),
        "description": _pack_description(),
        "price_cents": price_cents,
        "price_display": f"{price_cents / 100:.2f} {currency}".replace(".", ",") if currency == "eur" else f"{price_cents / 100:.2f} {currency.upper()}",
        "currency": currency,
        "video_credits": _pack_video_credits(),
        "image_credits": _pack_image_credits(),
    }


def _access_token_payload(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("admin"):
        return {"admin": True}
    return {"access_id": entry["id"]}


def _access_token_for_entry(entry: dict[str, Any]) -> str:
    return _sign_access_token(_access_token_payload(entry))


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
    if not payload:
        return None
    if payload.get("admin"):
        return {
            "id": "admin",
            "admin": True,
            "video_remaining": None,
            "image_remaining": None,
        }
    access_id = str(payload.get("access_id") or "")
    if not access_id:
        return None
    return ACCESS.get(access_id)



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
ACCESS_FILE = RUNTIME_ROOT / "access.json"
OUTPUT_ROOT = VISION_ROOT / "generated"
DISABLE_FILE = RUNTIME_ROOT / "gateway.disabled"

for path in (RUNTIME_ROOT, OUTPUT_ROOT):
    path.mkdir(parents=True, exist_ok=True)

APP.mount("/generated", StaticFiles(directory=str(OUTPUT_ROOT)), name="generated")


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=5000)
    quality: str | None = Field(default=None, min_length=4, max_length=16)
    mode: str | None = Field(default="video", min_length=5, max_length=16)


class CreateCheckoutSessionRequest(BaseModel):
    email: str | None = Field(default=None, max_length=320)


class ConfirmCheckoutRequest(BaseModel):
    session_id: str = Field(min_length=10, max_length=255)


class AdminUnlockRequest(BaseModel):
    token: str = Field(min_length=8, max_length=255)


class ImprovePromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1500)
    mode: str | None = Field(default="video", min_length=5, max_length=16)


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

    def apply_paid_session(self, *, session_id: str, email: str | None, current_access_id: str | None) -> dict[str, Any]:
        with self.lock:
            existing_access_id = self.applied_sessions.get(session_id)
            if existing_access_id and existing_access_id in self.entries:
                return dict(self.entries[existing_access_id])

            if current_access_id and current_access_id in self.entries and not self.entries[current_access_id].get("admin"):
                entry = self.entries[current_access_id]
            else:
                access_id = uuid.uuid4().hex[:16]
                entry = {
                    "id": access_id,
                    "admin": False,
                    "email": email,
                    "video_remaining": 0,
                    "image_remaining": 0,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "stripe_sessions": [],
                }
                self.entries[access_id] = entry

            entry["video_remaining"] = int(entry.get("video_remaining", 0)) + _pack_video_credits()
            entry["image_remaining"] = int(entry.get("image_remaining", 0)) + _pack_image_credits()
            entry["updated_at"] = _now_iso()
            if email and not entry.get("email"):
                entry["email"] = email
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


JOBS = JobsStore(JOBS_FILE)
ACCESS = AccessStore(ACCESS_FILE)
QUEUE: queue.Queue[str] = queue.Queue()


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

        route = _select_generation_route(str(job.get("quality") or "auto"), job_id)
        JOBS.update(
            job_id,
            provider=route["provider"],
            quality=route["quality"],
            status="preparing",
            message="Shaping the cinematic direction inside Vision.",
        )
        if route["provider"] == "byteplus_seedance":
            JOBS.update(job_id, status="generating", message="Building your cinematic render inside Vision.")
            output_video = generate_seedance_video(
                prompt=job["prompt"],
                output_dir=output_dir,
                model=route["model"],
                duration=5,
                aspect_ratio="16:9",
                resolution=route["resolution"],
            )
        elif route["provider"] == "google_veo":
            JOBS.update(job_id, status="generating", message="Building your cinematic render inside Vision.")
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
            JOBS.update(job_id, status="generating", message="Building your cinematic render inside Vision.")
            output_video = generate_kling_session_bridge(
                prompt=job["prompt"],
                output_dir=output_dir,
            )
        JOBS.update(job_id, status="downloading", message="Finishing and importing your result into Vision.")
        JOBS.update(
            job_id,
            status="ready",
            message="Ready.",
            output_path=str(output_video),
            output_url=_public_output_url(job_id, output_video.name),
            output_type="video",
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
    access = _access_from_request(request)
    return {
        "access": _access_summary(access),
        "pack": _pack_summary(),
    }


@APP.post("/api/prompt/improve")
def improve_prompt(payload: ImprovePromptRequest) -> dict[str, Any]:
    mode = _normalize_mode(payload.mode)
    result = improve_vision_prompt(prompt=payload.prompt.strip(), mode=mode)
    return {
        "ok": True,
        "mode": mode,
        **result,
    }


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
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    return response


@APP.post("/api/checkout/session")
def create_checkout_session(payload: CreateCheckoutSessionRequest, request: Request) -> dict[str, Any]:
    try:
        session = _create_stripe_checkout_session(request=request, email=(payload.email or "").strip() or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    checkout_url = session.get("url")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Stripe did not return a hosted checkout URL.")
    return {
        "session_id": session.get("id"),
        "url": checkout_url,
        "pack": _pack_summary(),
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
    email = customer_details.get("email") or session.get("customer_email")
    current_access = _access_from_request(request)
    current_access_id = current_access.get("id") if current_access and not current_access.get("admin") else None
    normalized_session_id = payload.session_id.strip()
    entry = ACCESS.apply_paid_session(
        session_id=normalized_session_id,
        email=email,
        current_access_id=current_access_id,
    )
    if ACCESS.claim_notification(normalized_session_id):
        _notify_purchase_async(session=session, entry=entry)
    response = JSONResponse(
        {
            "ok": True,
            "access": _access_summary(entry),
            "pack": _pack_summary(),
            "access_token": _access_token_for_entry(entry),
        }
    )
    _set_access_cookie(response, request, _access_token_payload(entry))
    return response


@APP.post("/api/jobs")
def create_job(payload: CreateJobRequest, request: Request) -> dict[str, Any]:
    mode = _normalize_mode(payload.mode)
    access = _access_from_request(request)
    summary = _access_summary(access)
    if not summary["has_access"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_required",
                "message": "Unlock Vision Pack to turn this idea into a cinematic result.",
                "access": summary,
                "pack": _pack_summary(),
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
                    "message": f"Unlock another Vision Pack to keep creating more {mode}s inside Vision.",
                    "access": summary,
                    "pack": _pack_summary(),
                },
            )
        charged_access_id = str(access_id)
        charged_mode = mode

    job = JOBS.create(
        payload.prompt.strip(),
        _normalize_quality(payload.quality),
        mode=mode,
        charged_access_id=charged_access_id,
        charged_mode=charged_mode,
    )
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
