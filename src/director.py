"""Visual Hook & Curiosity-Loop B-Roll Director powered by local Ollama."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import OllamaConfig


PANEL_POSITIONS_4 = ["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right"]
PANEL_POSITIONS_6 = [
    "Top-Left",
    "Top-Right",
    "Middle-Left",
    "Middle-Right",
    "Bottom-Left",
    "Bottom-Right",
]


@dataclass
class ScriptDirectives:
    clean_script: str
    aspect: str = "16:9"
    panel_count: int = 4
    generate_images: bool = True


@dataclass
class ShotPanel:
    panel_index: int
    panel_position: str
    shot_label: str
    curiosity_role: str
    script_beat: str
    visual_instruction: str
    framing_and_lens: str
    camera_movement: str
    lighting_and_props: str
    flux_prompt: str


@dataclass
class ShotListPlan:
    title: str
    hook_0_3s_concept: str
    curiosity_loop_strategy: str
    consistency_lock: str
    gear_and_location_notes: str
    shots: list[ShotPanel] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "hook_0_3s_concept": self.hook_0_3s_concept,
            "curiosity_loop_strategy": self.curiosity_loop_strategy,
            "consistency_lock": self.consistency_lock,
            "gear_and_location_notes": self.gear_and_location_notes,
            "shots": [
                {
                    "panel_index": s.panel_index,
                    "panel_position": s.panel_position,
                    "shot_label": s.shot_label,
                    "curiosity_role": s.curiosity_role,
                    "script_beat": s.script_beat,
                    "visual_instruction": s.visual_instruction,
                    "framing_and_lens": s.framing_and_lens,
                    "camera_movement": s.camera_movement,
                    "lighting_and_props": s.lighting_and_props,
                    "flux_prompt": s.flux_prompt,
                }
                for s in self.shots
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShotListPlan":
        shots_raw = data.get("shots", [])
        positions = PANEL_POSITIONS_6 if len(shots_raw) > 4 else PANEL_POSITIONS_4
        shots: list[ShotPanel] = []
        for idx, item in enumerate(shots_raw, start=1):
            default_pos = positions[idx - 1] if idx <= len(positions) else f"Panel {idx}"
            shots.append(
                ShotPanel(
                    panel_index=int(item.get("panel_index", idx)),
                    panel_position=str(item.get("panel_position") or default_pos),
                    shot_label=str(item.get("shot_label") or f"Hook {idx}"),
                    curiosity_role=str(item.get("curiosity_role") or "B-ROLL BEAT"),
                    script_beat=str(item.get("script_beat") or ""),
                    visual_instruction=str(item.get("visual_instruction") or ""),
                    framing_and_lens=str(item.get("framing_and_lens") or "Medium Close-Up (35mm f/2.8)"),
                    camera_movement=str(item.get("camera_movement") or "Slow handheld push-in"),
                    lighting_and_props=str(item.get("lighting_and_props") or "Natural practical lighting"),
                    flux_prompt=str(item.get("flux_prompt") or ""),
                )
            )
        return cls(
            title=str(data.get("title") or "Visual Hook B-Roll Shot List"),
            hook_0_3s_concept=str(data.get("hook_0_3s_concept") or ""),
            curiosity_loop_strategy=str(data.get("curiosity_loop_strategy") or ""),
            consistency_lock=str(data.get("consistency_lock") or ""),
            gear_and_location_notes=str(data.get("gear_and_location_notes") or ""),
            shots=shots,
        )


def parse_script_directives(raw_text: str, default_panel_count: int = 4) -> ScriptDirectives:
    """Extract optional WhatsApp inline hashtags like #vertical, #shots=6, #noimages."""
    aspect = "16:9"
    panel_count = default_panel_count
    generate_images = True

    if re.search(r"#(vertical|9:16|reel|reels|shorts)\b", raw_text, re.IGNORECASE):
        aspect = "9:16"
    elif re.search(r"#(horizontal|16:9|landscape)\b", raw_text, re.IGNORECASE):
        aspect = "16:9"

    match_shots = re.search(r"#shots\s*=\s*(\d+)", raw_text, re.IGNORECASE)
    if match_shots:
        panel_count = max(2, min(6, int(match_shots.group(1))))

    if re.search(r"#noimages?\b", raw_text, re.IGNORECASE):
        generate_images = False

    clean = re.sub(
        r"#(?:vertical|9:16|reel|reels|shorts|horizontal|16:9|landscape|noimages?|shots=\d+)\b",
        "",
        raw_text,
        flags=re.IGNORECASE,
    ).strip()

    return ScriptDirectives(
        clean_script=clean,
        aspect=aspect,
        panel_count=panel_count,
        generate_images=generate_images,
    )


def _build_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "hook_0_3s_concept": {"type": "string"},
            "curiosity_loop_strategy": {"type": "string"},
            "consistency_lock": {"type": "string"},
            "gear_and_location_notes": {"type": "string"},
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "panel_index": {"type": "integer"},
                        "panel_position": {"type": "string"},
                        "shot_label": {"type": "string"},
                        "curiosity_role": {"type": "string"},
                        "script_beat": {"type": "string"},
                        "visual_instruction": {"type": "string"},
                        "framing_and_lens": {"type": "string"},
                        "camera_movement": {"type": "string"},
                        "lighting_and_props": {"type": "string"},
                        "flux_prompt": {"type": "string"},
                    },
                    "required": [
                        "panel_index",
                        "panel_position",
                        "shot_label",
                        "curiosity_role",
                        "script_beat",
                        "visual_instruction",
                        "framing_and_lens",
                        "camera_movement",
                        "lighting_and_props",
                        "flux_prompt",
                    ],
                },
            },
        },
        "required": [
            "title",
            "hook_0_3s_concept",
            "curiosity_loop_strategy",
            "consistency_lock",
            "gear_and_location_notes",
            "shots",
        ],
    }


SYSTEM_PROMPT = """You are a commercial video director and DP (Director of Photography) who builds scroll-stopping Visual Hook & Curiosity-Loop B-Roll Shot Lists for on-site photographers and videographers.

Given a video script, design a tight, shootable storyboard plan with visual instructions the photographer can immediately shoot on location.

CORE DIRECTING PRINCIPLES:
1. Visual Hook (0-3s): Open with immediate visual tension, motion, or an unmistakable problem-in-progress so viewers stop scrolling even with sound OFF.
2. Curiosity Loop: Sequence the B-roll panels so each visual raises or resolves a micro-question (Tension -> Diagnosis -> Action/Wait -> Proof/Backup Payoff). Never waste a shot on generic smiling stock poses.
3. Photographer-Ready Direction: Every shot must include exact framing & focal length, camera movement, practical props/signage, and a plain-English visual instruction.
4. Consistency Lock: Define ONE cohesive real-world location, lighting style, wardrobe/talent cues, and color palette, and weave that consistency lock into EVERY shot's `flux_prompt` so all generated storyboard frames look like they belong to the same shoot.
5. Flux Image Prompts: Write clean, photorealistic documentary/commercial still-frame prompts for Flux. Describe concrete physical actions, hands, objects, lighting, and camera angle. Never include watermarks, UI borders, split-screens, or overlaid captions. Only include diegetic in-world text when the script specifically calls for a physical sign/label/screen readout (put exact text in single quotes).
"""


def _http_post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Ollama at {url}: {exc.reason}") from exc


def generate_shot_list(
    script_text: str,
    ollama_cfg: OllamaConfig,
    panel_count: int = 4,
    aspect: str = "16:9",
) -> ShotListPlan:
    """Call local Ollama to produce a structured Visual Hook & Curiosity-Loop shot list."""
    positions = PANEL_POSITIONS_6[:panel_count] if panel_count > 4 else PANEL_POSITIONS_4[:panel_count]
    user_prompt = (
        f"Create a {panel_count}-panel Visual Hook & Curiosity-Loop B-Roll Shot List "
        f"for the following script (target aspect ratio: {aspect}).\n"
        f"Assign panel_position values in exact order: {', '.join(positions)}.\n"
        f"If the script has more tips than panels, combine the final two related beats cleanly "
        f"into the last panel (e.g., shot_label 'Hook 4 & 5').\n"
        f"Return strictly valid JSON matching the schema.\n\n"
        f"SCRIPT:\n{script_text}"
    )

    payload = {
        "model": ollama_cfg.model,
        "stream": False,
        "format": _build_json_schema(),
        "options": {"temperature": ollama_cfg.temperature},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    url = f"{ollama_cfg.base_url.rstrip('/')}/api/chat"
    response = _http_post_json(url, payload, timeout=ollama_cfg.timeout_seconds)
    content = response.get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"Ollama returned an empty response: {response}")

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {exc}\nRaw output:\n{content[:500]}") from exc

    plan = ShotListPlan.from_dict(parsed)
    if not plan.shots:
        raise RuntimeError("Ollama returned zero shots in the plan.")
    return plan


def unload_ollama_model(ollama_cfg: OllamaConfig) -> None:
    """Explicitly evict the Ollama model from GPU VRAM before ComfyUI Flux runs."""
    url = f"{ollama_cfg.base_url.rstrip('/')}/api/generate"
    payload = {"model": ollama_cfg.model, "prompt": "", "keep_alive": 0}
    _http_post_json(url, payload, timeout=30)
