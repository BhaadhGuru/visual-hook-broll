"""Zero-config auto-detector for Windows setup (Drive folders, Ollama, ComfyUI Flux, Yap & MediaMTX)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import string
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PREFERRED_OLLAMA_PREFIXES = (
    "qwen2.5:14b",
    "qwen2.5:7b",
    "qwen2.5",
    "qwen3",
    "llama3.1",
    "llama3.2",
    "llama3",
    "gemma",
    "mistral",
)

EMBEDDING_HINTS = ("embed", "nomic-embed", "mxbai-embed", "bge-", "snowflake-arctic-embed")


def _http_get_json(url: str, timeout: float = 4.0) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_google_drive_root(candidates: list[Path] | None = None) -> Path | None:
    """Locate the local Google Drive 'My Drive' mount on Windows (or custom candidates)."""
    if candidates is None:
        candidates = []
        # Check G: first (most common Google Drive for Desktop letter), then other letters
        letters = ["G", "H", "I", "D", "E", "F"] + [
            ch for ch in string.ascii_uppercase if ch not in {"A", "B", "C", "G", "H", "I", "D", "E", "F"}
        ]
        for letter in letters:
            candidates.append(Path(f"{letter}:\\My Drive"))
        home = Path.home()
        candidates.extend(
            [
                home / "Google Drive" / "My Drive",
                home / "Google Drive",
                home / "My Drive",
            ]
        )

    for path in candidates:
        try:
            if path.exists() and path.is_dir():
                return path
        except OSError:
            continue
    return None


def pick_ollama_model(models: list[str]) -> str | None:
    """Pick the best installed Ollama chat model from a list of model names."""
    chat_models = [m for m in models if not any(h in m.lower() for h in EMBEDDING_HINTS)]
    if not chat_models:
        return models[0] if models else None
    lower_map = [(m, m.lower()) for m in chat_models]
    for prefix in PREFERRED_OLLAMA_PREFIXES:
        for orig, low in lower_map:
            if low.startswith(prefix):
                return orig
    return chat_models[0]


def detect_ollama(base_url: str = "http://127.0.0.1:11434") -> dict[str, Any]:
    """Query local Ollama /api/tags and select the best available model."""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        data = _http_get_json(url, timeout=4.0)
        names = [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        chosen = pick_ollama_model(names)
        return {"reachable": True, "models": names, "selected_model": chosen}
    except Exception as exc:
        return {"reachable": False, "models": [], "selected_model": None, "error": str(exc)}


def _extract_combo_options(node_info: dict[str, Any] | None, field_name: str) -> list[str]:
    if not isinstance(node_info, dict):
        return []
    req = node_info.get("input", {}).get("required", {})
    spec = req.get(field_name)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [str(x) for x in spec[0]]
    return []


def _pick_first_matching(items: list[str], keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        for item in items:
            if kw in item.lower():
                return item
    return items[0] if items else None


def detect_flux_from_object_info(info: dict[str, Any]) -> dict[str, Any]:
    """Inspect ComfyUI /object_info dict to pick the matching Flux template and model files."""
    ckpts = _extract_combo_options(info.get("CheckpointLoaderSimple"), "ckpt_name")
    unets = _extract_combo_options(info.get("UNETLoader"), "unet_name")
    ggufs = _extract_combo_options(info.get("UnetLoaderGGUF"), "unet_name")
    clips = _extract_combo_options(info.get("DualCLIPLoader"), "clip_name1")
    vaes = _extract_combo_options(info.get("VAELoader"), "vae_name")

    flux_ckpts = [c for c in ckpts if "flux" in c.lower()]
    flux_unets = [u for u in unets if "flux" in u.lower()]
    flux_ggufs = [g for g in ggufs if "flux" in g.lower()]

    # 1. All-in-one Flux checkpoint (simplest)
    if flux_ckpts:
        chosen = _pick_first_matching(flux_ckpts, ("dev-fp8", "dev", "schnell")) or flux_ckpts[0]
        is_schnell = "schnell" in chosen.lower()
        return {
            "layout": "checkpoint",
            "workflow_template": (
                "comfyui_workflows/flux_schnell_api.json" if is_schnell else "comfyui_workflows/flux_dev_fp8_api.json"
            ),
            "checkpoint_name": chosen,
            "steps": 4 if is_schnell else 20,
            "guidance": 3.5,
        }

    clip_l = _pick_first_matching(clips, ("clip_l", "clip-l")) or "clip_l.safetensors"
    t5 = _pick_first_matching(clips, ("t5xxl_fp8", "t5xxl", "t5")) or "t5xxl_fp8_e4m3fn.safetensors"
    vae = _pick_first_matching(vaes, ("ae.safetensors", "flux", "ae")) or "ae.safetensors"

    # 2. Split-file Flux UNET + DualCLIP + VAE
    if flux_unets:
        chosen = _pick_first_matching(flux_unets, ("dev-fp8", "dev", "schnell")) or flux_unets[0]
        is_schnell = "schnell" in chosen.lower()
        weight_dtype = "fp8_e4m3fn" if "fp8" in chosen.lower() else "default"
        return {
            "layout": "unet_dualclip",
            "workflow_template": "comfyui_workflows/flux_unet_dualclip_api.json",
            "unet_name": chosen,
            "weight_dtype": weight_dtype,
            "clip_l_name": clip_l,
            "t5_name": t5,
            "vae_name": vae,
            "steps": 4 if is_schnell else 20,
            "guidance": 3.5,
        }

    # 3. GGUF Flux UNET + DualCLIP + VAE
    if flux_ggufs:
        chosen = _pick_first_matching(flux_ggufs, ("dev", "q8", "q6", "q4", "schnell")) or flux_ggufs[0]
        is_schnell = "schnell" in chosen.lower()
        return {
            "layout": "gguf",
            "workflow_template": "comfyui_workflows/flux_gguf_api.json",
            "unet_name": chosen,
            "clip_l_name": clip_l,
            "t5_name": t5,
            "vae_name": vae,
            "steps": 4 if is_schnell else 20,
            "guidance": 3.5,
        }

    return {"layout": "not_found", "available_checkpoints": ckpts[:5], "available_unets": unets[:5]}


def detect_comfyui(base_url: str = "http://127.0.0.1:8188") -> dict[str, Any]:
    """Query local ComfyUI /object_info for Flux loaders and models."""
    base = base_url.rstrip("/")
    nodes = ("CheckpointLoaderSimple", "UNETLoader", "UnetLoaderGGUF", "DualCLIPLoader", "VAELoader")
    info: dict[str, Any] = {}
    reachable = False
    last_err = ""
    for node in nodes:
        try:
            data = _http_get_json(f"{base}/object_info/{node}", timeout=4.0)
            reachable = True
            if isinstance(data, dict):
                info.update(data)
        except urllib.error.HTTPError:
            reachable = True
        except Exception as exc:
            last_err = str(exc)
            if not reachable:
                return {"reachable": False, "layout": "unreachable", "error": last_err}
    result = detect_flux_from_object_info(info)
    result["reachable"] = reachable
    return result


def scan_for_scripts(
    search_roots: list[Path] | None = None,
    max_depth: int = 3,
) -> dict[str, str]:
    """Scan common Windows folders for existing Yap .bat and MediaMTX .bat/.exe files."""
    if search_roots is None:
        home = Path.home()
        search_roots = [
            Path("C:\\Projects"),
            Path("D:\\Projects"),
            Path("C:\\mediamtx"),
            Path("C:\\MediaMTX"),
            Path("C:\\Tools"),
            home / "Desktop",
            home / "Documents",
        ]

    yap_bat = ""
    mediamtx_bat = ""
    mediamtx_exe = ""

    def _walk(root: Path, depth: int) -> None:
        nonlocal yap_bat, mediamtx_bat, mediamtx_exe
        if depth > max_depth:
            return
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        for entry in entries:
            name_low = entry.name.lower()
            if entry.is_file():
                if not yap_bat and name_low.endswith(".bat") and "yap" in name_low:
                    yap_bat = str(entry)
                if not mediamtx_bat and name_low.endswith(".bat") and ("mediamtx" in name_low or "livestream" in name_low):
                    mediamtx_bat = str(entry)
                if not mediamtx_exe and name_low == "mediamtx.exe":
                    mediamtx_exe = str(entry)
            elif entry.is_dir() and not name_low.startswith(".") and name_low not in {
                "node_modules",
                ".venv",
                "venv",
                "__pycache__",
                "models",
                "bat_backups",
            }:
                _walk(entry, depth + 1)

    for root in search_roots:
        if root.exists() and root.is_dir():
            _walk(root, 0)

    return {
        "yap_bat": yap_bat,
        "mediamtx_bat": mediamtx_bat or mediamtx_exe,
    }


def backup_bat_scripts(project_root: Path, backup_dir: Path, extra_paths: list[str] | None = None) -> list[str]:
    """Copy project and discovered .bat scripts to Google Drive AI stuff/visual-hook-broll/bat_backups."""
    copied: list[str] = []
    backup_dir.mkdir(parents=True, exist_ok=True)
    sources: list[Path] = list(project_root.glob("*.bat"))
    for raw in extra_paths or []:
        if raw:
            p = Path(raw)
            if p.exists() and p.is_file() and p.suffix.lower() == ".bat":
                sources.append(p)
    seen: set[str] = set()
    for src in sources:
        key = str(src.resolve())
        if key in seen:
            continue
        seen.add(key)
        dest = backup_dir / src.name
        try:
            shutil.copy2(src, dest)
            copied.append(str(dest))
        except OSError:
            continue
    return copied


def build_detected_config(
    project_root: Path,
    *,
    drive_root: Path | None = None,
    ollama_info: dict[str, Any] | None = None,
    comfy_info: dict[str, Any] | None = None,
    scripts_info: dict[str, str] | None = None,
    existing_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge auto-detected values onto config.example.json (preserving existing user overrides)."""
    example_path = project_root / "config.example.json"
    cfg: dict[str, Any] = json.loads(example_path.read_text(encoding="utf-8")) if example_path.exists() else {}
    existing = existing_config or {}

    if drive_root is not None:
        broll_in = drive_root / "BrollInput"
        broll_out = drive_root / "BrollOutput"
        yap_in = drive_root / "YapInput"
        yap_out = drive_root / "YapOutput"
        backup_dir = drive_root / "AI stuff" / "visual-hook-broll" / "bat_backups"
        cfg["input_dir"] = str(broll_in)
        cfg["output_dir"] = str(broll_out)
        cfg["archive_dir"] = str(broll_in / "_processed")
        cfg.setdefault("news_engine", {})["yap_output_dir"] = str(yap_out)
        cfg.setdefault("watchdog", {})["yap_input_dir"] = str(yap_in)
        cfg["watchdog"]["bat_backup_dir"] = str(backup_dir)

    if ollama_info and ollama_info.get("selected_model"):
        cfg.setdefault("ollama", {})["model"] = ollama_info["selected_model"]

    if comfy_info and comfy_info.get("layout") in {"checkpoint", "unet_dualclip", "gguf"}:
        comfy = cfg.setdefault("comfyui", {})
        for key in (
            "workflow_template",
            "checkpoint_name",
            "unet_name",
            "weight_dtype",
            "clip_l_name",
            "t5_name",
            "vae_name",
            "steps",
            "guidance",
        ):
            if key in comfy_info and comfy_info[key]:
                comfy[key] = comfy_info[key]

    wd = cfg.setdefault("watchdog", {})
    existing_wd = existing.get("watchdog", {}) if isinstance(existing, dict) else {}
    if scripts_info:
        if scripts_info.get("yap_bat") and not existing_wd.get("yap_bat"):
            wd["yap_bat"] = scripts_info["yap_bat"]
        if scripts_info.get("mediamtx_bat") and not existing_wd.get("mediamtx_bat"):
            wd["mediamtx_bat"] = scripts_info["mediamtx_bat"]
    for keep_key in ("yap_bat", "mediamtx_bat", "notify_phone"):
        if existing_wd.get(keep_key):
            wd[keep_key] = existing_wd[keep_key]

    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Auto-detect local Windows setup and write config.json.")
    ap.add_argument("--write", action="store_true", help="Write detected config.json and back up .bat scripts")
    ap.add_argument("--yap-bat", default="", help="Optional explicit path to Yap .bat script")
    ap.add_argument("--mediamtx-bat", default="", help="Optional explicit path to MediaMTX .bat or .exe")
    ap.add_argument("--notify-phone", default="", help="Optional WhatsApp phone number for crash alerts")
    args = ap.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    cfg_path = project_root / "config.json"
    existing = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    drive_root = find_google_drive_root()
    ollama_info = detect_ollama(existing.get("ollama", {}).get("base_url", "http://127.0.0.1:11434"))
    comfy_info = detect_comfyui(existing.get("comfyui", {}).get("base_url", "http://127.0.0.1:8188"))
    scripts_info = scan_for_scripts()

    if args.yap_bat:
        scripts_info["yap_bat"] = args.yap_bat
    if args.mediamtx_bat:
        scripts_info["mediamtx_bat"] = args.mediamtx_bat

    cfg = build_detected_config(
        project_root,
        drive_root=drive_root,
        ollama_info=ollama_info,
        comfy_info=comfy_info,
        scripts_info=scripts_info,
        existing_config=existing,
    )
    if args.notify_phone:
        cfg["watchdog"]["notify_phone"] = args.notify_phone

    print("=== Auto-Detection Summary ===")
    print(f"Google Drive : {drive_root or 'NOT FOUND (using local folders)'}")
    print(
        f"Ollama       : {'OK -> ' + str(ollama_info.get('selected_model')) if ollama_info.get('reachable') else 'NOT REACHABLE (start Ollama)'}"
    )
    if comfy_info.get("reachable"):
        print(f"ComfyUI Flux : OK ({comfy_info.get('layout')}) -> {comfy_info.get('workflow_template', 'not found')}")
    else:
        print("ComfyUI Flux : NOT REACHABLE (start ComfyUI)")
    print(f"Yap .bat     : {cfg['watchdog'].get('yap_bat') or 'not found yet (can set anytime)'}")
    print(f"MediaMTX     : {cfg['watchdog'].get('mediamtx_bat') or 'not found yet (can set anytime)'}")

    if args.write:
        if drive_root is not None:
            for folder in (cfg["input_dir"], cfg["output_dir"], cfg["watchdog"]["yap_input_dir"]):
                try:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\nSaved config -> {cfg_path}")

        backup_dir_str = cfg.get("watchdog", {}).get("bat_backup_dir", "")
        if backup_dir_str and drive_root is not None:
            copied = backup_bat_scripts(
                project_root,
                Path(backup_dir_str),
                [cfg["watchdog"].get("yap_bat", ""), cfg["watchdog"].get("mediamtx_bat", "")],
            )
            if copied:
                print(f"Backed up {len(copied)} .bat script(s) to Drive -> {backup_dir_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
