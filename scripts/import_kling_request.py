from __future__ import annotations

import argparse
import codecs
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".runtime"
COOKIE_HEADER_FILE = RUNTIME_ROOT / "kling_cookie_header.txt"
REQUEST_HEADERS_FILE = RUNTIME_ROOT / "kling_request_headers.json"
VIDEO_PAYLOAD_FILE = RUNTIME_ROOT / "kling_submit_payload.sample.json"
IMAGE_PAYLOAD_FILE = RUNTIME_ROOT / "kling_image_submit_payload.sample.json"

HEADER_FLAGS = {"-H", "--header"}
DATA_FLAGS = {"--data", "--data-raw", "--data-binary", "--data-ascii"}


def _read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_curl(raw: str) -> tuple[str | None, dict[str, str], str | None]:
    stripped = raw.strip()
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return _parse_curl_fallback(stripped)

    if not tokens or tokens[0] != "curl":
        raise ValueError("Input is not a curl command.")

    url: str | None = None
    headers: dict[str, str] = {}
    body: str | None = None
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token in HEADER_FLAGS and i + 1 < len(tokens):
            header = tokens[i + 1]
            if ":" in header:
                name, value = header.split(":", 1)
                headers[name.strip().lower()] = value.strip()
            i += 2
            continue
        if token in DATA_FLAGS and i + 1 < len(tokens):
            body = tokens[i + 1]
            i += 2
            continue
        if token.startswith("http://") or token.startswith("https://"):
            url = token
        i += 1
    return url, headers, body


def _decode_dollar_single_quoted(value: str) -> str:
    return codecs.decode(value, "unicode_escape")


def _extract_single_quoted_segment(raw: str, start_index: int) -> tuple[str, int]:
    if raw[start_index] != "'":
        raise ValueError("Expected single quote.")
    i = start_index + 1
    out: list[str] = []
    while i < len(raw):
        ch = raw[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch == "\\" and i + 1 < len(raw):
            out.append(raw[i])
            out.append(raw[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    raise ValueError("Unterminated single-quoted segment.")


def _parse_curl_fallback(raw: str) -> tuple[str | None, dict[str, str], str | None]:
    if not raw.startswith("curl "):
        raise ValueError("Input is not a curl command.")

    url_match = re.search(r"curl\s+'([^']+)'", raw)
    url = url_match.group(1) if url_match else None

    headers: dict[str, str] = {}
    for match in re.finditer(r"(?:-H|--header)\s+'([^']+)'", raw):
        header = match.group(1)
        if ":" in header:
            name, value = header.split(":", 1)
            headers[name.strip().lower()] = value.strip()

    for match in re.finditer(r"(?:-b|--cookie)\s+'([^']+)'", raw):
        headers["cookie"] = match.group(1)

    body: str | None = None
    body_match = re.search(r"(--data(?:-raw|-binary|-ascii)?)[ \t]+(\$)?'", raw)
    if body_match:
        quote_start = body_match.end() - 1
        content, _ = _extract_single_quoted_segment(raw, quote_start)
        body = _decode_dollar_single_quoted(content) if body_match.group(2) else content

    return url, headers, body


def _clean_headers(headers: dict[str, str]) -> tuple[str | None, dict[str, str]]:
    cookie_header = headers.get("cookie")
    cleaned: dict[str, str] = {}
    excluded = {
        "cookie",
        "content-length",
        "host",
        "authority",
        "x-request-id",
        "priority",
    }
    for key, value in headers.items():
        if key in excluded:
            continue
        cleaned[key] = value
    return cookie_header, cleaned


def _parse_body(raw_body: str | None) -> dict[str, Any]:
    if not raw_body:
        raise ValueError("Curl request does not contain a JSON body.")
    body = _strip_quotes(raw_body)
    return json.loads(body)


def _detect_kind(payload: dict[str, Any], explicit_kind: str | None) -> str:
    if explicit_kind in {"image", "video"}:
        return explicit_kind
    payload_type = str(payload.get("type", "")).lower()
    if any(marker in payload_type for marker in ("txt2img", "img2img", "image")):
        return "image"
    if "video" in payload_type:
        return "video"
    arguments = payload.get("arguments", [])
    if isinstance(arguments, list):
        names = {str(item.get("name", "")).lower() for item in arguments if isinstance(item, dict)}
        if {"prompt", "rich_prompt"} <= names and "image" in payload_type:
            return "image"
    raise ValueError("Could not detect whether this Kling request is image or video.")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.strip() + "\n", encoding="utf-8")


def _env_line(key: str, value: str) -> str:
    return f"{key}={value}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a real Kling submit request into Vision bridge runtime files.")
    parser.add_argument("--input", help="Path to a text file containing a curl command. Reads stdin if omitted.")
    parser.add_argument("--kind", choices=["image", "video"], help="Force the request kind.")
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="Print Render-ready environment variable lines after import.",
    )
    args = parser.parse_args()

    raw = _read_input(args.input).strip()
    url, headers, raw_body = _parse_curl(raw)
    payload = _parse_body(raw_body)
    kind = _detect_kind(payload, args.kind)
    cookie_header, request_headers = _clean_headers(headers)

    if not cookie_header:
        raise SystemExit("Missing Cookie header in the curl request.")

    _write_text(COOKIE_HEADER_FILE, cookie_header)
    _write_json(REQUEST_HEADERS_FILE, request_headers)

    target_file = IMAGE_PAYLOAD_FILE if kind == "image" else VIDEO_PAYLOAD_FILE
    _write_json(target_file, payload)

    print(f"Imported Kling {kind} request.")
    if url:
        print(f"Submit URL: {url}")
    print(f"Cookie header written to: {COOKIE_HEADER_FILE}")
    print(f"Request headers written to: {REQUEST_HEADERS_FILE}")
    print(f"Payload written to: {target_file}")

    if args.print_env:
        print()
        print("Render env values:")
        print(_env_line("VISION_KLING_COOKIE_HEADER", cookie_header))
        print(_env_line("VISION_KLING_REQUEST_HEADERS_JSON", json.dumps(request_headers, separators=(",", ":"))))
        env_key = "VISION_KLING_IMAGE_SUBMIT_PAYLOAD_JSON" if kind == "image" else "VISION_KLING_SUBMIT_PAYLOAD_JSON"
        print(_env_line(env_key, json.dumps(payload, separators=(",", ":"))))


if __name__ == "__main__":
    main()
