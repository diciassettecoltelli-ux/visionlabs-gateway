from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
from typing import Any


def _resolve_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY to run Gemini image generation.")
    return api_key


def _guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(path))
    return mime_type or "image/png"


def _iter_parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None)
    if candidates:
        parts: list[Any] = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            candidate_parts = getattr(content, "parts", None)
            if candidate_parts:
                parts.extend(candidate_parts)
        if parts:
            return parts
    if isinstance(response, dict):
        parts = []
        for candidate in response.get("candidates", []):
            content = candidate.get("content", {})
            parts.extend(content.get("parts", []))
        return parts
    return []


def _inline_data_from_part(part: Any) -> tuple[bytes, str] | None:
    inline_data = getattr(part, "inline_data", None)
    if inline_data is None and isinstance(part, dict):
        inline_data = part.get("inline_data")
    if inline_data is None:
        return None
    data = getattr(inline_data, "data", None)
    mime_type = getattr(inline_data, "mime_type", None)
    if isinstance(inline_data, dict):
        data = inline_data.get("data", data)
        mime_type = inline_data.get("mime_type", mime_type)
    if not data:
        return None
    if isinstance(data, str):
        data = data.encode("latin1")
    return bytes(data), str(mime_type or "image/png")


def _save_first_image(response: Any, output_image: Path) -> tuple[Path, str]:
    for part in _iter_parts(response):
        payload = _inline_data_from_part(part)
        if payload is None:
            continue
        data, mime_type = payload
        output_image.parent.mkdir(parents=True, exist_ok=True)
        output_image.write_bytes(data)
        return output_image, mime_type
    raise RuntimeError("Gemini image response did not contain an inline image.")


def status() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    model = os.getenv("GOOGLE_IMAGE_MODEL", "gemini-3.1-flash-image-preview").strip()
    return {
        "ready": bool(api_key and model),
        "mode": "google",
        "has_api_key": bool(api_key),
        "model": model or None,
    }


def generate_image(
    *,
    prompt: str,
    output_dir: str | Path,
    output_image: str | Path | None = None,
    model: str | None = None,
    input_images: list[str | Path] | None = None,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_image_path = Path(output_image) if output_image else output_dir / "google_image.png"

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("google-genai is required for Gemini image runtime integration.") from exc

    client = genai.Client(api_key=_resolve_api_key())
    config = types.GenerateContentConfig(response_modalities=["IMAGE"])
    contents: list[Any] = []
    normalized_inputs = [Path(value).expanduser().resolve() for value in (input_images or [])]
    for image_path in normalized_inputs:
        if not image_path.exists():
            raise FileNotFoundError(f"Input image not found: {image_path}")
        contents.append(
            types.Part.from_bytes(
                data=image_path.read_bytes(),
                mime_type=_guess_mime_type(image_path),
            )
        )
    contents.append(prompt)
    resolved_model = model or os.getenv("GOOGLE_IMAGE_MODEL", "gemini-3.1-flash-image-preview")
    response = client.models.generate_content(
        model=resolved_model,
        contents=contents,
        config=config,
    )
    saved_image, mime_type = _save_first_image(response, output_image_path)
    metadata = {
        "provider": "google",
        "model": resolved_model,
        "prompt": prompt,
        "output_image": str(saved_image),
        "mime_type": mime_type,
        "input_images": [str(path) for path in normalized_inputs],
    }
    (output_dir / "google_image_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return saved_image


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an image with Gemini.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-image")
    parser.add_argument("--input-image", action="append", default=[])
    parser.add_argument("--model", default=os.getenv("GOOGLE_IMAGE_MODEL", "gemini-3.1-flash-image-preview"))
    args = parser.parse_args()

    saved_image = generate_image(
        prompt=args.prompt,
        output_dir=args.output_dir,
        output_image=args.output_image,
        model=args.model,
        input_images=args.input_image,
    )
    print(saved_image)


if __name__ == "__main__":
    main()
