from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import certifi


GATEWAY_URL = os.environ.get("VISION_GATEWAY_URL", "https://vision-gateway.onrender.com").rstrip("/")
OUTPUT_DIR = Path(os.environ.get("VISION_TRAILER_OUTPUT_DIR", "/tmp/vision_tiktok_trailer"))
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


SCENES = [
    {
        "slug": "core_cube",
        "image_prompt": (
            "Use case: stylized-concept\n"
            "Asset type: TikTok trailer first frame, vertical 9:16\n"
            "Primary request: a premium minimal futuristic studio scene with a matte black cube floating above a polished floor, "
            "surrounded by a thin chrome ribbon that curves like an orbital path\n"
            "Scene/background: clean white architectural room with soft walls, subtle reflections, high-end product-film atmosphere\n"
            "Subject: black cube and chrome orbital ribbon, centered but with space for overlay text\n"
            "Style/medium: photoreal cinematic product still, luxury technology advertising\n"
            "Composition/framing: low camera angle, strong depth, clear foreground floor reflection, enough negative space at top\n"
            "Lighting/mood: soft white key light, delicate rim light, calm mysterious launch mood\n"
            "Materials/textures: matte black ceramic cube, chrome ribbon, glossy white floor, subtle volumetric haze\n"
            "Constraints: no visible text, no logos, no watermark, no distorted geometry, no extra symbols"
        ),
        "video_prompt": (
            "Vertical 9:16 premium technology trailer shot. Use the provided first frame as the exact source scene. "
            "Do not redesign the cube, room, or chrome orbital ribbon. Camera makes a slow elegant orbit around the floating cube, "
            "then a small push-in to reveal reflective details on the ribbon. Preserve object identity and geometry, no new text, "
            "no logos, no morphing, no glitch effects, no added symbols. Smooth cinematic motion, luxury product film, subtle haze."
        ),
    },
    {
        "slug": "bonsai_room",
        "image_prompt": (
            "Use case: stylized-concept\n"
            "Asset type: TikTok trailer second frame, vertical 9:16\n"
            "Primary request: a real bonsai tree in a minimalist white room, cinematic and elegant, as if nature is being designed by a quiet AI studio\n"
            "Scene/background: quiet gallery-like room, white walls, seamless floor, one soft shadow, no brand marks\n"
            "Subject: detailed living bonsai tree in a simple stone planter, slightly elevated on a low pedestal\n"
            "Style/medium: photoreal editorial interior photography with cinematic contrast\n"
            "Composition/framing: centered bonsai, camera at planter height, negative space above for overlay text\n"
            "Lighting/mood: soft side light, serene, premium, contemplative\n"
            "Materials/textures: real bark, tiny leaves, stone planter, polished white floor\n"
            "Constraints: no visible text, no logos, no watermark, no people, no fantasy creatures"
        ),
        "video_prompt": (
            "Vertical 9:16 cinematic minimal interior shot. Use the provided first frame as the exact source scene. "
            "The camera slowly circles the bonsai at planter height, revealing bark texture, leaf depth, and the clean room geometry. "
            "Leaves may move very subtly as if from soft air, but the tree must not grow, mutate, or change shape. "
            "No text, no logos, no people, no fantasy effects. Premium calm motion, editorial studio lighting."
        ),
    },
    {
        "slug": "floating_frames",
        "image_prompt": (
            "Use case: stylized-concept\n"
            "Asset type: TikTok trailer third frame, vertical 9:16\n"
            "Primary request: a dark premium studio with several floating vertical glass frames arranged in a slow spiral path, "
            "each frame holding abstract cinematic light and texture, like a visual idea becoming a film\n"
            "Scene/background: black minimal stage, glossy floor reflections, controlled haze\n"
            "Subject: floating vertical glass frames, elegant parallax-ready composition\n"
            "Style/medium: photoreal high-end tech commercial still\n"
            "Composition/framing: camera slightly off-center, frames receding into depth, clear central path for camera movement\n"
            "Lighting/mood: cool white light edges, subtle cyan highlights, dramatic but restrained\n"
            "Materials/textures: glass, brushed metal, black floor, soft volumetric light\n"
            "Constraints: no visible text, no logos, no watermark, no readable UI, no clutter"
        ),
        "video_prompt": (
            "Vertical 9:16 premium AI studio trailer shot. Use the first frame exactly as reference. "
            "Camera glides through the spiral of floating vertical glass frames with smooth parallax and shallow depth of field. "
            "The frames should keep their abstract light textures; do not add readable UI, text, logos, faces, or random imagery. "
            "No morphing, no glitch, no chaos. Clean cinematic motion, glossy reflections, controlled haze."
        ),
    },
    {
        "slug": "workspace_portal",
        "image_prompt": (
            "Use case: stylized-concept\n"
            "Asset type: TikTok trailer final frame, vertical 9:16\n"
            "Primary request: a cinematic creator workspace at night where a laptop projects a clean luminous portal of vertical film frames into the room, "
            "suggesting a complete AI video studio in one place\n"
            "Scene/background: elegant dark desk, minimal black room, soft city light far outside a window, premium cinematic mood\n"
            "Subject: laptop and luminous portal of vertical frames, no readable screen text\n"
            "Style/medium: photoreal cinematic advertising still, luxury software launch aesthetic\n"
            "Composition/framing: camera behind and above the desk, portal rising in the center, strong leading lines\n"
            "Lighting/mood: deep blacks, cool white portal light, subtle blue accents, polished and desirable\n"
            "Materials/textures: anodized metal laptop, glass desk edge, soft haze, reflective floor\n"
            "Constraints: no visible text, no logos, no watermark, no hands, no identifiable person, no messy cables"
        ),
        "video_prompt": (
            "Vertical 9:16 cinematic software launch trailer shot. Use the first frame as the exact source scene. "
            "Camera slowly pushes from behind the desk toward the luminous portal of vertical frames, with light spilling across the laptop and desk. "
            "Keep the laptop, room, and portal consistent; no readable screen text, no logos, no people, no hands, no morphing. "
            "Premium dark tech mood, smooth motion, clean final hero energy."
        ),
    },
]


def _request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=300, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "vision-trailer-maker/1.0"}, method="GET")
    with urllib.request.urlopen(request, timeout=600, context=SSL_CONTEXT) as response:
        path.write_bytes(response.read())
    return path


def _first_found(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] not in {None, ""}:
                return obj[key]
        for value in obj.values():
            found = _first_found(value, keys)
            if found not in {None, ""}:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _first_found(item, keys)
            if found not in {None, ""}:
                return found
    return None


def _status_done(value: str) -> bool:
    return value.lower() in {"done", "completed", "complete", "success", "succeeded", "finished"}


def _status_error(value: str) -> bool:
    return value.lower() in {"error", "failed", "fail", "rejected", "cancelled", "canceled"}


def _sign_access_token(payload: dict[str, Any]) -> str:
    secret = os.environ.get("VISION_ACCESS_SECRET", "vision-dev-access-secret").strip()
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _absolute_gateway_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{GATEWAY_URL}/{url.lstrip('/')}"


def create_gateway_image(scene: dict[str, str]) -> dict[str, Any]:
    token = _sign_access_token({"admin": True})
    created = _request_json(
        f"{GATEWAY_URL}/api/jobs",
        method="POST",
        headers={"x-vision-access": token},
        payload={
            "prompt": scene["image_prompt"],
            "mode": "image",
            "provider": "openai",
            "quality": "studio",
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
    )
    job_id = str(created["id"])
    print(f"IMAGE_JOB {scene['slug']} {job_id}", flush=True)
    deadline = time.time() + 1200
    while True:
        job = _request_json(f"{GATEWAY_URL}/api/jobs/{urllib.parse.quote(job_id, safe='')}")
        status = str(job.get("status") or "unknown")
        print(f"IMAGE_POLL {scene['slug']} {status}", flush=True)
        if status == "ready":
            image_url = _absolute_gateway_url(str(job.get("output_url") or ""))
            return {"job": job, "image_url": image_url}
        if status == "failed":
            raise RuntimeError(f"Image job failed for {scene['slug']}: {job.get('error') or job.get('message')}")
        if time.time() > deadline:
            raise TimeoutError(f"Image job timed out for {scene['slug']}")
        time.sleep(8)


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


def create_seedance_clip(scene: dict[str, str], first_frame: Path) -> dict[str, Any]:
    api_key = os.environ.get("BYTEPLUS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BYTEPLUS_API_KEY is not configured.")
    base_url = os.environ.get("BYTEPLUS_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3").rstrip("/")
    model = (
        os.environ.get("BYTEPLUS_SEEDANCE_STANDARD_MODEL", "").strip()
        or os.environ.get("BYTEPLUS_SEEDANCE_FAST_MODEL", "").strip()
        or os.environ.get("BYTEPLUS_SEEDANCE_PREMIUM_MODEL", "").strip()
    )
    if not model:
        raise RuntimeError("No BYTEPLUS_SEEDANCE_* model is configured.")
    payload = {
        "model": model,
        "generate_audio": False,
        "duration": 5,
        "ratio": "9:16",
        "resolution": "720p",
        "content": [
            {"type": "text", "text": scene["video_prompt"]},
            {"type": "image_url", "image_url": {"url": _image_data_url(first_frame)}, "role": "first_frame"},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    created = _request_json(f"{base_url}/contents/generations/tasks", method="POST", headers=headers, payload=payload)
    task_id = str(_first_found(created, ("id", "task_id", "taskId")) or "")
    if not task_id:
        raise RuntimeError(f"Seedance create response did not contain a task id: {created}")
    print(f"SEEDANCE_TASK {scene['slug']} {task_id}", flush=True)
    deadline = time.time() + 2400
    final_payload = created
    while True:
        status = str(_first_found(final_payload, ("status", "task_status", "state")) or "submitted")
        print(f"SEEDANCE_POLL {scene['slug']} {status}", flush=True)
        if _status_done(status):
            break
        if _status_error(status):
            raise RuntimeError(f"Seedance failed for {scene['slug']}: {final_payload}")
        if time.time() > deadline:
            raise TimeoutError(f"Seedance timed out for {scene['slug']}")
        time.sleep(10)
        final_payload = _request_json(f"{base_url}/contents/generations/tasks/{urllib.parse.quote(task_id, safe='')}", headers=headers)
    video_url = str(_first_found(final_payload, ("video_url", "url", "download_url", "file_url")) or "")
    if not video_url:
        raise RuntimeError(f"Seedance completed without video URL for {scene['slug']}: {final_payload}")
    return {"task_id": task_id, "video_url": video_url, "model": model, "status_payload": final_payload}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "gateway_url": GATEWAY_URL,
        "scenes": [],
    }
    for scene in SCENES:
        slug = scene["slug"]
        image_result = create_gateway_image(scene)
        image_path = _download(image_result["image_url"], OUTPUT_DIR / f"{slug}.png")
        seedance_result = create_seedance_clip(scene, image_path)
        manifest["scenes"].append(
            {
                "slug": slug,
                "image_url": image_result["image_url"],
                "image_job_id": image_result["job"].get("id"),
                "seedance_task_id": seedance_result["task_id"],
                "seedance_model": seedance_result["model"],
                "video_url": seedance_result["video_url"],
            }
        )
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("VISION_TRAILER_MANIFEST_JSON=" + json.dumps(manifest, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
