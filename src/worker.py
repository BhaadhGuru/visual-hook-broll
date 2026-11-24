"""Continuous folder watcher mirroring the YapInput -> YapOutput Drive bridge.

Watches `input_dir` (e.g. G:\\My Drive\\BrollInput) for `.json` or `.txt` files dropped
by n8n or Make.com from WhatsApp, runs the local Ollama + ComfyUI Flux pipeline,
and writes outputs atomically to `output_dir` (e.g. G:\\My Drive\\BrollOutput).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, load_config
from .pipeline import run_job


SUPPORTED_SUFFIXES = {".json", ".txt"}


def _is_file_stable(path: Path, wait_seconds: float = 0.4) -> bool:
    """Wait briefly to ensure Google Drive / rclone finished writing the dropped file."""
    try:
        size_a = path.stat().st_size
        time.sleep(wait_seconds)
        size_b = path.stat().st_size
        return size_a == size_b and size_b > 0
    except OSError:
        return False


def _load_incoming_job(path: Path) -> tuple[str, str, str, str, str]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        script_text = str(data.get("script") or data.get("text") or data.get("body") or "").strip()
        sender = str(data.get("sender") or data.get("from") or "")
        chat_id = str(data.get("chat_id") or sender)
        message_id = str(data.get("message_id") or "")
        job_id = str(data.get("job_id") or path.stem)
        return job_id, script_text, sender, chat_id, message_id

    script_text = path.read_text(encoding="utf-8-sig").strip()
    job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.stem}"
    return job_id, script_text, "", "", ""


def process_once(cfg: AppConfig, *, mock_comfy: bool = False) -> int:
    """Scan input_dir once and process all ready job files. Returns count processed."""
    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    failed_dir = cfg.input_dir / "_failed"

    candidates = sorted(
        p
        for p in cfg.input_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and not p.name.startswith(".")
        and not p.name.endswith(".tmp")
    )

    processed = 0
    for path in candidates:
        if not _is_file_stable(path):
            continue

        print(f"[worker] Picked up {path.name}")
        try:
            job_id, script_text, sender, chat_id, message_id = _load_incoming_job(path)
            result = run_job(
                job_id=job_id,
                script_text=script_text,
                cfg=cfg,
                sender=sender,
                chat_id=chat_id,
                message_id=message_id,
                mock_comfy=mock_comfy,
            )
            dest = cfg.archive_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
            shutil.move(str(path), str(dest))
            print(f"[worker] Completed {result['job_id']} -> {cfg.output_dir}")
            processed += 1
        except Exception as exc:
            failed_dir.mkdir(parents=True, exist_ok=True)
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[worker] ERROR processing {path.name}: {err_msg}", file=sys.stderr)
            traceback.print_exc()

            # Write an error result JSON so n8n/Make.com can notify WhatsApp cleanly instead of hanging
            err_payload = {
                "job_id": path.stem,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": err_msg,
                "whatsapp_message": f"⚠️ Could not generate visual hook shot list for `{path.name}`:\n{err_msg}",
                "storyboard_filename": None,
            }
            try:
                raw_in = json.loads(path.read_text(encoding="utf-8-sig")) if path.suffix.lower() == ".json" else {}
                err_payload["sender"] = raw_in.get("sender") or raw_in.get("from") or ""
                err_payload["chat_id"] = raw_in.get("chat_id") or err_payload["sender"]
                err_payload["message_id"] = raw_in.get("message_id") or ""
            except Exception:
                pass

            err_out = cfg.output_dir / f"{path.stem}_error.json"
            err_out.write_text(json.dumps(err_payload, indent=2), encoding="utf-8")
            shutil.move(str(path), str(failed_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"))

    return processed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Watch BrollInput/ and generate visual hook shot lists + Flux storyboards.")
    ap.add_argument("--config", default=None, help="Path to config.json")
    ap.add_argument("--once", action="store_true", help="Process pending files once and exit")
    ap.add_argument("--mock-comfy", action="store_true", help="Use mock frames instead of ComfyUI (testing)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    print(f"[worker] Watching: {cfg.input_dir}")
    print(f"[worker] Output:   {cfg.output_dir}")
    print(f"[worker] Ollama:   {cfg.ollama.base_url} ({cfg.ollama.model})")
    print(f"[worker] ComfyUI:  {cfg.comfyui.base_url} ({cfg.comfyui.workflow_template})")

    if args.once:
        count = process_once(cfg, mock_comfy=args.mock_comfy)
        print(f"[worker] Processed {count} job(s).")
        return 0

    try:
        while True:
            process_once(cfg, mock_comfy=args.mock_comfy)
            time.sleep(cfg.poll_interval_seconds)
    except KeyboardInterrupt:
        print("\n[worker] Stopped cleanly.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
