from __future__ import annotations

import json
import os
from typing import Any


def _resolve_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY to run prompt enhancement.")
    return api_key


def _default_model() -> str:
    return os.getenv("GOOGLE_PROMPT_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"


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


def _first_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    if isinstance(response, dict):
        raw_text = response.get("text")
        if isinstance(raw_text, str) and raw_text.strip():
            return raw_text.strip()
    for part in _iter_parts(response):
        value = getattr(part, "text", None)
        if value is None and isinstance(part, dict):
            value = part.get("text")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _looks_already_enhanced(prompt: str, mode: str) -> bool:
    lowered = prompt.strip().lower()
    prefixes = [
        "ultra-realistic cinematic shot of",
        "ultra realistic cinematic shot of",
        "ultra-realistic cinematic still of",
        "ultra realistic cinematic still of",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True
    if mode == "video" and "feature-film realism" in lowered:
        return True
    if mode == "image" and "clean visual hierarchy" in lowered:
        return True
    return False


def _dedupe_intro(prompt: str) -> str:
    cleaned = " ".join(prompt.strip().split())
    duplicate_pairs = [
        ("Ultra-realistic cinematic shot of Ultra-realistic cinematic shot of ", "Ultra-realistic cinematic shot of "),
        ("Ultra-realistic cinematic still of Ultra-realistic cinematic still of ", "Ultra-realistic cinematic still of "),
    ]
    for before, after in duplicate_pairs:
        while before in cleaned:
            cleaned = cleaned.replace(before, after)
    return cleaned


def _local_prompt(prompt: str, mode: str) -> dict[str, str | None]:
    normalized = "image" if mode == "image" else "video"
    cleaned = _dedupe_intro(prompt)
    if _looks_already_enhanced(cleaned, normalized):
        return {
            "improved_prompt": cleaned,
            "summary": "Prompt already sharpened by Vision.",
            "provider": "vision_local",
            "model": None,
        }
    if normalized == "image":
        improved = (
            f"Ultra-realistic cinematic still of {cleaned}, premium lighting, refined composition, detailed textures, "
            "natural atmosphere, clean visual hierarchy, elegant realism, no text, no watermark."
        )
        summary = "Enhanced by Vision for a sharper still image result."
    else:
        improved = (
            f"Ultra-realistic cinematic shot of {cleaned}, strong visual direction, natural motion, premium lighting, "
            "rich atmosphere, realistic textures, clean composition, feature-film realism, no text, no watermark."
        )
        summary = "Enhanced by Vision for stronger cinematic motion and realism."
    return {
        "improved_prompt": improved,
        "summary": summary,
        "provider": "vision_local",
        "model": None,
    }


def status() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    model = _default_model()
    return {
        "ready": True,
        "mode": "google",
        "has_api_key": bool(api_key),
        "model": model,
        "fallback": "vision_local",
    }


def improve_prompt(*, prompt: str, mode: str = "video", model: str | None = None) -> dict[str, str | None]:
    normalized_mode = "image" if mode == "image" else "video"
    cleaned_prompt = _dedupe_intro(prompt)
    if not cleaned_prompt:
        raise RuntimeError("Prompt enhancement needs a prompt first.")
    if _looks_already_enhanced(cleaned_prompt, normalized_mode):
        return {
            "improved_prompt": cleaned_prompt,
            "summary": "Prompt already sharpened by Vision.",
            "provider": "vision_local",
            "model": None,
        }

    try:
        from google import genai
        from google.genai import types
    except Exception:
        return _local_prompt(cleaned_prompt, normalized_mode)

    try:
        client = genai.Client(api_key=_resolve_api_key())
        config = types.GenerateContentConfig(
            temperature=0.9,
            response_mime_type="application/json",
        )
        instruction = f"""
You are the Vision engine.

Rewrite the user's raw {normalized_mode} idea into a premium prompt for cinematic generation.

Rules:
- Preserve the original subject and intent.
- Make the prompt feel richer, more precise, and more premium.
- Improve realism, atmosphere, lighting, framing, material detail, and visual tone.
- If mode is video, add natural motion and restrained camera direction.
- If mode is image, optimize for a powerful still frame.
- If the prompt already sounds premium, only tighten it lightly instead of rewriting the opening phrase.
- Never repeat the same cinematic intro twice.
- Never mention provider names, model names, aspect ratios, resolutions, credits, or pricing.
- Do not use markdown.
- Return strict JSON with keys: improved_prompt, summary.
- improved_prompt must stay under 420 characters.
- summary must stay under 90 characters.

Mode: {normalized_mode}
User prompt: {cleaned_prompt}
""".strip()
        response = client.models.generate_content(
            model=model or _default_model(),
            contents=instruction,
            config=config,
        )
        text = _first_text(response)
        if not text:
            return _local_prompt(cleaned_prompt, normalized_mode)
        payload = json.loads(text)
        improved_prompt = str(payload.get("improved_prompt") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        if not improved_prompt:
            return _local_prompt(cleaned_prompt, normalized_mode)
        return {
            "improved_prompt": improved_prompt,
            "summary": summary or "Enhanced by Vision.",
            "provider": "google",
            "model": model or _default_model(),
        }
    except Exception:
        return _local_prompt(cleaned_prompt, normalized_mode)
