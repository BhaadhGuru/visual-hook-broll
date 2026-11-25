"""Single-job WhatsApp bridge entrypoint for local Baileys / CLI callers.

Runs one Visual Hook B-Roll or News SOT job without waiting for Google Drive
folder sync, writes outputs to `cfg.output_dir` (or `--output-dir`), and prints
a single machine-readable JSON line to stdout as its final line.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .news_engine import run_news_package
from .pipeline import run_job
from .watchdog import WatchdogSupervisor, parse_command

_TRIGGER_PREFIX_RE = re.compile(
    r"^(?:(?:#broll|#hook)\b|(?:visual\s+hook\s+)?b-?roll\s*[:：]|storyboard\s*[:：]|shot\s*list\s*[:：]|shotlist\s*[:：])\s*",
    re.IGNORECASE,
)


def strip_whatsapp_trigger(raw_text: str) -> tuple[str, bool]:
    """Strip leading WhatsApp routing triggers while keeping inline hashtags intact.

    Returns `(cleaned_script, is_news)`.
    """
    cleaned = _TRIGGER_PREFIX_RE.sub("", raw_text.strip(), count=1).strip()
    is_news = bool(re.search(r"#news\b", cleaned, re.IGNORECASE))
    return cleaned, is_news


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one Visual Hook B-Roll or News SOT job and emit JSON on stdout.")
    ap.add_argument("--text", default=None, help="Raw WhatsApp script text")
    ap.add_argument("--text-file", default=None, help="Path to UTF-8 text file containing the script")
    ap.add_argument("--sender", default="", help="WhatsApp sender JID / phone number")
    ap.add_argument("--job-id", default=None, help="Optional explicit job/package ID")
    ap.add_argument("--config", default=None, help="Optional config.json path")
    ap.add_argument("--output-dir", default=None, help="Override output_dir (e.g., D:\\Project\\visual-hook-broll\\BrollOutput)")
    ap.add_argument("--mock-comfy", action="store_true", help="Use wireframe placeholder panels instead of calling ComfyUI")
    args = ap.parse_args(argv)

    try:
        raw_text = args.text or ""
        if not raw_text and args.text_file:
            raw_text = Path(args.text_file).read_text(encoding="utf-8-sig")
        if not raw_text.strip():
            raise ValueError("Input script text is empty.")

        cfg = load_config(args.config)
        job_id = args.job_id or f"wa_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        cmd = parse_command(raw_text)
        if cmd is not None:
            action, target = cmd
            reply = WatchdogSupervisor(cfg).execute_command(action, target)
            payload: dict[str, Any] = {
                "ok": True,
                "mode": "watchdog",
                "job_id": job_id,
                "whatsapp_message": reply,
                "whatsapp_image_caption": "",
                "storyboard_path": None,
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        cleaned_script, is_news = strip_whatsapp_trigger(raw_text)
        if not cleaned_script:
            raise ValueError("Script text is empty after stripping trigger prefix.")

        if args.output_dir:
            cfg.output_dir = Path(args.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)

        if is_news:
            manifest = run_news_package(
                package_id=job_id,
                script_text=cleaned_script,
                cfg=cfg,
                prompts_only=not cfg.news_engine.auto_run_veo,
                sender=args.sender,
                chat_id=args.sender,
            )
            payload: dict[str, Any] = {
                "ok": True,
                "mode": "news",
                "job_id": manifest["job_id"],
                "whatsapp_message": manifest["whatsapp_message"],
                "whatsapp_image_caption": "",
                "storyboard_path": None,
                "resolve_package_dir": manifest.get("resolve_package_dir"),
            }
        else:
            manifest = run_job(
                job_id=job_id,
                script_text=cleaned_script,
                cfg=cfg,
                sender=args.sender,
                chat_id=args.sender,
                mock_comfy=args.mock_comfy,
            )
            payload = {
                "ok": True,
                "mode": "broll",
                "job_id": manifest["job_id"],
                "whatsapp_message": manifest["whatsapp_message"],
                "whatsapp_image_caption": manifest.get("whatsapp_image_caption", ""),
                "storyboard_path": manifest.get("storyboard_path"),
            }

        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
