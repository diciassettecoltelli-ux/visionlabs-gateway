from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import ssl
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi


REPO_ROOT = Path(__file__).resolve().parents[1]
ATLAS_USER_DATA_ROOT = Path(
    os.environ.get(
        "VISION_ATLAS_USER_DATA_ROOT",
        str(Path.home() / "Library/Application Support/com.openai.atlas/browser-data/host"),
    )
)
ATLAS_USER_PREFIX = "user-AHFCHj1ZnVqcRd5JxyUkImLy__"
KLING_OMNI_URL = "https://kling.ai/app/omni/new"
RUNTIME_ROOT = Path(os.environ.get("VISION_KLING_RUNTIME_ROOT", str(REPO_ROOT / ".runtime")))
STATUS_FILE = RUNTIME_ROOT / "kling_session_bridge_status.json"
COOKIE_HEADER_FILE = RUNTIME_ROOT / "kling_cookie_header.txt"
COOKIE_HEADER_EXAMPLE_FILE = RUNTIME_ROOT / "kling_cookie_header.example.txt"
REQUEST_HEADERS_FILE = RUNTIME_ROOT / "kling_request_headers.json"
SUBMIT_PAYLOAD_FILE = RUNTIME_ROOT / "kling_submit_payload.sample.json"
IMAGE_SUBMIT_PAYLOAD_FILE = RUNTIME_ROOT / "kling_image_submit_payload.sample.json"
FORMATTER_BUNDLE_URL = (
    "https://s15-kling.klingai.com/kos/s101/nlav112918/kling-web/assets/js/formatter-zn7YLI44.js"
)
SIG4_RUNTIME_SCRIPT = Path(
    os.environ.get("VISION_KLING_SIG4_RUNTIME_SCRIPT", str(REPO_ROOT / "scripts/kling_sig4_runtime.mjs"))
)

COOKIE_FILES = ("Cookies", "Default/Cookies")
LEVELDB_DIRS = (
    "Local Storage/leveldb",
    "Session Storage",
    "IndexedDB/https_kling.ai_0.indexeddb.leveldb",
)

COOKIE_NAME_RE = re.compile(rb"(passToken|kGateway-identity|did|__risk_web_device_id|userId|teamId|accept-language)")
TEXT_TOKEN_RE = re.compile(
    rb"(crossAppClientSessionId|x-session-id|_logininfo|passToken|kGateway-identity|__risk_web_device_id|did)"
)


class SessionBridgeNotReadyError(RuntimeError):
    """Raised when the Kling web session is not yet reusable server-side."""


@dataclass
class RuntimeCookieHeader:
    raw: str
    cookies: dict[str, str]

    @property
    def has_required_auth(self) -> bool:
        return all(self.cookies.get(name) for name in ("kGateway-identity", "did"))


@dataclass
class RuntimeRequestHeaders:
    headers: dict[str, str]


@dataclass
class RuntimeSubmitPayload:
    payload: dict[str, Any]

    @property
    def is_complete(self) -> bool:
        payload = self.payload
        if not isinstance(payload, dict):
            return False
        payload_type = str(payload.get("type", ""))
        arguments = payload.get("arguments", [])
        if not isinstance(arguments, list):
            return False
        names = {str(item.get("name", "")) for item in arguments if isinstance(item, dict)}
        video_ready = (
            bool(payload_type)
            and "PASTE_" not in payload_type
            and {"kling_version", "model_mode", "prompt", "rich_prompt"} <= names
        )
        image_ready = (
            bool(payload_type)
            and "PASTE_" not in payload_type
            and {"kolors_version", "img_resolution", "imageCount", "prompt", "rich_prompt"} <= names
        )
        return video_ready or image_ready


@dataclass
class BridgeArtifacts:
    profile_dir: Path | None
    cookie_names: list[str]
    session_markers: dict[str, list[str]]
    indexeddb_markers: list[str]
    web_contract: dict[str, Any]
    runtime_cookie_header: RuntimeCookieHeader | None
    runtime_request_headers: RuntimeRequestHeaders | None
    runtime_submit_payload: RuntimeSubmitPayload | None
    runtime_image_submit_payload: RuntimeSubmitPayload | None

    @property
    def has_cookie_auth(self) -> bool:
        names = set(self.cookie_names)
        return bool({"passToken", "kGateway-identity"} & names)

    @property
    def has_session_markers(self) -> bool:
        return any(self.session_markers.values())


def _parse_cookie_header(raw: str) -> RuntimeCookieHeader:
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies[name] = value
    return RuntimeCookieHeader(raw=raw.strip(), cookies=cookies)


def _load_runtime_cookie_header() -> RuntimeCookieHeader | None:
    env_value = os.environ.get("VISION_KLING_COOKIE_HEADER", "").strip()
    raw = env_value
    if not raw and COOKIE_HEADER_FILE.exists():
        raw = COOKIE_HEADER_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return _parse_cookie_header(raw)


def _load_runtime_request_headers() -> RuntimeRequestHeaders | None:
    env_value = os.environ.get("VISION_KLING_REQUEST_HEADERS_JSON", "").strip()
    if env_value:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            headers = {str(key): str(value) for key, value in payload.items() if value is not None}
            return RuntimeRequestHeaders(headers=headers)
    if not REQUEST_HEADERS_FILE.exists():
        return None
    try:
        payload = json.loads(REQUEST_HEADERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    headers = {str(key): str(value) for key, value in payload.items() if value is not None}
    return RuntimeRequestHeaders(headers=headers)


def _load_runtime_submit_payload() -> RuntimeSubmitPayload | None:
    env_value = os.environ.get("VISION_KLING_SUBMIT_PAYLOAD_JSON", "").strip()
    if env_value:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return RuntimeSubmitPayload(payload=payload)
    if not SUBMIT_PAYLOAD_FILE.exists():
        return None
    try:
        payload = json.loads(SUBMIT_PAYLOAD_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return RuntimeSubmitPayload(payload=payload)


def _load_runtime_image_submit_payload() -> RuntimeSubmitPayload | None:
    env_value = os.environ.get("VISION_KLING_IMAGE_SUBMIT_PAYLOAD_JSON", "").strip()
    if env_value:
        try:
            payload = json.loads(env_value)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return RuntimeSubmitPayload(payload=payload)
    if not IMAGE_SUBMIT_PAYLOAD_FILE.exists():
        return None
    try:
        payload = json.loads(IMAGE_SUBMIT_PAYLOAD_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return RuntimeSubmitPayload(payload=payload)


def _ensure_cookie_header_example() -> None:
    COOKIE_HEADER_EXAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COOKIE_HEADER_EXAMPLE_FILE.exists():
        COOKIE_HEADER_EXAMPLE_FILE.write_text(
            (
                "kGateway-identity=PASTE_REAL_VALUE;"
                " did=PASTE_REAL_VALUE;"
                " teamId=OPTIONAL_VALUE;"
                " userId=OPTIONAL_VALUE;"
                " passToken=OPTIONAL_IF_PRESENT"
            ),
            encoding="utf-8",
        )

    if not REQUEST_HEADERS_FILE.exists():
        REQUEST_HEADERS_FILE.write_text(
            json.dumps(
                {
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "en-001",
                    "content-type": "application/json",
                    "origin": "https://kling.ai",
                    "referer": "https://kling.ai/app/omni/new",
                    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                    "time-zone": "Europe/Rome",
                    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if not SUBMIT_PAYLOAD_FILE.exists():
        SUBMIT_PAYLOAD_FILE.write_text(
            json.dumps(
                {
                    "type": "m2v_omni_video",
                    "inputs": [],
                    "arguments": [
                        {"name": "kling_version", "value": "3.0-omni"},
                        {"name": "model_mode", "value": "pro"},
                        {"name": "prompt", "value": "PASTE_PROMPT"},
                        {"name": "rich_prompt", "value": "PASTE_PROMPT"},
                    ],
                    "callbackPayloads": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if not IMAGE_SUBMIT_PAYLOAD_FILE.exists():
        IMAGE_SUBMIT_PAYLOAD_FILE.write_text(
            json.dumps(
                {
                    "type": "PASTE_REAL_KLING_IMAGE_TYPE",
                    "inputs": [],
                    "arguments": [
                        {"name": "kling_version", "value": "PASTE_REAL_VERSION"},
                        {"name": "model_mode", "value": "pro"},
                        {"name": "prompt", "value": "PASTE_PROMPT"},
                        {"name": "rich_prompt", "value": "PASTE_PROMPT"},
                    ],
                    "callbackPayloads": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def _latest_atlas_profile_dir() -> Path | None:
    if not ATLAS_USER_DATA_ROOT.exists():
        return None
    candidates = sorted(
        (path for path in ATLAS_USER_DATA_ROOT.iterdir() if path.is_dir() and path.name.startswith(ATLAS_USER_PREFIX)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _copy_sqlite(src: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="vision-kling-session-"))
    dest = tmpdir / src.name
    shutil.copy2(src, dest)
    wal = src.with_name(src.name + "-wal")
    shm = src.with_name(src.name + "-shm")
    if wal.exists():
        shutil.copy2(wal, tmpdir / wal.name)
    if shm.exists():
        shutil.copy2(shm, tmpdir / shm.name)
    return dest


def _read_cookie_names(profile_dir: Path) -> list[str]:
    names: set[str] = set()
    for rel in COOKIE_FILES:
        cookie_path = profile_dir / rel
        if not cookie_path.exists():
            continue
        copied = _copy_sqlite(cookie_path)
        try:
            with sqlite3.connect(copied) as conn:
                rows = conn.execute(
                    """
                    select name
                    from cookies
                    where host_key like '%kling.ai%'
                    """
                ).fetchall()
                names.update(str(row[0]) for row in rows if row and row[0])
        except sqlite3.DatabaseError:
            continue
        finally:
            shutil.rmtree(copied.parent, ignore_errors=True)
    return sorted(names)


def _extract_markers_from_file(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    found = {match.decode("utf-8", "ignore") for match in TEXT_TOKEN_RE.findall(data)}
    return sorted(marker for marker in found if marker)


def _scan_leveldb_markers(profile_dir: Path) -> dict[str, list[str]]:
    markers: dict[str, list[str]] = {}
    for rel in LEVELDB_DIRS:
        directory = profile_dir / rel
        found: set[str] = set()
        if directory.exists():
            for path in directory.glob("*.ldb"):
                found.update(_extract_markers_from_file(path))
                if len(found) >= 8:
                    break
        markers[rel] = sorted(found)
    return markers


def _scan_indexeddb_markers(profile_dir: Path) -> list[str]:
    directory = profile_dir / "IndexedDB/https_kling.ai_0.indexeddb.leveldb"
    results: set[str] = set()
    if not directory.exists():
        return []
    keywords = (
        b"creativeId",
        b"taskId",
        b"taskInfo",
        b"m2v_omni_video",
        b"showPrice",
        b"kling_version",
        b"omniRecognition",
    )
    for path in directory.glob("*.ldb"):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for keyword in keywords:
            if keyword in data:
                results.add(keyword.decode("utf-8"))
    return sorted(results)


def _collect_artifacts() -> BridgeArtifacts:
    _ensure_cookie_header_example()
    profile_dir = _latest_atlas_profile_dir()
    if not profile_dir:
        return BridgeArtifacts(
            profile_dir=None,
            cookie_names=[],
            session_markers={},
            indexeddb_markers=[],
            web_contract=_discover_web_contract(),
            runtime_cookie_header=_load_runtime_cookie_header(),
            runtime_request_headers=_load_runtime_request_headers(),
            runtime_submit_payload=_load_runtime_submit_payload(),
            runtime_image_submit_payload=_load_runtime_image_submit_payload(),
        )
    return BridgeArtifacts(
        profile_dir=profile_dir,
        cookie_names=_read_cookie_names(profile_dir),
        session_markers=_scan_leveldb_markers(profile_dir),
        indexeddb_markers=_scan_indexeddb_markers(profile_dir),
        web_contract=_discover_web_contract(),
        runtime_cookie_header=_load_runtime_cookie_header(),
        runtime_request_headers=_load_runtime_request_headers(),
        runtime_submit_payload=_load_runtime_submit_payload(),
        runtime_image_submit_payload=_load_runtime_image_submit_payload(),
    )


def _fetch_bundle_text(url: str) -> str | None:
    try:
        proc = subprocess.run(
            ["curl", "-k", "-L", "--fail", "--silent", "--show-error", url],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout or None


def _extract_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    value = match.group(1) if match.lastindex else match.group(0)
    return value.strip() if isinstance(value, str) else value


def _extract_many(pattern: str, text: str) -> list[str]:
    return sorted({match.strip() for match in re.findall(pattern, text)})


def _discover_web_contract() -> dict[str, Any]:
    bundle = _fetch_bundle_text(FORMATTER_BUNDLE_URL)
    if not bundle:
        return {
            "bundle_url": FORMATTER_BUNDLE_URL,
            "bundle_loaded": False,
            "submit_endpoint": None,
            "status_endpoint": None,
            "signature_query_param": None,
            "signature_version_param": None,
            "requires_sig4": False,
            "submit_body_keys": [],
            "auth_paths": [],
            "notes": ["Formatter bundle could not be fetched for contract discovery."],
        }

    submit_endpoint = _extract_first(r'e\.taskId\?"/api/task/special-effects/random":"([^"]+)"', bundle)
    status_endpoint = _extract_first(r'N\.get\(`([^`]+task/status)\?taskId=', bundle)
    if not status_endpoint and "/api/task/status?taskId=" in bundle:
        status_endpoint = "/api/task/status"

    auth_paths = _extract_many(r'"/pass/[^"]+"', bundle)
    requires_sig4 = "Failed to generate sig4:" in bundle or "getSig4(" in bundle or "__NS_hxfalcon=" in bundle
    signature_version_param = "caver" if ("$getCatVersion" in bundle and "__NS_hxfalcon=" in bundle) else None
    sig4_app_key = _extract_first(r'sig4:\{projectInfo:\{appKey:"([^"]+)"', bundle)
    sig4_radar_id = _extract_first(r'sig4:\{projectInfo:\{appKey:"[^"]+",radarId:"([^"]+)"', bundle)
    contract = {
        "bundle_url": FORMATTER_BUNDLE_URL,
        "bundle_loaded": True,
        "submit_endpoint": submit_endpoint,
        "status_endpoint": status_endpoint,
        "signature_query_param": "__NS_hxfalcon" if "__NS_hxfalcon=" in bundle else None,
        "signature_version_param": signature_version_param,
        "requires_sig4": requires_sig4,
        "sig4_project_info": {
            "appKey": sig4_app_key,
            "radarId": sig4_radar_id,
            "debug": False,
        },
        "sig4_runtime_script": str(SIG4_RUNTIME_SCRIPT),
        "submit_body_keys": ["type", "inputs", "arguments", "extraArgs"],
        "auth_paths": auth_paths,
        "notes": [],
    }

    if "$getCatVersion" in bundle:
        contract["notes"].append("Submit signing passes caver in the query payload before appending __NS_hxfalcon.")
    if "requestBody:p" in bundle or "requestBody:{}" in bundle:
        contract["notes"].append("The Sig4 helper signs either a form body or a JSON requestBody, depending on Content-Type.")
    if "__NS_hxfalcon=" in bundle and "delete c.__NS_hxfalcon" in bundle:
        contract["notes"].append("The helper strips any existing __NS_hxfalcon before regenerating the signature.")
    if "document.cookie" in bundle or "query.caver must exist!" in bundle:
        contract["notes"].append("The low-level signature input mixes path, sorted query/form pairs, selected cookies, and requestBody JSON.")
    if 'm!=="encryptHeaders"' in bundle:
        contract["notes"].append("Submit form excludes encryptHeaders before signing.")
    if "Failed to generate sig4:" in bundle and "getSig4(" in bundle:
        contract["notes"].append("A dedicated Sig4 request interceptor wraps the submit URL before the POST is sent.")
    if '"/api/task/submit"' in bundle and 'N.post(s,e)' in bundle:
        contract["notes"].append("Web submit posts the full task payload to /api/task/submit after signature augmentation.")
    return contract


def _status_payload(artifacts: BridgeArtifacts) -> dict[str, Any]:
    session_detected = artifacts.has_cookie_auth and artifacts.has_session_markers
    runtime_cookie_ready = bool(artifacts.runtime_cookie_header and artifacts.runtime_cookie_header.has_required_auth)
    runtime_submit_payload_ready = bool(
        artifacts.runtime_submit_payload and artifacts.runtime_submit_payload.is_complete
    )
    runtime_image_submit_payload_ready = bool(
        artifacts.runtime_image_submit_payload and artifacts.runtime_image_submit_payload.is_complete
    )
    ready = (
        runtime_cookie_ready
        and bool(artifacts.web_contract.get("signature_query_param"))
        and runtime_submit_payload_ready
    )
    if ready:
        message = "Kling session bridge is ready with runtime cookies and Sig4 signing."
    elif runtime_cookie_ready:
        message = "Runtime cookies and signer are ready. Paste the real Request Payload JSON to unlock submit."
    elif artifacts.runtime_cookie_header:
        message = "Runtime cookie header loaded, but required auth cookies are incomplete."
    elif session_detected:
        message = "Kling web session detected, but runtime cookie header has not been provided yet."
    else:
        message = "Kling web session markers are incomplete."
    payload = {
        "ready": ready,
        "mode": "kling_web_session_bridge",
        "message": message,
        "session_detected": session_detected,
        "auth_extraction_complete": runtime_cookie_ready,
        "profile_dir": str(artifacts.profile_dir) if artifacts.profile_dir else None,
        "kling_omni_url": KLING_OMNI_URL,
        "cookie_names": artifacts.cookie_names,
        "session_markers": artifacts.session_markers,
        "indexeddb_markers": artifacts.indexeddb_markers,
        "web_contract": artifacts.web_contract,
        "runtime_cookie_header_file": str(COOKIE_HEADER_FILE),
        "runtime_cookie_header_example_file": str(COOKIE_HEADER_EXAMPLE_FILE),
        "runtime_cookie_names": sorted(artifacts.runtime_cookie_header.cookies.keys()) if artifacts.runtime_cookie_header else [],
        "runtime_cookie_ready": runtime_cookie_ready,
        "runtime_request_headers_file": str(REQUEST_HEADERS_FILE),
        "runtime_request_header_names": sorted(artifacts.runtime_request_headers.headers.keys()) if artifacts.runtime_request_headers else [],
        "runtime_submit_payload_file": str(SUBMIT_PAYLOAD_FILE),
        "runtime_submit_payload_ready": runtime_submit_payload_ready,
        "runtime_image_submit_payload_file": str(IMAGE_SUBMIT_PAYLOAD_FILE),
        "runtime_image_submit_payload_ready": runtime_image_submit_payload_ready,
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def status() -> dict[str, Any]:
    return _status_payload(_collect_artifacts())


def status_image() -> dict[str, Any]:
    artifacts = _collect_artifacts()
    payload = _status_payload(artifacts)
    image_ready = (
        bool(payload.get("runtime_cookie_ready"))
        and bool(payload.get("web_contract", {}).get("signature_query_param"))
        and bool(payload.get("runtime_image_submit_payload_ready"))
    )
    missing: list[str] = []
    if not payload.get("runtime_cookie_ready"):
        missing.append("runtime cookie header")
    if not payload.get("runtime_image_submit_payload_ready"):
        missing.append("real image submit payload")
    if image_ready:
        message = "Kling image bridge is ready with runtime cookies and image payload."
    else:
        joined = " and ".join(missing) if missing else "runtime setup"
        message = (
            f"Kling image bridge still needs {joined}. "
            "You can import a real browser request with scripts/import_kling_request.py."
        )
    return {
        **payload,
        "ready": image_ready,
        "message": message,
        "mode": "kling_web_image_bridge",
    }


def prepare() -> dict[str, Any]:
    return status()


def sign_submit_payload(*, request_body: dict[str, Any], query: dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    payload = {
        "url": "/api/task/submit",
        "query": query,
        "requestBody": request_body,
    }
    proc = subprocess.run(
        ["node", str(SIG4_RUNTIME_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


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


def _status_done(value: str) -> bool:
    normalized = value.lower()
    return normalized in {"done", "completed", "complete", "success", "succeeded", "succeed", "finished", "99"}


def _status_error(value: str) -> bool:
    normalized = value.lower()
    return normalized in {"error", "failed", "fail", "rejected", "cancelled", "-1"}


def _status_value(payload: dict[str, Any]) -> str:
    task = payload.get("data", {}).get("task") if isinstance(payload.get("data"), dict) else None
    value = (
        _first_found(task, ("task_status", "status", "state"))
        or _first_found(payload.get("data"), ("status", "task_status", "state"))
        or _first_found(payload, ("task_status", "status", "state"))
        or "submitted"
    )
    return str(value)


def _request_headers(artifacts: BridgeArtifacts, *, include_content_type: bool = True) -> dict[str, str]:
    headers = dict(artifacts.runtime_request_headers.headers if artifacts.runtime_request_headers else {})
    if include_content_type:
        headers.setdefault("content-type", "application/json")
    else:
        headers.pop("content-type", None)
    if artifacts.runtime_cookie_header:
        headers["cookie"] = artifacts.runtime_cookie_header.raw
    return headers


def _json_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=300, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"Kling session bridge HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, ConnectionResetError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Kling session bridge network error after retries: {last_error}") from last_error


def _download(url: str, output_video: Path, *, headers: dict[str, str]) -> Path:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = ssl.create_default_context(cafile=certifi.where())
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=600, context=context) as response:
                output_video.write_bytes(response.read())
            return output_video
        except (urllib.error.URLError, ConnectionResetError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Kling session bridge download error after retries: {last_error}") from last_error


def _override_prompt_in_payload(template: dict[str, Any], prompt: str) -> dict[str, Any]:
    payload = json.loads(json.dumps(template))
    arguments = payload.get("arguments", [])
    if isinstance(arguments, list):
        for item in arguments:
            if not isinstance(item, dict):
                continue
            if item.get("name") in {"prompt", "rich_prompt"}:
                item["value"] = prompt
    return payload


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _set_argument_value(payload: dict[str, Any], name: str, value: Any, *, set_by_user: bool | None = None) -> None:
    arguments = payload.get("arguments")
    if not isinstance(arguments, list):
        return
    for item in arguments:
        if not isinstance(item, dict):
            continue
        if item.get("name") == name:
            item["value"] = value
            if set_by_user is not None:
                item["setByUser"] = set_by_user
            return
    new_item: dict[str, Any] = {"name": name, "value": value}
    if set_by_user is not None:
        new_item["setByUser"] = set_by_user
    arguments.append(new_item)


def _image_quality_settings(quality: str) -> dict[str, Any]:
    normalized = quality if quality in {"fast", "studio", "director"} else "studio"
    resolution = {
        "fast": os.environ.get("VISION_KLING_IMAGE_FAST_RESOLUTION", "1k").strip().lower(),
        "studio": os.environ.get("VISION_KLING_IMAGE_STANDARD_RESOLUTION", "2k").strip().lower(),
        "director": os.environ.get("VISION_KLING_IMAGE_PREMIUM_RESOLUTION", "4k").strip().lower(),
    }[normalized]
    show_price = {
        "fast": int(os.environ.get("VISION_KLING_IMAGE_FAST_SHOW_PRICE", "0").strip() or "0"),
        "studio": int(os.environ.get("VISION_KLING_IMAGE_STANDARD_SHOW_PRICE", "0").strip() or "0"),
        "director": int(os.environ.get("VISION_KLING_IMAGE_PREMIUM_SHOW_PRICE", "200").strip() or "200"),
    }[normalized]
    unlimited = {
        "fast": _env_bool("VISION_KLING_IMAGE_FAST_UNLIMITED", True),
        "studio": _env_bool("VISION_KLING_IMAGE_STANDARD_UNLIMITED", True),
        "director": _env_bool("VISION_KLING_IMAGE_PREMIUM_UNLIMITED", False),
    }[normalized]
    return {
        "resolution": resolution,
        "show_price": show_price,
        "unlimited": unlimited,
    }


def _override_image_quality(payload: dict[str, Any], quality: str) -> dict[str, Any]:
    tuned = json.loads(json.dumps(payload))
    settings = _image_quality_settings(quality)
    _set_argument_value(tuned, "img_resolution", settings["resolution"], set_by_user=True)
    _set_argument_value(tuned, "showPrice", settings["show_price"])
    _set_argument_value(tuned, "__isUnLimited", settings["unlimited"])
    return tuned


def _extract_task_id(payload: dict[str, Any]) -> str | None:
    value = _first_found(payload, ("task_id", "taskId", "id", "job_id", "creativeId"))
    return str(value) if value not in {None, ""} else None


def _build_status_url(task_id: str) -> str:
    payload = {"url": "/api/task/status", "query": {"taskId": task_id}, "requestBody": {}}
    proc = subprocess.run(
        ["node", str(SIG4_RUNTIME_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    signed = json.loads(proc.stdout)
    sig = signed.get("signResult") or signed.get("signature") or signed.get("__NS_hxfalcon")
    caver = signed.get("caver") or "2"
    query = urllib.parse.urlencode({"taskId": task_id, "__NS_hxfalcon": sig, "caver": str(caver)})
    return f"https://kling.ai/api/task/status?{query}"


def _extract_download_url(payload: dict[str, Any]) -> str | None:
    works = payload.get("data", {}).get("works") if isinstance(payload.get("data"), dict) else None
    if isinstance(works, list):
        for work in works:
            if not isinstance(work, dict):
                continue
            resource = work.get("resource")
            if isinstance(resource, dict):
                candidate = resource.get("resource")
                if candidate:
                    value = str(candidate)
                    if value.startswith("//"):
                        return f"https:{value}"
                    if value.startswith("/"):
                        return f"https://kling.ai{value}"
                    return value
    value = _first_found(
        payload,
        (
            "download_url",
            "downloadUrl",
            "video_url",
            "videoUrl",
            "url",
            "file_url",
            "fileUrl",
            "resourceUrl",
            "src",
        ),
    )
    if value in {None, ""}:
        return None
    value = str(value)
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"https://kling.ai{value}"
    return value


def _generate_asset(
    *,
    prompt: str,
    output_dir: str | Path,
    payload_template: RuntimeSubmitPayload,
    output_filename: str,
    metadata_filename: str,
) -> Path:
    artifacts = _collect_artifacts()
    state = _status_payload(artifacts)
    if not (state["runtime_cookie_ready"] and state["web_contract"].get("signature_query_param")):
        raise SessionBridgeNotReadyError(
            "Kling session bridge is not ready yet. "
            f"Cookie ready={state['runtime_cookie_ready']}."
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    request_body = _override_prompt_in_payload(payload_template.payload, prompt)
    signed = sign_submit_payload(request_body=request_body, query={})
    sig4_value = signed.get("signature") or signed.get("__NS_hxfalcon") or signed.get("signResult")
    caver = signed.get("caver") or _first_found(signed.get("payload", {}), ("caver",)) or "2"
    if not sig4_value:
        raise RuntimeError(f"Kling session bridge signing failed: {signed}")

    submit_query = urllib.parse.urlencode({"__NS_hxfalcon": sig4_value, "caver": str(caver)})
    submit_url = f"https://kling.ai/api/task/submit?{submit_query}"
    submit_headers = _request_headers(artifacts)
    created = _json_request(submit_url, method="POST", headers=submit_headers, payload=request_body)
    task_id = _extract_task_id(created)
    if not task_id:
        raise RuntimeError(f"Kling web submit response did not contain a recognizable task id: {created}")

    deadline = time.time() + 1800
    status_payload = created
    status_value = _status_value(status_payload)
    status_headers = _request_headers(artifacts, include_content_type=False)
    download_url = _extract_download_url(status_payload)
    while not _status_done(status_value) and not download_url:
        if _status_error(status_value):
            raise RuntimeError(f"Kling web task failed with status={status_value}: {status_payload}")
        if time.time() > deadline:
            raise TimeoutError(f"Kling web task {task_id} exceeded timeout.")
        time.sleep(8)
        status_payload = _json_request(_build_status_url(task_id), method="GET", headers=status_headers)
        status_value = _status_value(status_payload)
        download_url = _extract_download_url(status_payload)

    if not download_url:
        raise RuntimeError(f"Kling web task completed but no download URL was present: {status_payload}")

    saved_asset = _download(download_url, output_dir / output_filename, headers=status_headers)
    metadata = {
        "provider": "kling_web_session_bridge",
        "prompt": prompt,
        "output_asset": str(saved_asset),
        "task_id": task_id,
        "submit_response": created,
        "status_payload": status_payload,
        "signed_submit": signed,
    }
    (output_dir / metadata_filename).write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return saved_asset


def generate(*, prompt: str, output_dir: str | Path) -> Path:
    artifacts = _collect_artifacts()
    state = _status_payload(artifacts)
    if not state["ready"]:
        raise SessionBridgeNotReadyError(
            "Kling session bridge is not ready yet. "
            f"Cookie ready={state['runtime_cookie_ready']} payload ready={state['runtime_submit_payload_ready']}."
        )

    return _generate_asset(
        prompt=prompt,
        output_dir=output_dir,
        payload_template=artifacts.runtime_submit_payload,
        output_filename="kling_session_bridge.mp4",
        metadata_filename="kling_session_bridge_metadata.json",
    )


def generate_image(*, prompt: str, output_dir: str | Path, quality: str = "studio") -> Path:
    artifacts = _collect_artifacts()
    state = status_image()
    if not state["ready"] or not artifacts.runtime_image_submit_payload:
        raise SessionBridgeNotReadyError(
            "Kling image bridge is not ready yet. "
            f"Cookie ready={state['runtime_cookie_ready']} payload ready={state['runtime_image_submit_payload_ready']}."
        )
    tuned_payload = RuntimeSubmitPayload(
        payload=_override_image_quality(artifacts.runtime_image_submit_payload.payload, quality)
    )
    return _generate_asset(
        prompt=prompt,
        output_dir=output_dir,
        payload_template=tuned_payload,
        output_filename="kling_image_bridge.png",
        metadata_filename="kling_image_bridge_metadata.json",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the Atlas-backed Kling web session bridge.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON status.")
    args = parser.parse_args()
    payload = status()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"mode: {payload['mode']}")
        print(f"ready: {payload['ready']}")
        print(f"profile_dir: {payload['profile_dir']}")
        print(f"cookie_names: {', '.join(payload['cookie_names']) or '-'}")
        print(f"indexeddb_markers: {', '.join(payload['indexeddb_markers']) or '-'}")


if __name__ == "__main__":
    main()
