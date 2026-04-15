from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

DEFAULT_IMAGE_MODEL = "imagen-4.0-generate-001"
DEFAULT_IMAGE_FALLBACK_MODELS = ("imagen-4.0-fast-generate-001",)


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


def _save_generated_image(response: Any, output_image: Path) -> tuple[Path, str]:
    generated_images = getattr(response, "generated_images", None)
    if generated_images is None and isinstance(response, dict):
        generated_images = response.get("generated_images") or response.get("generatedImages")
    if not generated_images:
        raise RuntimeError("Google image response did not contain a generated image.")

    first = generated_images[0]
    image = getattr(first, "image", None)
    if image is None and isinstance(first, dict):
        image = first.get("image")
    if image is None:
        raise RuntimeError("Google image response did not include image bytes.")

    image_bytes = getattr(image, "image_bytes", None)
    mime_type = getattr(image, "mime_type", None)
    if isinstance(image, dict):
        image_bytes = image.get("image_bytes") or image.get("imageBytes") or image_bytes
        mime_type = image.get("mime_type") or image.get("mimeType") or mime_type
    if not image_bytes:
        raise RuntimeError("Google image response did not include image bytes.")

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(bytes(image_bytes))
    return output_image, str(mime_type or "image/png")


def _default_primary_model() -> str:
    return os.getenv("GOOGLE_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def _parse_fallback_models(raw: str | None) -> list[str]:
    configured = raw if raw is not None else os.getenv("GOOGLE_IMAGE_FALLBACK_MODELS", "")
    values = [value.strip() for value in str(configured or "").split(",") if value.strip()]
    return values or list(DEFAULT_IMAGE_FALLBACK_MODELS)


def _candidate_models(primary: str | None, fallback_models: str | None = None) -> list[str]:
    models: list[str] = []
    for candidate in [primary or _default_primary_model(), *_parse_fallback_models(fallback_models)]:
        if candidate and candidate not in models:
            models.append(candidate)
    return models


def status() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    model = _default_primary_model()
    return {
        "ready": bool(api_key and model),
        "mode": "google",
        "has_api_key": bool(api_key),
        "model": model or None,
        "fallback_models": ",".join(_parse_fallback_models(None)),
    }


def generate_image(
    *,
    prompt: str,
    output_dir: str | Path,
    output_image: str | Path | None = None,
    model: str | None = None,
    fallback_models: str | None = None,
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
    normalized_inputs = [Path(value).expanduser().resolve() for value in (input_images or [])]
    candidate_models = _candidate_models(model, fallback_models)
    failures: list[str] = []

    for resolved_model in candidate_models:
        try:
            if normalized_inputs:
                config = types.GenerateContentConfig(response_modalities=["IMAGE"])
                contents: list[Any] = []
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
                response = client.models.generate_content(
                    model=resolved_model,
                    contents=contents,
                    config=config,
                )
                saved_image, mime_type = _save_first_image(response, output_image_path)
            else:
                response = client.models.generate_images(
                    model=resolved_model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        numberOfImages=1,
                        aspectRatio="16:9",
                        outputMimeType="image/png",
                        addWatermark=False,
                        enhancePrompt=True,
                    ),
                )
                saved_image, mime_type = _save_generated_image(response, output_image_path)

            metadata = {
                "provider": "google",
                "model": resolved_model,
                "prompt": prompt,
                "output_image": str(saved_image),
                "mime_type": mime_type,
                "input_images": [str(path) for path in normalized_inputs],
                "fallback_models": candidate_models[1:],
            }
            (output_dir / "google_image_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            return saved_image
        except Exception as exc:
            failures.append(f"{resolved_model}: {exc}")

    raise RuntimeError(" ; ".join(failures) if failures else "Google image generation failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an image with Gemini.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-image")
    parser.add_argument("--input-image", action="append", default=[])
    parser.add_argument("--model", default=_default_primary_model())
    parser.add_argument("--fallback-models", default=os.getenv("GOOGLE_IMAGE_FALLBACK_MODELS", ",".join(DEFAULT_IMAGE_FALLBACK_MODELS)))
    args = parser.parse_args()

    saved_image = generate_image(
        prompt=args.prompt,
        output_dir=args.output_dir,
        output_image=args.output_image,
        model=args.model,
        fallback_models=args.fallback_models,
        input_images=args.input_image,
    )
    print(saved_image)


if __name__ == "__main__":
    main()
