"""End-to-end job runner: Script -> Ollama Shot List -> ComfyUI Flux -> 2x2 Storyboard -> Outputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .comfy_client import ComfyUIClient
from .config import AppConfig, load_config
from .director import (
    ShotListPlan,
    generate_shot_list,
    parse_script_directives,
    unload_ollama_model,
)
from .formatter import (
    format_markdown_shoot_card,
    format_whatsapp_image_caption,
    format_whatsapp_shot_list,
)
from .storyboard import compose_storyboard_grid, make_placeholder_panel


def _slugify(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return (slug[:max_len].strip("-")) or "shoot"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run_job(
    *,
    job_id: str,
    script_text: str,
    cfg: AppConfig,
    sender: str = "",
    chat_id: str = "",
    message_id: str = "",
    prebuilt_plan: ShotListPlan | None = None,
    prebuilt_images: list[Path] | None = None,
    mock_comfy: bool = False,
) -> dict[str, Any]:
    """Execute one full script-to-visual-hook-storyboard job and write flat + folder outputs."""
    directives = parse_script_directives(
        script_text, default_panel_count=cfg.storyboard.default_panel_count
    )
    if not directives.clean_script:
        raise ValueError("Script text is empty after stripping directives.")

    # Stage 1: Generate structured shot list via local Ollama
    if prebuilt_plan is not None:
        plan = prebuilt_plan
    else:
        plan = generate_shot_list(
            directives.clean_script,
            cfg.ollama,
            panel_count=directives.panel_count,
            aspect=directives.aspect,
        )
        if cfg.ollama.unload_after_generation and directives.generate_images and not mock_comfy:
            try:
                unload_ollama_model(cfg.ollama)
            except Exception as exc:
                print(f"[warn] Could not explicitly unload Ollama VRAM: {exc}", file=sys.stderr)

    safe_job_id = _slugify(job_id, max_len=64)
    job_dir = cfg.output_dir / safe_job_id
    panels_dir = job_dir / "panels"
    panels_dir.mkdir(parents=True, exist_ok=True)

    # Target Flux resolution based on #vertical vs #horizontal
    if directives.aspect == "9:16":
        flux_w, flux_h = 768, 1024
    else:
        flux_w, flux_h = cfg.comfyui.width, cfg.comfyui.height

    # Stage 2: Generate per-panel reference images via local ComfyUI Flux
    panel_paths: list[Path] = []
    storyboard_path: Path | None = None
    flat_storyboard_path: Path | None = None

    if directives.generate_images:
        if prebuilt_images is not None:
            panel_paths = list(prebuilt_images)
        elif mock_comfy:
            for shot in plan.shots:
                out = panels_dir / f"panel_{shot.panel_index:02d}_{_slugify(shot.shot_label, 24)}.png"
                make_placeholder_panel(shot, out, width=flux_w, height=flux_h)
                panel_paths.append(out)
        else:
            comfy = ComfyUIClient(cfg.comfyui, cfg.resolve_workflow_path())
            try:
                for shot in plan.shots:
                    out = panels_dir / f"panel_{shot.panel_index:02d}_{_slugify(shot.shot_label, 24)}.png"
                    comfy.generate_image(
                        prompt_text=shot.flux_prompt,
                        output_path=out,
                        width=flux_w,
                        height=flux_h,
                        filename_prefix=f"{safe_job_id}_p{shot.panel_index:02d}",
                    )
                    panel_paths.append(out)
            finally:
                if cfg.comfyui.free_vram_after_job:
                    try:
                        comfy.free_vram()
                    except Exception as exc:
                        print(f"[warn] Could not free ComfyUI VRAM: {exc}", file=sys.stderr)

        # Stage 3: Stitch into labeled 2x2 / Nx2 photog storyboard sheet
        storyboard_path = job_dir / "storyboard_grid.png"
        compose_storyboard_grid(
            plan,
            panel_paths,
            storyboard_path,
            columns=cfg.storyboard.grid_columns,
            panel_width=cfg.storyboard.panel_width,
            panel_height=cfg.storyboard.panel_height,
            aspect=directives.aspect,
        )
        # Flat copy in BrollOutput/ so n8n / Make.com Drive Watch finds it directly
        flat_storyboard_path = cfg.output_dir / f"{safe_job_id}_storyboard.png"
        flat_storyboard_path.write_bytes(storyboard_path.read_bytes())

    # Stage 4: Format outputs for WhatsApp + Shoot Card Archive
    whatsapp_text = format_whatsapp_shot_list(plan, aspect=directives.aspect)
    image_caption = format_whatsapp_image_caption(plan)
    markdown_card = format_markdown_shoot_card(plan, aspect=directives.aspect)

    _atomic_write_text(job_dir / "whatsapp_message.txt", whatsapp_text + "\n")
    _atomic_write_text(job_dir / "shoot_card.md", markdown_card)
    _atomic_write_text(job_dir / "shot_plan.json", json.dumps(plan.to_dict(), indent=2))
    _atomic_write_text(cfg.output_dir / f"{safe_job_id}_whatsapp.txt", whatsapp_text + "\n")

    result_manifest: dict[str, Any] = {
        "job_id": safe_job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "sender": sender,
        "chat_id": chat_id,
        "message_id": message_id,
        "aspect": directives.aspect,
        "title": plan.title,
        "whatsapp_message": whatsapp_text,
        "whatsapp_image_caption": image_caption,
        "storyboard_filename": flat_storyboard_path.name if flat_storyboard_path else None,
        "storyboard_path": str(flat_storyboard_path) if flat_storyboard_path else None,
        "panel_files": [str(p) for p in panel_paths],
        "plan": plan.to_dict(),
    }

    # Write *_result.json LAST (atomically) because n8n / Make.com triggers on this file!
    result_json_path = cfg.output_dir / f"{safe_job_id}_result.json"
    _atomic_write_text(result_json_path, json.dumps(result_manifest, indent=2))
    return result_manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one Visual Hook & B-Roll Shot List job from a script file.")
    ap.add_argument("script_file", help="Path to a .txt script file or .json job payload")
    ap.add_argument("--config", default=None, help="Path to config.json")
    ap.add_argument("--job-id", default=None, help="Optional custom job ID")
    ap.add_argument("--mock-comfy", action="store_true", help="Generate wireframe placeholder frames instead of calling ComfyUI")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    src = Path(args.script_file)
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        return 1

    sender = chat_id = message_id = ""
    if src.suffix.lower() == ".json":
        payload = json.loads(src.read_text(encoding="utf-8"))
        script_text = str(payload.get("script") or payload.get("text") or payload.get("body") or "")
        sender = str(payload.get("sender") or payload.get("from") or "")
        chat_id = str(payload.get("chat_id") or sender)
        message_id = str(payload.get("message_id") or "")
        job_id = args.job_id or str(payload.get("job_id") or src.stem)
    else:
        script_text = src.read_text(encoding="utf-8")
        job_id = args.job_id or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{src.stem}"

    result = run_job(
        job_id=job_id,
        script_text=script_text,
        cfg=cfg,
        sender=sender,
        chat_id=chat_id,
        message_id=message_id,
        mock_comfy=args.mock_comfy,
    )
    print(f"Done: {result['job_id']}")
    print(f"Result JSON: {cfg.output_dir / (result['job_id'] + '_result.json')}")
    if result.get("storyboard_path"):
        print(f"Storyboard:  {result['storyboard_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
