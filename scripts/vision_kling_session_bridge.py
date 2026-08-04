from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
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
VOLATILE_COOKIE_NAMES = frozenset({"kwfv1", "kwssectoken", "kwscode"})
VOLATILE_REQUEST_HEADER_NAMES = frozenset({"kww", "ktrace-context"})
AUTH_PROBE_TTL_SECONDS = 45.0
AUTH_PROBE_WAIT_SECONDS = 150.0
SIG4_SIGN_TIMEOUT_SECONDS = 60.0
AUTH_PROBE_LOCK = threading.Lock()
AUTH_PROBE_CONDITION = threading.Condition(AUTH_PROBE_LOCK)
AUTH_PROBE_CACHE: dict[str, Any] = {
    "fingerprint": "",
    "checked_monotonic": 0.0,
    "result": None,
    "in_flight": False,
}

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


class SessionBridgeAuthenticationError(SessionBridgeNotReadyError):
    """Raised when Kling rejects the configured web session."""


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
    image_configured = (
        bool(payload.get("runtime_cookie_ready"))
        and bool(payload.get("web_contract", {}).get("signature_query_param"))
        and bool(payload.get("runtime_image_submit_payload_ready"))
    )
    auth_probe = (
        _probe_image_auth(artifacts)
        if image_configured
        else {
            "state": "not_configured",
            "checked_at": None,
            "message": "Kling image bridge configuration is incomplete.",
        }
    )
    image_ready = image_configured and auth_probe.get("state") == "authenticated"
    missing: list[str] = []
    if not payload.get("runtime_cookie_ready"):
        missing.append("runtime cookie header")
    if not payload.get("runtime_image_submit_payload_ready"):
        missing.append("real image submit payload")
    if image_ready:
        message = "Kling image bridge is authenticated and ready with the 2K unlimited contract."
    elif image_configured:
        message = str(auth_probe.get("message") or "Kling image authentication is unavailable.")
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
        "auth_state": auth_probe.get("state"),
        "auth_checked_at": auth_probe.get("checked_at"),
        "image_contract": {
            "resolution": "2k",
            "show_price": 0,
            "unlimited": True,
        },
    }


def prepare() -> dict[str, Any]:
    return status()


def sign_request_payload(
    *,
    path: str,
    request_body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "url": path,
        "query": query or {},
        # Kling's Sig4 interceptor signs GET requests with a JSON null body.
        # An empty object produces a different signature for query-bearing
        # endpoints such as /api/upload/issue/token.
        "requestBody": request_body,
    }
    proc = subprocess.run(
        ["node", str(SIG4_RUNTIME_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=SIG4_SIGN_TIMEOUT_SECONDS,
    )
    return json.loads(proc.stdout)


def sign_submit_payload(*, request_body: dict[str, Any], query: dict[str, Any] | None = None) -> dict[str, Any]:
    return sign_request_payload(path="/api/task/submit", request_body=request_body, query=query)


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
    headers: dict[str, str] = {}
    for name, value in (artifacts.runtime_request_headers.headers if artifacts.runtime_request_headers else {}).items():
        normalized_name = name.lower()
        if normalized_name in VOLATILE_REQUEST_HEADER_NAMES or normalized_name == "cookie":
            continue
        headers[normalized_name] = value
    if include_content_type:
        headers.setdefault("content-type", "application/json")
    else:
        headers.pop("content-type", None)
    if artifacts.runtime_cookie_header:
        stable_cookies = {
            name: value
            for name, value in artifacts.runtime_cookie_header.cookies.items()
            if name not in VOLATILE_COOKIE_NAMES
        }
        headers["cookie"] = "; ".join(f"{name}={value}" for name, value in stable_cookies.items())
    return headers


def _json_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: float = 300,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            if exc.code == 401:
                raise SessionBridgeAuthenticationError(
                    "The Kling connection is no longer authenticated. Renew the Kling web session before generating."
                ) from exc
            raise RuntimeError(f"Kling session bridge HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, ConnectionResetError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Kling session bridge network error after retries: {last_error}") from last_error


def _signed_get(
    artifacts: BridgeArtifacts,
    path: str,
    query: dict[str, Any] | None = None,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    request_query = query or {}
    signed = sign_request_payload(path=path, query=request_query)
    signature = signed.get("signature") or signed.get("__NS_hxfalcon") or signed.get("signResult")
    caver = signed.get("caver") or "2"
    if not signature:
        raise RuntimeError("Kling request signing failed.")
    encoded_query = urllib.parse.urlencode(
        {
            "__NS_hxfalcon": signature,
            "caver": str(caver),
            **request_query,
        }
    )
    return _json_request(
        f"https://kling.ai{path}?{encoded_query}",
        method="GET",
        headers=_request_headers(artifacts, include_content_type=False),
        timeout=timeout,
    )


def _upload_endpoint_url(endpoint: str, path: str, query: dict[str, Any]) -> str:
    normalized_endpoint = str(endpoint or "").strip().rstrip("/")
    if not normalized_endpoint:
        raise RuntimeError("Kling did not provide a reference upload endpoint.")
    if not normalized_endpoint.startswith(("http://", "https://")):
        normalized_endpoint = f"https://{normalized_endpoint}"
    return f"{normalized_endpoint}{path}?{urllib.parse.urlencode(query)}"


def _upload_json_request(
    url: str,
    *,
    method: str,
    payload: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 300,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=payload, headers=headers or {}, method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # Never echo the upload URL: it contains the temporary upload token.
        raise RuntimeError(f"Kling reference upload HTTP {exc.code}.") from exc
    except (urllib.error.URLError, ConnectionResetError) as exc:
        raise RuntimeError("Kling reference upload network error.") from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Kling reference upload returned an invalid response.") from exc
    return value if isinstance(value, dict) else {}


def _upload_reference_chunks(endpoint: str, upload_token: str, file_bytes: bytes) -> None:
    file_size = len(file_bytes)
    whole_digest = hashlib.md5(file_bytes).hexdigest()
    resume_payload = _upload_json_request(
        _upload_endpoint_url(
            endpoint,
            "/api/upload/resume",
            {"upload_token": upload_token, "content_md5": whole_digest},
        ),
        method="GET",
        headers={"X-File-MD5": whole_digest},
    )
    resume_data = resume_payload.get("data") if isinstance(resume_payload.get("data"), dict) else {}
    if bool(resume_data.get("existed")):
        return

    existing_fragments: set[int] = set()
    fragment_list = resume_data.get("fragment_list") or resume_data.get("fragmentList") or []
    if isinstance(fragment_list, list):
        for item in fragment_list:
            fragment_id = item.get("id") if isinstance(item, dict) else item
            try:
                existing_fragments.add(int(fragment_id))
            except (TypeError, ValueError):
                continue

    mebibyte = 1024 * 1024
    chunk_size = max(((file_size + mebibyte * 10_000 - 1) // (mebibyte * 10_000)) * mebibyte, mebibyte)
    chunk_size = min(chunk_size, 200 * mebibyte)
    fragment_count = (file_size + chunk_size - 1) // chunk_size
    for fragment_id in range(fragment_count):
        if fragment_id in existing_fragments:
            continue
        start = fragment_id * chunk_size
        end = min(start + chunk_size, file_size)
        fragment = file_bytes[start:end]
        fragment_digest = hashlib.md5(fragment).hexdigest()
        _upload_json_request(
            _upload_endpoint_url(
                endpoint,
                "/api/upload/fragment",
                {
                    "upload_token": upload_token,
                    "fragment_id": str(fragment_id),
                    "content_md5": fragment_digest,
                },
            ),
            method="POST",
            payload=fragment,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{end - 1}/{file_size}",
                "X-File-MD5": fragment_digest,
            },
        )

    _upload_json_request(
        _upload_endpoint_url(
            endpoint,
            "/api/upload/complete",
            {"fragment_count": str(fragment_count), "upload_token": upload_token},
        ),
        method="POST",
        payload=b"",
    )


def _upload_image_reference(artifacts: BridgeArtifacts, image_path: Path) -> dict[str, Any]:
    resolved_path = image_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(resolved_path)
    file_bytes = resolved_path.read_bytes()
    if not file_bytes:
        raise RuntimeError("The reference image is empty.")

    suffix = resolved_path.suffix.lower() if resolved_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    issued = _signed_get(
        artifacts,
        "/api/upload/issue/token",
        {"filename": f"vision-reference{suffix}"},
    )
    issued_data = issued.get("data") if isinstance(issued.get("data"), dict) else {}
    upload_token = str(issued_data.get("token") or "")
    endpoints = issued_data.get("httpEndpoints") or issued_data.get("http_endpoints") or []
    if not upload_token or not isinstance(endpoints, list) or not endpoints:
        raise RuntimeError("Kling did not open a reference upload lane.")

    last_error: Exception | None = None
    for endpoint in endpoints:
        try:
            _upload_reference_chunks(str(endpoint), upload_token, file_bytes)
            last_error = None
            break
        except RuntimeError as exc:
            last_error = exc
    if last_error is not None:
        raise RuntimeError("Kling could not receive the reference image.") from last_error

    verified = _signed_get(
        artifacts,
        "/api/upload/verify/token",
        {"token": upload_token, "type": "image"},
    )
    verified_data = verified.get("data") if isinstance(verified.get("data"), dict) else {}
    reference_url = str(verified_data.get("url") or "")
    upload_asset_id = verified_data.get("uploadAssetId") or verified_data.get("upload_asset_id")
    if not reference_url or upload_asset_id in {None, ""}:
        raise RuntimeError("Kling could not verify the reference image.")
    return {
        "name": "image_1",
        "inputType": "URL",
        "url": reference_url,
        "fromUploadId": upload_asset_id,
    }


def _probe_image_auth(artifacts: BridgeArtifacts) -> dict[str, Any]:
    headers = _request_headers(artifacts, include_content_type=False)
    cookie_header = str(headers.get("cookie") or "")
    fingerprint = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
    with AUTH_PROBE_CONDITION:
        while True:
            now_monotonic = time.monotonic()
            cached = AUTH_PROBE_CACHE.get("result")
            if (
                cached
                and AUTH_PROBE_CACHE.get("fingerprint") == fingerprint
                and now_monotonic - float(AUTH_PROBE_CACHE.get("checked_monotonic") or 0.0) < AUTH_PROBE_TTL_SECONDS
            ):
                return dict(cached)
            if not AUTH_PROBE_CACHE.get("in_flight"):
                AUTH_PROBE_CACHE["in_flight"] = True
                break
            if not AUTH_PROBE_CONDITION.wait(timeout=AUTH_PROBE_WAIT_SECONDS):
                return {
                    "state": "unavailable",
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "message": "Vision could not verify the Kling connection right now.",
                }

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result: dict[str, Any] | None = None
    try:
        signed = sign_request_payload(path="/api/user/info")
        signature = signed.get("signResult") or signed.get("signature") or signed.get("__NS_hxfalcon")
        caver = signed.get("caver") or "2"
        if not signature:
            raise RuntimeError("Kling authentication probe signing failed.")
        query = urllib.parse.urlencode({"__NS_hxfalcon": signature, "caver": str(caver)})
        response = _json_request(
            f"https://kling.ai/api/user/info?{query}",
            method="GET",
            headers=headers,
            timeout=20,
        )
        authenticated = (
            response.get("status") == 200
            and response.get("result") == 1
            and isinstance(response.get("data"), dict)
        )
        result = {
            "state": "authenticated" if authenticated else "invalid",
            "checked_at": checked_at,
            "message": (
                "Kling web session is authenticated."
                if authenticated
                else "The Kling connection must be renewed before image generation can continue."
            ),
        }
    except SessionBridgeAuthenticationError:
        result = {
            "state": "invalid",
            "checked_at": checked_at,
            "message": "The Kling connection must be renewed before image generation can continue.",
        }
    except Exception:
        result = {
            "state": "unavailable",
            "checked_at": checked_at,
            "message": "Vision could not verify the Kling connection right now.",
        }
    finally:
        with AUTH_PROBE_CONDITION:
            if result is not None:
                AUTH_PROBE_CACHE.update(
                    fingerprint=fingerprint,
                    checked_monotonic=time.monotonic(),
                    result=dict(result),
                )
            AUTH_PROBE_CACHE["in_flight"] = False
            AUTH_PROBE_CONDITION.notify_all()
    if result is None:
        raise RuntimeError("Kling authentication probe did not produce a result.")
    return result


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


def _override_prompt_in_payload(
    template: dict[str, Any],
    prompt: str,
    *,
    rich_prompt: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(template))
    arguments = payload.get("arguments", [])
    if isinstance(arguments, list):
        for item in arguments:
            if not isinstance(item, dict):
                continue
            if item.get("name") == "prompt":
                item["value"] = prompt
            elif item.get("name") == "rich_prompt":
                item["value"] = rich_prompt if rich_prompt is not None else prompt
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
    matched = False
    for item in arguments:
        if not isinstance(item, dict):
            continue
        if item.get("name") == name:
            item["value"] = value
            if set_by_user is not None:
                item["setByUser"] = set_by_user
            matched = True
    if matched:
        return
    new_item: dict[str, Any] = {"name": name, "value": value}
    if set_by_user is not None:
        new_item["setByUser"] = set_by_user
    arguments.append(new_item)


def _image_quality_settings(quality: str) -> dict[str, Any]:
    del quality
    return {
        "resolution": "2k",
        "show_price": 0,
        "unlimited": True,
    }


def _normalize_image_aspect_ratio(value: str | None) -> str:
    normalized = str(value or "16:9").strip().lower().replace(" ", "")
    return normalized if normalized in {"1:1", "16:9", "9:16", "4:5", "3:4", "4:3", "3:2"} else "16:9"


def _kling_image_aspect_ratio(value: str | None) -> str:
    normalized = _normalize_image_aspect_ratio(value)
    return {
        "4:5": "3:4",
        "3:2": "4:3",
    }.get(normalized, normalized)


def _override_image_quality(
    payload: dict[str, Any],
    quality: str,
    aspect_ratio: str | None = "16:9",
    *,
    reference_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tuned = json.loads(json.dumps(payload))
    settings = _image_quality_settings(quality)
    if settings != {"resolution": "2k", "show_price": 0, "unlimited": True}:
        raise RuntimeError("Kling image generation must use the 2K unlimited contract.")
    if reference_input is not None:
        tuned["inputs"] = [json.loads(json.dumps(reference_input))]
    elif _env_bool("VISION_KLING_IMAGE_TEXT_ONLY", True):
        tuned["inputs"] = []
    _set_argument_value(tuned, "img_resolution", settings["resolution"], set_by_user=True)
    _set_argument_value(tuned, "imageCount", "1", set_by_user=True)
    _set_argument_value(tuned, "story_mode", False)
    _set_argument_value(tuned, "showPrice", settings["show_price"])
    _set_argument_value(tuned, "__isUnLimited", settings["unlimited"])
    _set_argument_value(tuned, "aspect_ratio", _kling_image_aspect_ratio(aspect_ratio), set_by_user=True)

    required_arguments = {
        "img_resolution": "2k",
        "imageCount": "1",
        "showPrice": 0,
        "__isUnLimited": True,
    }
    arguments = tuned.get("arguments")
    if not isinstance(arguments, list):
        raise RuntimeError("Kling image payload is missing its arguments list.")
    for name, expected in required_arguments.items():
        values = [item.get("value") for item in arguments if isinstance(item, dict) and item.get("name") == name]
        if not values or any(value != expected for value in values):
            raise RuntimeError(f"Kling image payload violates the 2K unlimited contract for {name}.")
    return tuned


def _extract_task_id(payload: dict[str, Any]) -> str | None:
    value = _first_found(payload, ("task_id", "taskId", "id", "job_id", "creativeId"))
    return str(value) if value not in {None, ""} else None


def _build_status_url(task_id: str) -> str:
    signed = sign_request_payload(path="/api/task/status", query={"taskId": task_id})
    sig = signed.get("signResult") or signed.get("signature") or signed.get("__NS_hxfalcon")
    caver = signed.get("caver") or "2"
    query = urllib.parse.urlencode({"taskId": task_id, "__NS_hxfalcon": sig, "caver": str(caver)})
    return f"https://kling.ai/api/task/status?{query}"


def _extract_download_url(
    payload: dict[str, Any],
    *,
    allow_generic_fallback: bool = True,
) -> str | None:
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
    if not allow_generic_fallback:
        # Reference-image jobs echo their uploaded source under taskInfo.inputs.
        # Treating that generic URL as a finished output makes the bridge return
        # the source image before Kling has generated anything. For these jobs,
        # only data.works[].resource is an acceptable result.
        return None
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
    rich_prompt: str | None = None,
    reference_used: bool = False,
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
    request_body = _override_prompt_in_payload(
        payload_template.payload,
        prompt,
        rich_prompt=rich_prompt,
    )
    signed = sign_submit_payload(request_body=request_body, query={})
    sig4_value = signed.get("signature") or signed.get("__NS_hxfalcon") or signed.get("signResult")
    caver = signed.get("caver") or _first_found(signed.get("payload", {}), ("caver",)) or "2"
    if not sig4_value:
        if reference_used:
            raise RuntimeError("Kling reference image signing failed.")
        raise RuntimeError(f"Kling session bridge signing failed: {signed}")

    submit_query = urllib.parse.urlencode({"__NS_hxfalcon": sig4_value, "caver": str(caver)})
    submit_url = f"https://kling.ai/api/task/submit?{submit_query}"
    submit_headers = _request_headers(artifacts)
    try:
        created = _json_request(submit_url, method="POST", headers=submit_headers, payload=request_body)
    except SessionBridgeAuthenticationError:
        raise
    except RuntimeError:
        if reference_used:
            raise RuntimeError("Kling could not start the reference image generation.") from None
        raise
    task_id = _extract_task_id(created)
    if not task_id:
        if reference_used:
            raise RuntimeError("Kling did not return a task for the reference image generation.")
        raise RuntimeError(f"Kling web submit response did not contain a recognizable task id: {created}")

    deadline = time.time() + 1800
    status_payload = created
    status_value = _status_value(status_payload)
    status_headers = _request_headers(artifacts, include_content_type=False)
    download_url = _extract_download_url(
        status_payload,
        allow_generic_fallback=not reference_used,
    )
    while not _status_done(status_value) and not download_url:
        if _status_error(status_value):
            if reference_used:
                raise RuntimeError(f"Kling reference image generation failed with status={status_value}.")
            raise RuntimeError(f"Kling web task failed with status={status_value}: {status_payload}")
        if time.time() > deadline:
            raise TimeoutError(f"Kling web task {task_id} exceeded timeout.")
        time.sleep(8)
        try:
            status_payload = _json_request(_build_status_url(task_id), method="GET", headers=status_headers)
        except SessionBridgeAuthenticationError:
            raise
        except RuntimeError:
            if reference_used:
                raise RuntimeError("Kling could not read the reference image generation status.") from None
            raise
        status_value = _status_value(status_payload)
        download_url = _extract_download_url(
            status_payload,
            allow_generic_fallback=not reference_used,
        )

    if not download_url:
        if reference_used:
            raise RuntimeError("Kling completed the reference image generation without an output image.")
        raise RuntimeError(f"Kling web task completed but no download URL was present: {status_payload}")

    saved_asset = _download(download_url, output_dir / output_filename, headers=status_headers)
    metadata: dict[str, Any] = {
        "provider": "kling_web_session_bridge",
        "prompt": prompt,
        "output_asset": str(saved_asset),
        "task_id": task_id,
    }
    if reference_used:
        # Reference upload URLs and asset identifiers are short-lived private
        # material. Generated output metadata can be served publicly, so keep
        # only the non-sensitive task summary for image-to-image requests.
        metadata.update(
            reference_image=True,
            submit_status=_status_value(created),
            final_status=status_value,
        )
    else:
        metadata.update(
            submit_response=created,
            status_payload=status_payload,
            signed_submit=signed,
        )
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


def generate_image(
    *,
    prompt: str,
    output_dir: str | Path,
    quality: str = "studio",
    aspect_ratio: str = "16:9",
    image_path: str | Path | None = None,
) -> Path:
    artifacts = _collect_artifacts()
    state = status_image()
    if not state["ready"] or not artifacts.runtime_image_submit_payload:
        raise SessionBridgeNotReadyError(
            "Kling image bridge is not ready yet. "
            f"Cookie ready={state['runtime_cookie_ready']} payload ready={state['runtime_image_submit_payload_ready']}."
        )
    reference_input: dict[str, Any] | None = None
    rich_prompt: str | None = None
    if image_path is not None:
        reference_input = _upload_image_reference(artifacts, Path(image_path))
        rich_prompt = f"<<<image_1>>> {prompt}".strip()

    tuned_payload = RuntimeSubmitPayload(
        payload=_override_image_quality(
            artifacts.runtime_image_submit_payload.payload,
            quality,
            aspect_ratio,
            reference_input=reference_input,
        )
    )
    return _generate_asset(
        prompt=prompt,
        output_dir=output_dir,
        payload_template=tuned_payload,
        output_filename="kling_image_bridge.png",
        metadata_filename="kling_image_bridge_metadata.json",
        rich_prompt=rich_prompt,
        reference_used=reference_input is not None,
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
