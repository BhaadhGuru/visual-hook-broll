"""News B-Roll & SOT Engine — End-to-End Orchestrator & CLI.

Pipeline:
  1. Script Segmenter (Ollama structured JSON -> VO/SOT shots, 3-5s pacing)
  2. Camera & Style Anchor (Sony FX9 / 35mm / Rec.709 broadcast continuity wrapper)
  3. Batch Execution & Routing:
       - Optional Veo 3.1 clip generation (with optional local reference still)
       - Yap audio marker alignment (JSON/SRT/VTT or 4s sequential news pacing)
       - Pre-numbered clip & audio staging into DaVinci Resolve watched folder
       - `resolve_markers.json` (resolve_markers.py compatible) + `resolve_markers.csv`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .news_segmenter import (
    DEFAULT_CAMERA_STYLE_ANCHOR,
    NewsShot,
    segment_news_script,
)
from .veo_client import VeoClient, VeoConfig
from .yap_merge import (
    build_resolve_markers,
    load_yap_markers,
    stage_yap_audio_tracks,
    write_resolve_marker_csv,
)


def _slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_")
    return (slug[:max_len].strip("_")) or "News_Pkg"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def format_news_whatsapp_summary(
    package_name: str,
    shots: list[NewsShot],
    resolve_markers: dict[str, Any],
    *,
    resolve_dir: Path,
    veo_mode: str,
) -> str:
    """Format a compact broadcast news shot sheet for WhatsApp."""
    markers_by_idx = {m["index"]: m for m in resolve_markers.get("markers", [])}
    lines = [
        f"📺 *NEWS B-ROLL & SOT PACKAGE: {package_name}*",
        f"🎞️ Shots: {len(shots)} | Mode: {veo_mode}",
        f"📂 Resolve Folder: `{resolve_dir}`",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for shot in shots:
        m = markers_by_idx.get(shot.shot_index, {})
        tc = m.get("timecode", "00:00:00:00")
        tag = "🟢 VO" if shot.segment_type == "VO" else "🔵 SOT"
        lines.append(f"*{shot.suggested_filename}* ({tag} @ {tc})")
        lines.append(f"• {shot.visual_description}")
    lines.extend(
        [
            "",
            "✅ `resolve_markers.json` & `resolve_markers.csv` staged for DaVinci Resolve.",
        ]
    )
    return "\n".join(lines)


def format_veo_prompts_markdown(package_name: str, shots: list[NewsShot]) -> str:
    lines = [
        f"# News B-Roll Veo 3.1 Batch Prompts — {package_name}",
        "",
        "Camera & Style Anchor locked to Sony FX9 / 35mm handheld shoulder-mount / Neutral Rec.709.",
        "",
    ]
    for shot in shots:
        lines.extend(
            [
                f"## {shot.shot_index:02d}. `{shot.suggested_filename}` [{shot.segment_type}]",
                f"- **Objective Visual:** {shot.visual_description}",
                f"- **Wrapped Veo 3.1 Prompt:**",
                f"  > {shot.veo_prompt}",
                "",
            ]
        )
    return "\n".join(lines)


def run_news_package(
    *,
    package_id: str,
    script_text: str,
    cfg: AppConfig,
    yap_markers_path: Path | None = None,
    yap_audio_dir: Path | None = None,
    ref_image_path: Path | None = None,
    ref_mode: str = "asset",
    resolve_dir: Path | None = None,
    prompts_only: bool = False,
    resume: bool = True,
    prebuilt_shots: list[NewsShot] | None = None,
    sender: str = "",
    chat_id: str = "",
    message_id: str = "",
) -> dict[str, Any]:
    """Execute the complete News B-Roll & SOT Engine for one broadcast script."""
    clean_script = re.sub(r"#news\b", "", script_text, flags=re.IGNORECASE).strip()
    if not clean_script:
        raise ValueError("News script is empty.")

    news_cfg = cfg.news_engine
    aspect = "9:16" if re.search(r"#(vertical|9:16)\b", script_text, re.IGNORECASE) else news_cfg.aspect_ratio
    resolution = news_cfg.resolution

    # Stage 1 & 2: Segment VO/SOT + Wrap Sony FX9 Camera & Style Anchor
    if prebuilt_shots is not None:
        shots = prebuilt_shots
    else:
        shots = segment_news_script(
            clean_script,
            cfg.ollama,
            resolution=resolution,
            aspect_ratio=aspect,
            anchor_template=DEFAULT_CAMERA_STYLE_ANCHOR,
        )

    pkg_slug = _slugify(package_id)
    base_resolve_dir = resolve_dir or news_cfg.resolve_watch_dir
    package_dir = base_resolve_dir / pkg_slug
    broll_dir = package_dir / "01_Broll_Veo"
    broll_dir.mkdir(parents=True, exist_ok=True)

    # Load Yap audio markers if supplied (or auto-discover latest in yap_output_dir)
    yap_slots = None
    if yap_markers_path is not None:
        yap_slots = load_yap_markers(yap_markers_path, fps=news_cfg.fps)

    # Build DaVinci Resolve markers mapped to pre-numbered shot filenames
    resolve_markers = build_resolve_markers(
        pkg_slug,
        shots,
        yap_slots=yap_slots,
        fps=news_cfg.fps,
        default_pacing_seconds=news_cfg.default_pacing_seconds,
    )

    # Stage Yap audio tracks if provided
    staged_audio = stage_yap_audio_tracks(yap_audio_dir, package_dir)

    # Stage 3: Optional Veo 3.1 batch execution
    rendered_clips: list[dict[str, Any]] = []
    run_veo = (not prompts_only) and news_cfg.auto_run_veo
    if not prompts_only and prebuilt_shots is None:
        run_veo = True

    veo_mode = "Prompts & Resolve Markers Only (dry-run)"
    if not prompts_only:
        veo_cfg = VeoConfig(
            provider=news_cfg.veo_provider,
            model=news_cfg.veo_model,
            resolution=resolution,
            aspect_ratio=aspect,
            duration_seconds=news_cfg.veo_duration_seconds,
            generate_audio=news_cfg.veo_generate_audio,
            vertex_project_id=news_cfg.vertex_project_id,
            vertex_location=news_cfg.vertex_location,
        )
        veo_client = VeoClient(veo_cfg)
        veo_mode = f"Veo 3.1 ({veo_cfg.provider}:{veo_cfg.model})"

        for shot in shots:
            clip_path = broll_dir / shot.suggested_filename
            if resume and clip_path.exists() and clip_path.stat().st_size > 0:
                rendered_clips.append(
                    {"shot_index": shot.shot_index, "filename": shot.suggested_filename, "path": str(clip_path), "status": "cached"}
                )
                continue
            veo_client.generate_clip(
                shot.veo_prompt,
                clip_path,
                ref_image_path=ref_image_path,
                ref_mode=ref_mode,
            )
            rendered_clips.append(
                {"shot_index": shot.shot_index, "filename": shot.suggested_filename, "path": str(clip_path), "status": "generated"}
            )
    else:
        for shot in shots:
            rendered_clips.append(
                {
                    "shot_index": shot.shot_index,
                    "filename": shot.suggested_filename,
                    "path": str(broll_dir / shot.suggested_filename),
                    "status": "pending_veo",
                }
            )

    # Write DaVinci Resolve watched-folder artifacts
    markers_json_path = package_dir / "resolve_markers.json"
    markers_csv_path = package_dir / "resolve_markers.csv"
    prompts_json_path = package_dir / "veo_batch_prompts.json"
    prompts_md_path = package_dir / "veo_batch_prompts.md"

    _atomic_write_text(markers_json_path, json.dumps(resolve_markers, indent=2) + "\n")
    write_resolve_marker_csv(resolve_markers, markers_csv_path)
    _atomic_write_text(prompts_json_path, json.dumps([s.to_dict() for s in shots], indent=2) + "\n")
    _atomic_write_text(prompts_md_path, format_veo_prompts_markdown(pkg_slug, shots))

    whatsapp_text = format_news_whatsapp_summary(
        pkg_slug, shots, resolve_markers, resolve_dir=package_dir, veo_mode=veo_mode
    )

    manifest: dict[str, Any] = {
        "job_id": pkg_slug,
        "engine": "news-broll-sot-engine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "sender": sender,
        "chat_id": chat_id,
        "message_id": message_id,
        "resolution": resolution,
        "aspect_ratio": aspect,
        "fps": news_cfg.fps,
        "veo_mode": veo_mode,
        "resolve_package_dir": str(package_dir),
        "resolve_markers_json": str(markers_json_path),
        "resolve_markers_csv": str(markers_csv_path),
        "veo_prompts_json": str(prompts_json_path),
        "staged_yap_audio": staged_audio,
        "clips": rendered_clips,
        "shots": [s.to_dict() for s in shots],
        "whatsapp_message": whatsapp_text,
        "storyboard_filename": None,
    }
    _atomic_write_text(package_dir / "news_package_manifest.json", json.dumps(manifest, indent=2) + "\n")

    # Also mirror *_result.json into BrollOutput when invoked with a WhatsApp sender/worker
    if cfg.output_dir:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(cfg.output_dir / f"{pkg_slug}_whatsapp.txt", whatsapp_text + "\n")
        _atomic_write_text(cfg.output_dir / f"{pkg_slug}_result.json", json.dumps(manifest, indent=2) + "\n")

    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="News B-Roll & SOT Engine (Ollama + Sony FX9 Anchor + Veo 3.1 + Yap/Resolve Merge)")
    ap.add_argument("script_file", help="Path to raw VO/SOT/STANDUP news script (.txt)")
    ap.add_argument("--config", default=None, help="Path to config.json")
    ap.add_argument("--package-id", default=None, help="Optional package folder name")
    ap.add_argument("--yap-markers", default=None, help="Path to Yap markers (.json, .srt, or .vtt)")
    ap.add_argument("--yap-audio-dir", default=None, help="Optional folder with Yap VO/SOT audio tracks to stage")
    ap.add_argument("--ref-image", default=None, help="Optional local reference still for Veo 3.1 color/lighting match")
    ap.add_argument("--ref-mode", choices=["asset", "first_frame"], default="asset", help="How to attach --ref-image to Veo 3.1")
    ap.add_argument("--resolve-dir", default=None, help="Override DaVinci Resolve watched folder")
    ap.add_argument("--prompts-only", action="store_true", help="Generate JSON, Sony FX9 Veo prompts & Resolve markers without calling Veo 3.1")
    ap.add_argument("--no-resume", action="store_true", help="Re-render clips even if filename already exists")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    src = Path(args.script_file)
    if not src.exists():
        print(f"Error: script file not found: {src}", file=sys.stderr)
        return 1

    pkg_id = args.package_id or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{src.stem}"
    manifest = run_news_package(
        package_id=pkg_id,
        script_text=src.read_text(encoding="utf-8-sig"),
        cfg=cfg,
        yap_markers_path=Path(args.yap_markers) if args.yap_markers else None,
        yap_audio_dir=Path(args.yap_audio_dir) if args.yap_audio_dir else None,
        ref_image_path=Path(args.ref_image) if args.ref_image else None,
        ref_mode=args.ref_mode,
        resolve_dir=Path(args.resolve_dir) if args.resolve_dir else None,
        prompts_only=args.prompts_only,
        resume=not args.no_resume,
    )
    print(f"Package staged: {manifest['resolve_package_dir']}")
    print(f"Shots:          {len(manifest['shots'])}")
    print(f"Resolve JSON:   {manifest['resolve_markers_json']}")
    print(f"Veo Prompts:    {manifest['veo_prompts_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
