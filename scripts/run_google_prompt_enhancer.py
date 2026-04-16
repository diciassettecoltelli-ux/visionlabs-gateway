from __future__ import annotations

import json
import os
import re
from typing import Any


def _resolve_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY to run prompt enhancement.")
    return api_key


def _default_model() -> str:
    return os.getenv("GOOGLE_PROMPT_MODEL", "gemini-2.5-pro").strip() or "gemini-2.5-pro"


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
        "ultra-realistic still frame of",
        "ultra realistic still frame of",
        "cinematic scene of",
        "cinematic still of",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True
    cinematic_cues = [
        "premium atmosphere",
        "premium lighting",
        "refined lighting",
        "realistic textures",
        "realistic material detail",
        "clean composition",
        "clean visual hierarchy",
        "natural motion",
        "controlled camera movement",
        "no text",
        "no watermark",
    ]
    score = sum(1 for cue in cinematic_cues if cue in lowered)
    if mode == "video" and ("feature-film realism" in lowered or score >= 4):
        return True
    if mode == "image" and ("clean visual hierarchy" in lowered or score >= 4):
        return True
    return False


def _collapse_duplicate_tokens(text: str) -> str:
    cleaned = text
    duplicate_patterns = [
        (r"(?i)\bshot of shot of\b", "shot of"),
        (r"(?i)\bstill of still of\b", "still of"),
        (r"(?i)\bimage of image of\b", "image of"),
        (r"(?i)\bcinematic cinematic\b", "cinematic"),
        (r"(?i)\brealistic realistic\b", "realistic"),
        (r"(?i)\bultra-realistic ultra-realistic\b", "ultra-realistic"),
        (r"(?i)\bultra realistic ultra realistic\b", "ultra realistic"),
        (r"(?i)\bof of\b", "of"),
    ]
    for pattern, replacement in duplicate_patterns:
        while re.search(pattern, cleaned):
            cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned


def _strip_leading_style(prompt: str) -> str:
    cleaned = prompt.strip()
    prefixes = [
        r"(?i)^ultra[- ]realistic cinematic shot of\s+",
        r"(?i)^ultra[- ]realistic cinematic still of\s+",
        r"(?i)^ultra[- ]realistic still frame of\s+",
        r"(?i)^cinematic shot of\s+",
        r"(?i)^cinematic still of\s+",
        r"(?i)^still frame of\s+",
        r"(?i)^shot of\s+",
        r"(?i)^image of\s+",
        r"(?i)^still of\s+",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in prefixes:
            stripped = re.sub(pattern, "", cleaned).strip(" ,.-")
            if stripped != cleaned:
                cleaned = stripped
                changed = True
    return cleaned


def _normalize_subject(prompt: str) -> str:
    cleaned = " ".join(prompt.strip().split())
    cleaned = _collapse_duplicate_tokens(cleaned)
    cleaned = _strip_leading_style(cleaned)
    style_segments = [
        "strong visual direction",
        "natural motion",
        "controlled camera movement",
        "premium lighting",
        "refined lighting",
        "rich atmosphere",
        "premium atmosphere",
        "feature-film realism",
        "clean composition",
        "clean visual hierarchy",
        "realistic textures",
        "realistic material detail",
        "detailed textures",
        "elegant realism",
    ]
    parts = [part.strip(" ,.-") for part in cleaned.split(",")]
    kept_parts = [part for part in parts if part and not any(cue in part.lower() for cue in style_segments)]
    if kept_parts:
        cleaned = ", ".join(kept_parts)
    cleaned = re.sub(r"(?i)\bultra[- ]?realistic\b", "", cleaned)
    cleaned = re.sub(r"(?i)\bcinematic\b", "", cleaned)
    cleaned = re.sub(r"(?i)\brealistic\b", "", cleaned)
    cleaned = re.sub(r"(?i)\b(no text,\s*)?no watermark\.?$", "", cleaned).strip(" ,.-")
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = _collapse_duplicate_tokens(cleaned)
    return cleaned.strip(" ,.-")


def _trim_prompt(text: str, limit: int = 420) -> str:
    if len(text) <= limit:
        return text
    trimmed = text[: limit - 3].rsplit(" ", 1)[0].rstrip(",.;:- ")
    return f"{trimmed}..."


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


def _prompt_profile(prompt: str) -> str:
    lowered = prompt.lower()
    human_cues = {
        "woman",
        "man",
        "girl",
        "boy",
        "person",
        "portrait",
        "face",
        "skin",
        "fashion",
        "editorial",
        "dress",
        "model",
        "beauty",
        "character",
    }
    architecture_cues = {
        "house",
        "villa",
        "interior",
        "room",
        "building",
        "architecture",
        "hotel",
        "bar",
        "restaurant",
        "desert house",
        "modern house",
        "apartment",
    }
    product_cues = {
        "perfume",
        "bottle",
        "jewelry",
        "watch",
        "product",
        "shoe",
        "bag",
    }
    environment_cues = {
        "landscape",
        "forest",
        "mountain",
        "ocean",
        "sea",
        "beach",
        "city",
        "street",
        "rain",
        "storm",
        "sunset",
        "sunrise",
    }
    motion_cues = {
        "walking",
        "running",
        "driving",
        "camera",
        "tracking",
        "orbit",
        "pan",
        "tilt",
        "drift",
        "handheld",
        "wind",
        "waves",
        "rain",
        "moving",
        "motion",
    }

    has_human = any(cue in lowered for cue in human_cues)
    has_architecture = any(cue in lowered for cue in architecture_cues)
    has_product = any(cue in lowered for cue in product_cues)
    has_environment = any(cue in lowered for cue in environment_cues)
    has_motion = any(cue in lowered for cue in motion_cues)

    if has_human:
        return "human_video" if has_motion else "human"
    if has_product:
        return "product"
    if has_architecture:
        return "architecture_video" if has_motion else "architecture"
    if has_environment:
        return "environment_video" if has_motion else "environment"
    return "general_video" if has_motion else "general"


def _local_prompt(prompt: str, mode: str) -> dict[str, str | None]:
    normalized = "image" if mode == "image" else "video"
    cleaned = _normalize_subject(_dedupe_intro(prompt))
    if not cleaned:
        cleaned = "a cinematic subject"
    if _looks_already_enhanced(cleaned, normalized):
        return {
            "improved_prompt": cleaned,
            "summary": "Prompt already sharpened by Vision.",
            "provider": "vision_local",
            "model": None,
        }
    profile = _prompt_profile(cleaned)
    if normalized == "image":
        if profile == "human":
            improved = _trim_prompt(
                f"Ultra-realistic editorial portrait of {cleaned}, consistent facial identity, natural skin texture with subtle pores, "
                "believable anatomy, soft directional light, restrained premium styling, clean composition, no text, no watermark."
            )
        elif profile == "architecture":
            improved = _trim_prompt(
                f"Ultra-realistic architectural still of {cleaned}, refined natural light, realistic materials, restrained luxury atmosphere, "
                "clean composition, premium realism, no text, no watermark."
            )
        elif profile == "product":
            improved = _trim_prompt(
                f"Ultra-realistic luxury product still of {cleaned}, sculpted lighting, realistic reflections, premium material detail, "
                "clean background hierarchy, commercial realism, no text, no watermark."
            )
        else:
            improved = _trim_prompt(
                f"Ultra-realistic still frame of {cleaned}, refined lighting, realistic material detail, premium atmosphere, "
                "strong composition, clean visual hierarchy, elegant realism, no text, no watermark."
            )
        summary = "Enhanced by Vision for a stronger premium still."
    else:
        if profile in {"human", "human_video"}:
            improved = _trim_prompt(
                f"Ultra-realistic single-shot editorial fashion video of {cleaned}, medium-wide framing, restrained tracking camera, "
                "consistent facial identity throughout, natural skin texture, elegant fabric movement, soft directional light, "
                "quiet luxury atmosphere, feature-film realism, no text, no watermark."
            )
        elif profile in {"architecture", "architecture_video"}:
            improved = _trim_prompt(
                f"Ultra-realistic cinematic architectural shot of {cleaned}, slow controlled camera drift, realistic material detail, "
                "golden-hour or soft directional light, premium realism, clean visual hierarchy, no text, no watermark."
            )
        elif profile == "product":
            improved = _trim_prompt(
                f"Ultra-realistic cinematic commercial shot of {cleaned}, slow premium camera motion, refined highlights, realistic materials, "
                "luxury atmosphere, polished realism, no text, no watermark."
            )
        else:
            improved = _trim_prompt(
                f"Ultra-realistic cinematic scene of {cleaned}, natural motion, controlled camera movement, refined lighting, "
                "realistic textures, premium atmosphere, clean composition, feature-film realism, no text, no watermark."
            )
        summary = "Enhanced by Vision for stronger cinematic realism."
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
    cleaned_prompt = _normalize_subject(_dedupe_intro(prompt))
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
            temperature=0.55,
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
- For people, prefer single-shot continuity, consistent facial identity, realistic skin texture, believable anatomy, and premium editorial realism.
- For architecture and interiors, prefer refined natural light, realistic materials, and controlled camera movement.
- For landscapes and environments, prefer one strong time-of-day cue, atmospheric depth, and grounded realism.
- For human video, prefer medium or medium-wide framing, restrained tracking or locked camera movement, and avoid sudden close-up escalation.
- If the prompt already sounds premium, only tighten it lightly instead of rewriting the opening phrase.
- Never repeat the same cinematic intro twice.
- Remove stacked or duplicated phrases such as "shot of shot of", "cinematic cinematic", or repeated realism modifiers.
- Avoid generic filler like "strong visual direction" unless it adds something concrete.
- Prefer one clear cinematic idea over a pile of vague adjectives.
- Avoid melodramatic prose, overly literary verbs, and vague luxury language that does not change the shot.
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
