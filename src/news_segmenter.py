"""Stage 1 (Script Segmenter) & Stage 2 (Camera & Style Anchor) for the News B-Roll & SOT Engine."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import OllamaConfig
from .director import _http_post_json


NEWS_SEGMENTER_PROMPT = (
    "Analyze the provided news script. Extract the most visually compelling moments and "
    "rephrase them into distinct, objective B-roll shot descriptions. Focus on physical "
    "action in the frame (e.g., 'People walking into a building' rather than 'The meeting "
    "started'). Use neutral, journalistic language without emotive or cinematic adjectives. "
    "Focus on shots suited for 3-5 second news pacing. Output a structured JSON list "
    "containing: shot_index, segment_type (VO or SOT), suggested_filename (e.g., "
    "01_Suburban_Street.mp4), and visual_description."
)

DEFAULT_CAMERA_STYLE_ANCHOR = (
    "Generate a {resolution}, {aspect_ratio} broadcast news video of {visual_description}. "
    "Mandatory Style: Shot on Sony FX9 with a 35mm lens, featuring realistic handheld "
    "shoulder-mount movement with a slight natural sway. Neutral Rec.709 color profile, "
    "natural daylight or high-CRI office lighting, sharp high-detail focus. Authentic local "
    "news aesthetic, no stylized cinematic filters, no AI artifacts."
)


@dataclass
class NewsShot:
    shot_index: int
    segment_type: str  # "VO" or "SOT"
    suggested_filename: str
    visual_description: str
    veo_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_index": self.shot_index,
            "segment_type": self.segment_type,
            "suggested_filename": self.suggested_filename,
            "visual_description": self.visual_description,
            "veo_prompt": self.veo_prompt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_index: int = 1) -> "NewsShot":
        idx = int(data.get("shot_index", fallback_index))
        raw_seg = str(data.get("segment_type", "VO")).strip().upper()
        seg_type = "SOT" if "SOT" in raw_seg else "VO"
        raw_desc = str(data.get("visual_description") or "").strip()
        raw_name = str(data.get("suggested_filename") or "").strip()
        filename = sanitize_shot_filename(raw_name, idx, raw_desc)
        return cls(
            shot_index=idx,
            segment_type=seg_type,
            suggested_filename=filename,
            visual_description=raw_desc,
            veo_prompt=str(data.get("veo_prompt") or ""),
        )


def sanitize_shot_filename(raw_name: str, shot_index: int, fallback_desc: str = "") -> str:
    """Enforce pre-numbered DaVinci Resolve-safe MP4 filenames like 01_Suburban_Street.mp4."""
    stem = raw_name.strip()
    if stem.lower().endswith(".mp4"):
        stem = stem[:-4]
    # Strip any existing leading digits/underscores so we re-apply exact 2-digit shot_index
    stem = re.sub(r"^\d+[\s_-]*", "", stem)
    if not stem and fallback_desc:
        words = re.findall(r"[A-Za-z0-9]+", fallback_desc)[:4]
        stem = "_".join(w.capitalize() for w in words)
    clean_stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")[:48] or "News_Broll"
    return f"{shot_index:02d}_{clean_stem}.mp4"


def wrap_camera_style_anchor(
    visual_description: str,
    *,
    resolution: str = "1080p",
    aspect_ratio: str = "16:9",
    anchor_template: str = DEFAULT_CAMERA_STYLE_ANCHOR,
) -> str:
    """Apply the Sony FX9 / Rec.709 broadcast news continuity anchor to a shot description."""
    desc = visual_description.strip().rstrip(".")
    # Support both {{Resolution}} / {{Aspect Ratio}} / {{visual_description}} and single-brace forms
    rendered = anchor_template
    rendered = rendered.replace("{{Resolution}}", resolution).replace("{resolution}", resolution)
    rendered = rendered.replace("{{Aspect Ratio}}", aspect_ratio).replace("{aspect_ratio}", aspect_ratio)
    rendered = rendered.replace("{{visual_description}}", desc).replace("{visual_description}", desc)
    return rendered


def _news_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "shot_index": {"type": "integer"},
                        "segment_type": {"type": "string", "enum": ["VO", "SOT"]},
                        "suggested_filename": {"type": "string"},
                        "visual_description": {"type": "string"},
                    },
                    "required": [
                        "shot_index",
                        "segment_type",
                        "suggested_filename",
                        "visual_description",
                    ],
                },
            }
        },
        "required": ["shots"],
    }


def segment_news_script(
    script_text: str,
    ollama_cfg: OllamaConfig,
    *,
    resolution: str = "1080p",
    aspect_ratio: str = "16:9",
    anchor_template: str = DEFAULT_CAMERA_STYLE_ANCHOR,
) -> list[NewsShot]:
    """Run Stage 1 (LLM Script Segmenter) via local Ollama and Stage 2 (Camera Anchor)."""
    user_prompt = (
        f"{NEWS_SEGMENTER_PROMPT}\n\n"
        f"Return a JSON object with a top-level 'shots' array matching the schema.\n"
        f"NEWS SCRIPT:\n{script_text.strip()}"
    )
    payload = {
        "model": ollama_cfg.model,
        "stream": False,
        "format": _news_json_schema(),
        "options": {"temperature": ollama_cfg.temperature},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a broadcast television news field producer. "
                    "Extract objective, physical-action B-roll shots for 3-5 second news pacing. "
                    "Never use cinematic or emotive adjectives. Return strictly valid JSON."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
    }

    url = f"{ollama_cfg.base_url.rstrip('/')}/api/chat"
    response = _http_post_json(url, payload, timeout=ollama_cfg.timeout_seconds)
    content = response.get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"Ollama returned empty news segmentation response: {response}")

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {exc}\nRaw:\n{content[:500]}") from exc

    raw_shots = parsed if isinstance(parsed, list) else parsed.get("shots", [])
    if not isinstance(raw_shots, list) or not raw_shots:
        raise RuntimeError(f"Ollama returned zero shots: {parsed}")

    shots: list[NewsShot] = []
    for i, item in enumerate(raw_shots, start=1):
        shot = NewsShot.from_dict(item, fallback_index=i)
        shot.shot_index = i
        shot.suggested_filename = sanitize_shot_filename(
            shot.suggested_filename, i, shot.visual_description
        )
        shot.veo_prompt = wrap_camera_style_anchor(
            shot.visual_description,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            anchor_template=anchor_template,
        )
        shots.append(shot)

    return shots
