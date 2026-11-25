"""Zero-config local environment auto-detection for visual-hook-broll.

Detects:
1. Google Drive Desktop mount (`My Drive\\BrollInput`, `BrollOutput`, `YapOutput`)
   and DaVinci Resolve watched folder (`D:\\ResolveWatch\\NewsPackages`).
2. Local Ollama server (`/api/tags`) and selects the best available text/JSON
   director model (filtering out vision-only and embedding models).
3. Local ComfyUI server (`/system_stats` and `/object_info/*`) and auto-selects
   the matching Flux workflow template (single-file checkpoint, split UNET +
   DualCLIP + VAE, or GGUF `UnetLoaderGGUF`).

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EXCLUDED_OLLAMA_PREFIXES = (
    "moondream",
    "llava",
    "bakllava",
    "minicpm-v",
    "nomic-embed",
    "mxbai-embed",
    "bge-",
    "all-minilm",
    "snowflake-arctic-embed",
)

OLLAMA_MODEL_PRIORITY = (
    "qwen2.5:14b",
    "qwen3:14b",
    "qwen2.5:7b",
    "qwen3:8b",
    "qwen2.5-coder:7b",
    "qwen2.5:32b",
    "llama3.1:8b",
    "llama3.2",
    "qwen2.5:3b",
    "mistral",
    "gemma3",
    "gemma2",
    "deepseek-r1",
    "phi4",
    "phi3",
)

COMFY_NODE_CLASSES = (
    "CheckpointLoaderSimple",
    "UNETLoader",
    "UnetLoaderGGUF",
    "DualCLIPLoader",
    "DualCLIPLoaderGGUF",
    "VAELoader",
)


@dataclass
class DriveDetectionResult:
    input_dir: Path
    output_dir: Path
    archive_dir: Path
    yap_output_dir: Path
    resolve_watch_dir: Path
    is_google_drive: bool
    drive_root: Path | None = None
    input_exists: bool = False
    output_exists: bool = False
    warning: str = ""


@dataclass
class OllamaDetectionResult:
    base_url: str
    reachable: bool
    installed_models: list[str] = field(default_factory=list)
    selected_model: str | None = None
    warning: str = ""
    error: str = ""


@dataclass
class FluxLayoutSelection:
    layout: str  # "checkpoint" | "unet_dualclip" | "gguf" | "not_found"
    workflow_template: str = ""
    checkpoint_name: str = ""
    unet_name: str = ""
    weight_dtype: str = "fp8_e4m3fn"
    clip_l_name: str = ""
    t5_name: str = ""
    vae_name: str = ""
    steps: int = 20
    warnings: list[str] = field(default_factory=list)
    details: str = ""


@dataclass
class ComfyDetectionResult:
    base_url: str
    reachable: bool
    gpu_name: str = ""
    vram_total_mb: int | None = None
    layout: FluxLayoutSelection = field(default_factory=lambda: FluxLayoutSelection(layout="not_found"))
    error: str = ""


@dataclass
class SetupReport:
    config_path: Path
    wrote_config: bool
    drive: DriveDetectionResult
    ollama: OllamaDetectionResult
    comfyui: ComfyDetectionResult
    config_data: dict[str, Any]


def default_drive_search_roots() -> list[Path]:
    """Candidate Google Drive 'My Drive' locations on Windows and desktop hosts."""
    return [
        Path(r"G:\My Drive"),
        Path(r"H:\My Drive"),
        Path(r"F:\My Drive"),
        Path(r"E:\My Drive"),
        Path(r"D:\My Drive"),
        Path(r"I:\My Drive"),
        Path.home() / "Google Drive" / "My Drive",
        Path.home() / "My Drive",
    ]


def detect_drive_folders(
    project_root: Path,
    *,
    search_roots: list[Path] | None = None,
    input_override: str | None = None,
    output_override: str | None = None,
    resolve_root_override: Path | None = None,
) -> DriveDetectionResult:
    """Locate Google Drive BrollInput/BrollOutput/YapOutput or fall back locally."""
    roots = search_roots if search_roots is not None else default_drive_search_roots()

    existing_roots = [r for r in roots if r.exists() and r.is_dir()]
    chosen_drive_root: Path | None = None
    for root in existing_roots:
        if (root / "BrollInput").exists() or (root / "BrollOutput").exists():
            chosen_drive_root = root
            break
    if chosen_drive_root is None and existing_roots:
        chosen_drive_root = existing_roots[0]

    warning = ""
    if chosen_drive_root is not None:
        is_gdrive = True
        input_dir = chosen_drive_root / "BrollInput"
        output_dir = chosen_drive_root / "BrollOutput"
        yap_output_dir = chosen_drive_root / "YapOutput"
    else:
        is_gdrive = False
        input_dir = project_root / "BrollInput"
        output_dir = project_root / "BrollOutput"
        yap_output_dir = project_root / "YapOutput"
        warning = (
            "Google Drive desktop mount ('My Drive') was not found; "
            "using local project folders as fallback."
        )

    if input_override:
        input_dir = Path(input_override)
    if output_override:
        output_dir = Path(output_override)

    archive_dir = input_dir / "_processed"

    if resolve_root_override is not None:
        resolve_watch_dir = resolve_root_override / "ResolveWatch" / "NewsPackages"
    elif Path("D:\\").exists():
        resolve_watch_dir = Path(r"D:\ResolveWatch\NewsPackages")
    else:
        resolve_watch_dir = project_root / "ResolveWatch" / "NewsPackages"

    return DriveDetectionResult(
        input_dir=input_dir,
        output_dir=output_dir,
        archive_dir=archive_dir,
        yap_output_dir=yap_output_dir,
        resolve_watch_dir=resolve_watch_dir,
        is_google_drive=is_gdrive,
        drive_root=chosen_drive_root,
        input_exists=input_dir.exists(),
        output_exists=output_dir.exists(),
        warning=warning,
    )


def _http_get_json(url: str, timeout: int = 5) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def _is_excluded_ollama_model(model_name: str) -> bool:
    lower = model_name.strip().lower()
    return any(lower == prefix or lower.startswith(prefix) for prefix in EXCLUDED_OLLAMA_PREFIXES)


def _matches_model_pattern(model_name: str, pattern: str) -> bool:
    m = model_name.strip().lower()
    p = pattern.strip().lower()
    if m == p:
        return True
    if ":" not in p and m == f"{p}:latest":
        return True
    return m.startswith(f"{p}:") or m.startswith(f"{p}-") or m.startswith(f"{p}_")


def choose_best_ollama_model(
    installed_models: list[str],
    preferred: str | None = None,
) -> str | None:
    """Choose the best installed Ollama text/JSON model, skipping vision/embed models."""
    cleaned = [m.strip() for m in installed_models if m and m.strip()]
    if not cleaned:
        return None

    if preferred:
        pref_clean = preferred.strip()
        for model in cleaned:
            if model.lower() == pref_clean.lower():
                return model
        for model in cleaned:
            if _matches_model_pattern(model, pref_clean):
                return model

    candidates = [m for m in cleaned if not _is_excluded_ollama_model(m)]
    if not candidates:
        return None

    # Pass 1: exact matches against the ranked priority list
    for pat in OLLAMA_MODEL_PRIORITY:
        for model in candidates:
            if model.lower() == pat.lower() or (":" not in pat and model.lower() == f"{pat.lower()}:latest"):
                return model

    # Pass 2: boundary prefix matches (e.g., llama3.2:3b or qwen2.5:14b-instruct)
    for pat in OLLAMA_MODEL_PRIORITY:
        for model in candidates:
            if _matches_model_pattern(model, pat):
                return model

    return candidates[0]


def detect_ollama(
    base_url: str = "http://127.0.0.1:11434",
    *,
    preferred_model: str | None = None,
    timeout: int = 5,
) -> OllamaDetectionResult:
    """Probe local Ollama `/api/tags` and select the best JSON-capable model."""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        data = _http_get_json(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        return OllamaDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"Ollama HTTP {code} at {url}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return OllamaDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"Could not reach Ollama at {url}: {reason}",
        )
    except json.JSONDecodeError as exc:
        return OllamaDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"Invalid JSON from Ollama at {url}: {exc}",
        )

    raw_models = data.get("models", []) if isinstance(data, dict) else []
    installed = [
        str(entry["name"])
        for entry in raw_models
        if isinstance(entry, dict) and entry.get("name")
    ]
    selected = choose_best_ollama_model(installed, preferred=preferred_model)

    warning = ""
    if not installed:
        warning = "Ollama is running, but no models are installed yet (run: ollama pull qwen2.5:7b)."
    elif selected is None:
        warning = (
            f"Ollama has only vision/embedding models ({', '.join(installed)}). "
            "Pull a text/JSON director model such as: ollama pull qwen2.5:7b"
        )
    elif preferred_model and not _matches_model_pattern(selected, preferred_model):
        warning = f"Configured model '{preferred_model}' not installed; selected '{selected}'."

    return OllamaDetectionResult(
        base_url=base_url,
        reachable=True,
        installed_models=installed,
        selected_model=selected,
        warning=warning,
    )


def extract_combo_options(object_info: dict[str, Any], node_name: str, input_name: str) -> list[str]:
    """Extract combo dropdown choices from ComfyUI classic or V3 `/object_info` schemas."""
    node = object_info.get(node_name)
    if not isinstance(node, dict):
        return []
    input_block = node.get("input")
    if not isinstance(input_block, dict):
        return []

    spec: Any = None
    for section in ("required", "optional"):
        sec_dict = input_block.get(section)
        if isinstance(sec_dict, dict) and input_name in sec_dict:
            spec = sec_dict[input_name]
            break

    if spec is None:
        return []

    # Classic ComfyUI: [["a", "b"], {...}]
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return [str(item) for item in spec[0]]
        # ComfyUI V3: ["COMBO", {"options": ["a", "b"], ...}]
        if (
            len(spec) >= 2
            and str(spec[0]).upper() == "COMBO"
            and isinstance(spec[1], dict)
            and isinstance(spec[1].get("options"), list)
        ):
            return [str(item) for item in spec[1]["options"]]

    if isinstance(spec, dict) and isinstance(spec.get("options"), list):
        return [str(item) for item in spec["options"]]

    return []


def _pick_preferred(items: list[str], patterns: tuple[str, ...]) -> str:
    lowered = [(item, item.lower()) for item in items]
    for pat in patterns:
        pat_l = pat.lower()
        for orig, low in lowered:
            if pat_l in low:
                return orig
    return ""


def _pick_flux_clips_and_vae(
    object_info: dict[str, Any],
    *,
    allow_gguf_clip: bool,
) -> tuple[str, str, str, list[str]]:
    warnings: list[str] = []

    clip1 = extract_combo_options(object_info, "DualCLIPLoader", "clip_name1")
    clip2 = extract_combo_options(object_info, "DualCLIPLoader", "clip_name2")
    dual_clips = list(dict.fromkeys(clip1 + clip2))

    gguf_clips: list[str] = []
    if allow_gguf_clip:
        g1 = extract_combo_options(object_info, "DualCLIPLoaderGGUF", "clip_name1")
        g2 = extract_combo_options(object_info, "DualCLIPLoaderGGUF", "clip_name2")
        gguf_clips = list(dict.fromkeys(g1 + g2))

    clip_l = _pick_preferred(dual_clips, ("clip_l", "clip-l"))
    if not clip_l and gguf_clips:
        clip_l = _pick_preferred(gguf_clips, ("clip_l", "clip-l"))

    t5 = _pick_preferred(dual_clips, ("t5xxl_fp8_e4m3fn", "t5xxl_fp8", "t5xxl", "t5"))
    if not t5 and gguf_clips:
        t5 = _pick_preferred(gguf_clips, ("t5xxl_fp8_e4m3fn", "t5xxl_fp8", "t5xxl", "t5"))
        if t5 and t5.lower().endswith(".gguf"):
            warnings.append(
                f"Detected GGUF T5 encoder '{t5}' from DualCLIPLoaderGGUF; "
                "ensure DualCLIPLoader in your workflow supports it or add a .safetensors T5."
            )

    vaes = extract_combo_options(object_info, "VAELoader", "vae_name")
    vae = _pick_preferred(vaes, ("ae.safetensors", "ae.", "flux"))
    if not vae and vaes:
        vae = vaes[0]
        warnings.append(f"No Flux 'ae.safetensors' VAE found; falling back to '{vae}'.")

    if not clip_l:
        warnings.append("Missing clip_l text encoder (clip_l.safetensors) in ComfyUI text_encoders/clip folder.")
    if not t5:
        warnings.append("Missing T5 text encoder (t5xxl_fp8_e4m3fn.safetensors) in ComfyUI text_encoders/clip folder.")
    if not vae:
        warnings.append("Missing Flux VAE (ae.safetensors) in ComfyUI models/vae folder.")

    return clip_l, t5, vae, warnings


def select_flux_layout(object_info: dict[str, Any]) -> FluxLayoutSelection:
    """Determine the active Flux workflow layout from ComfyUI `/object_info`."""
    # Layout 1: Single-file checkpoint (CheckpointLoaderSimple)
    ckpts = extract_combo_options(object_info, "CheckpointLoaderSimple", "ckpt_name")
    flux_ckpts = [c for c in ckpts if "flux" in c.lower()]
    if flux_ckpts:
        chosen = _pick_preferred(
            flux_ckpts,
            ("flux1-dev-fp8", "flux1-dev", "flux-dev", "flux1-schnell", "flux-schnell", "flux"),
        )
        is_schnell = "schnell" in chosen.lower()
        return FluxLayoutSelection(
            layout="checkpoint",
            workflow_template=(
                "comfyui_workflows/flux_schnell_api.json"
                if is_schnell
                else "comfyui_workflows/flux_dev_fp8_api.json"
            ),
            checkpoint_name=chosen,
            steps=4 if is_schnell else 20,
            details=f"Single-file checkpoint '{chosen}' via CheckpointLoaderSimple",
        )

    # Layout 2: Split-file UNETLoader + DualCLIPLoader + VAELoader
    unets = extract_combo_options(object_info, "UNETLoader", "unet_name")
    flux_unets = [u for u in unets if "flux" in u.lower() and not u.lower().endswith(".gguf")]
    if flux_unets:
        chosen_unet = _pick_preferred(
            flux_unets,
            ("flux1-dev-fp8", "flux1-dev", "flux-dev", "flux1-schnell", "flux-schnell", "flux"),
        )
        dtypes = extract_combo_options(object_info, "UNETLoader", "weight_dtype")
        if "e5m2" in chosen_unet.lower() and (not dtypes or "fp8_e5m2" in dtypes):
            weight_dtype = "fp8_e5m2"
        elif "fp8" in chosen_unet.lower() and (not dtypes or "fp8_e4m3fn" in dtypes):
            weight_dtype = "fp8_e4m3fn"
        elif "default" in dtypes:
            weight_dtype = "default"
        elif "fp8_e4m3fn" in dtypes:
            weight_dtype = "fp8_e4m3fn"
        elif dtypes:
            weight_dtype = dtypes[0]
        else:
            weight_dtype = "fp8_e4m3fn"

        clip_l, t5, vae, clip_warnings = _pick_flux_clips_and_vae(object_info, allow_gguf_clip=False)
        is_schnell = "schnell" in chosen_unet.lower()
        return FluxLayoutSelection(
            layout="unet_dualclip",
            workflow_template="comfyui_workflows/flux_unet_dualclip_api.json",
            unet_name=chosen_unet,
            weight_dtype=weight_dtype,
            clip_l_name=clip_l or "clip_l.safetensors",
            t5_name=t5 or "t5xxl_fp8_e4m3fn.safetensors",
            vae_name=vae or "ae.safetensors",
            steps=4 if is_schnell else 20,
            warnings=clip_warnings,
            details=f"Split-file Flux UNET '{chosen_unet}' ({weight_dtype}) + DualCLIPLoader + VAELoader",
        )

    # Layout 3: GGUF Flux via UnetLoaderGGUF + DualCLIPLoader + VAELoader
    ggufs = extract_combo_options(object_info, "UnetLoaderGGUF", "unet_name")
    flux_ggufs = [g for g in ggufs if "flux" in g.lower()]
    if flux_ggufs:
        chosen_gguf = _pick_preferred(
            flux_ggufs,
            ("q4_k_s", "q4_k_m", "q8_0", "q5_k_s", "q5_k_m", "q6_k", "q4_0", "q3_k", "f16", "flux"),
        )
        clip_l, t5, vae, clip_warnings = _pick_flux_clips_and_vae(object_info, allow_gguf_clip=True)
        is_schnell = "schnell" in chosen_gguf.lower()
        return FluxLayoutSelection(
            layout="gguf",
            workflow_template="comfyui_workflows/flux_gguf_api.json",
            unet_name=chosen_gguf,
            clip_l_name=clip_l or "clip_l.safetensors",
            t5_name=t5 or "t5xxl_fp8_e4m3fn.safetensors",
            vae_name=vae or "ae.safetensors",
            steps=4 if is_schnell else 20,
            warnings=clip_warnings,
            details=f"GGUF Flux '{chosen_gguf}' via UnetLoaderGGUF + DualCLIPLoader + VAELoader",
        )

    return FluxLayoutSelection(
        layout="not_found",
        details=(
            "No Flux model found in CheckpointLoaderSimple, UNETLoader, or UnetLoaderGGUF. "
            "Place a Flux model in ComfyUI/models/checkpoints, models/unet, or models/diffusion_models."
        ),
    )


def detect_comfyui(
    base_url: str = "http://127.0.0.1:8188",
    *,
    timeout: int = 5,
) -> ComfyDetectionResult:
    """Probe local ComfyUI server and detect GPU stats plus Flux model layout."""
    root = base_url.rstrip("/")
    try:
        stats = _http_get_json(f"{root}/system_stats", timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        return ComfyDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"ComfyUI HTTP {code} at {root}/system_stats",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return ComfyDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"Could not reach ComfyUI at {root}: {reason}",
        )
    except json.JSONDecodeError as exc:
        return ComfyDetectionResult(
            base_url=base_url,
            reachable=False,
            error=f"Invalid JSON from ComfyUI at {root}/system_stats: {exc}",
        )

    gpu_name = ""
    vram_total_mb: int | None = None
    devices = stats.get("devices", []) if isinstance(stats, dict) else []
    if devices and isinstance(devices[0], dict):
        gpu_name = str(devices[0].get("name", ""))
        vram_bytes = devices[0].get("vram_total")
        if isinstance(vram_bytes, (int, float)) and vram_bytes > 0:
            vram_total_mb = int(vram_bytes // (1024 * 1024))

    object_info: dict[str, Any] = {}
    for node_cls in COMFY_NODE_CLASSES:
        try:
            node_payload = _http_get_json(f"{root}/object_info/{node_cls}", timeout=timeout)
            if isinstance(node_payload, dict):
                if node_cls in node_payload:
                    object_info[node_cls] = node_payload[node_cls]
                elif "input" in node_payload:
                    object_info[node_cls] = node_payload
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404:
                continue
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue

    if not object_info:
        try:
            full_info = _http_get_json(f"{root}/object_info", timeout=max(timeout, 10))
            if isinstance(full_info, dict):
                object_info = full_info
        except urllib.error.HTTPError as exc:
            exc.close()
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass

    layout = select_flux_layout(object_info)
    return ComfyDetectionResult(
        base_url=base_url,
        reachable=True,
        gpu_name=gpu_name,
        vram_total_mb=vram_total_mb,
        layout=layout,
    )


def build_config_dict(
    project_root: Path,
    *,
    existing_config: dict[str, Any] | None,
    drive: DriveDetectionResult,
    ollama: OllamaDetectionResult,
    comfyui: ComfyDetectionResult,
) -> dict[str, Any]:
    """Merge auto-detected settings on top of existing/example config without clobbering user keys."""
    if existing_config is not None:
        merged = copy.deepcopy(existing_config)
    else:
        example_path = project_root / "config.example.json"
        if example_path.exists():
            merged = json.loads(example_path.read_text(encoding="utf-8"))
        else:
            merged = {}

    merged["input_dir"] = str(drive.input_dir)
    merged["output_dir"] = str(drive.output_dir)
    merged["archive_dir"] = str(drive.archive_dir)
    merged.setdefault("poll_interval_seconds", 3.0)

    ollama_sec = merged.setdefault("ollama", {})
    ollama_sec["base_url"] = ollama.base_url
    if ollama.selected_model:
        ollama_sec["model"] = ollama.selected_model
    else:
        ollama_sec.setdefault("model", "qwen2.5:14b")
    ollama_sec.setdefault("timeout_seconds", 180)
    ollama_sec.setdefault("temperature", 0.4)
    ollama_sec.setdefault("unload_after_generation", True)

    comfy_sec = merged.setdefault("comfyui", {})
    comfy_sec["base_url"] = comfyui.base_url
    layout = comfyui.layout
    if comfyui.reachable and layout.layout != "not_found":
        comfy_sec["workflow_template"] = layout.workflow_template
        comfy_sec["steps"] = layout.steps
        if layout.layout == "checkpoint":
            comfy_sec["checkpoint_name"] = layout.checkpoint_name
            comfy_sec["unet_name"] = ""
        else:
            comfy_sec["unet_name"] = layout.unet_name
            if layout.layout == "unet_dualclip":
                comfy_sec["weight_dtype"] = layout.weight_dtype
            comfy_sec["clip_l_name"] = layout.clip_l_name
            comfy_sec["t5_name"] = layout.t5_name
            comfy_sec["vae_name"] = layout.vae_name
    else:
        comfy_sec.setdefault("workflow_template", "comfyui_workflows/flux_dev_fp8_api.json")
        comfy_sec.setdefault("checkpoint_name", "flux1-dev-fp8.safetensors")
        comfy_sec.setdefault("unet_name", "")
        comfy_sec.setdefault("weight_dtype", "fp8_e4m3fn")
        comfy_sec.setdefault("clip_l_name", "clip_l.safetensors")
        comfy_sec.setdefault("t5_name", "t5xxl_fp8_e4m3fn.safetensors")
        comfy_sec.setdefault("vae_name", "ae.safetensors")
        comfy_sec.setdefault("steps", 20)

    comfy_sec.setdefault("width", 1024)
    comfy_sec.setdefault("height", 576)
    comfy_sec.setdefault("cfg", 1.0)
    comfy_sec.setdefault("guidance", 3.5)
    comfy_sec.setdefault("timeout_seconds", 300)
    comfy_sec.setdefault("free_vram_after_job", True)

    sb_sec = merged.setdefault("storyboard", {})
    sb_sec.setdefault("default_panel_count", 4)
    sb_sec.setdefault("grid_columns", 2)
    sb_sec.setdefault("panel_width", 960)
    sb_sec.setdefault("panel_height", 540)
    sb_sec.setdefault("include_individual_panels", True)

    news_sec = merged.setdefault("news_engine", {})
    news_sec.setdefault("veo_provider", "vertex")
    news_sec.setdefault("veo_model", "veo-3.1-fast-generate-001")
    news_sec.setdefault("resolution", "1080p")
    news_sec.setdefault("aspect_ratio", "16:9")
    news_sec.setdefault("veo_duration_seconds", 8)
    news_sec.setdefault("veo_generate_audio", False)
    news_sec.setdefault("fps", 29.97)
    news_sec.setdefault("default_pacing_seconds", 4.0)
    news_sec.setdefault("vertex_project_id", "")
    news_sec.setdefault("vertex_location", "us-central1")
    news_sec.setdefault("auto_run_veo", False)
    if drive.drive_root is not None:
        news_sec["yap_output_dir"] = str(drive.yap_output_dir)
    else:
        news_sec.setdefault("yap_output_dir", str(drive.yap_output_dir))
    news_sec.setdefault("resolve_watch_dir", str(drive.resolve_watch_dir))

    return merged


def write_config(config_path: Path, config_data: dict[str, Any]) -> Path:
    """Atomically write JSON configuration to disk."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(config_path)
    return config_path


def run_setup_detect(
    *,
    project_root: Path | None = None,
    config_path: Path | None = None,
    ollama_url: str = "http://127.0.0.1:11434",
    comfyui_url: str = "http://127.0.0.1:8188",
    input_dir: str | None = None,
    output_dir: str | None = None,
    ollama_model: str | None = None,
    create_dirs: bool = False,
    dry_run: bool = False,
) -> SetupReport:
    """Run full environment auto-detection and optionally write `config.json`."""
    root = project_root or Path(__file__).resolve().parent.parent
    target_config = config_path or (root / "config.json")

    existing: dict[str, Any] | None = None
    if target_config.exists():
        try:
            existing = json.loads(target_config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None

    drive = detect_drive_folders(
        root,
        input_override=input_dir,
        output_override=output_dir,
    )

    if create_dirs and not dry_run:
        if drive.is_google_drive or (input_dir or output_dir):
            for path in (drive.input_dir, drive.output_dir, drive.archive_dir):
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    msg = f"Could not create folder '{path}': {exc}"
                    drive.warning = f"{drive.warning} {msg}".strip()
            drive.input_exists = drive.input_dir.exists()
            drive.output_exists = drive.output_dir.exists()

    preferred = ollama_model or (existing.get("ollama", {}).get("model") if existing else None)
    ollama = detect_ollama(ollama_url, preferred_model=preferred)
    comfyui = detect_comfyui(comfyui_url)

    config_data = build_config_dict(
        root,
        existing_config=existing,
        drive=drive,
        ollama=ollama,
        comfyui=comfyui,
    )

    wrote = False
    if not dry_run:
        write_config(target_config, config_data)
        wrote = True

    return SetupReport(
        config_path=target_config,
        wrote_config=wrote,
        drive=drive,
        ollama=ollama,
        comfyui=comfyui,
        config_data=config_data,
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")


def _print_human_report(report: SetupReport) -> None:
    print("=== Visual Hook B-Roll Auto-Detect Setup ===")

    d = report.drive
    if d.is_google_drive:
        in_tag = "exists" if d.input_exists else "will create when Drive syncs"
        out_tag = "exists" if d.output_exists else "will create when Drive syncs"
        print(f"[OK]     Google Drive root -> {d.drive_root}")
        print(f"         input_dir  = {d.input_dir} ({in_tag})")
        print(f"         output_dir = {d.output_dir} ({out_tag})")
    else:
        print(f"[WARN]   {d.warning}")
        print(f"         input_dir  = {d.input_dir}")
        print(f"         output_dir = {d.output_dir}")
    if d.warning and d.is_google_drive:
        print(f"[WARN]   {d.warning}")

    o = report.ollama
    if o.reachable:
        if o.selected_model:
            print(f"[OK]     Ollama reachable -> selected model '{o.selected_model}'")
        else:
            print(f"[WARN]   Ollama reachable at {o.base_url}, but no suitable text model selected.")
        if o.warning:
            print(f"[WARN]   {o.warning}")
    else:
        print(f"[WARN]   Ollama offline: {o.error}")
        print(f"[ACTION] Start Ollama and ensure model '{report.config_data['ollama']['model']}' is pulled.")

    c = report.comfyui
    if c.reachable:
        gpu_note = f" ({c.gpu_name}" + (f", {c.vram_total_mb} MB VRAM)" if c.vram_total_mb else ")") if c.gpu_name else ""
        if c.layout.layout != "not_found":
            print(f"[OK]     ComfyUI reachable{gpu_note} -> {c.layout.details}")
            print(f"         workflow_template = {c.layout.workflow_template} (steps={c.layout.steps})")
            for w in c.layout.warnings:
                print(f"[WARN]   {w}")
        else:
            print(f"[WARN]   ComfyUI reachable{gpu_note}, but no Flux workflow models found.")
            print(f"[ACTION] {c.layout.details}")
    else:
        print(f"[WARN]   ComfyUI offline: {c.error}")
        print("[ACTION] Start ComfyUI with '--listen 127.0.0.1 --port 8188' and re-run SETUP.bat to auto-detect Flux.")

    if report.wrote_config:
        print(f"[OK]     Wrote configuration -> {report.config_path}")
    else:
        print(f"[OK]     Dry run complete (no file written; target: {report.config_path})")


def pick_ollama_model(models: list[str]) -> str | None:
    """Compatibility helper returning the best installed Ollama chat model."""
    chosen = choose_best_ollama_model(models)
    return chosen or (models[0] if models else None)


def detect_flux_from_object_info(info: dict[str, Any]) -> dict[str, Any]:
    """Compatibility helper returning a dict summary of the selected Flux layout."""
    sel = select_flux_layout(info)
    layout_map = {
        "checkpoint_dev": "checkpoint",
        "checkpoint_schnell": "checkpoint",
        "split_unet": "unet_dualclip",
    }
    return {
        "layout": layout_map.get(sel.layout, sel.layout),
        "workflow_template": sel.workflow_template,
        "checkpoint_name": sel.checkpoint_name,
        "unet_name": sel.unet_name,
        "weight_dtype": sel.weight_dtype,
        "clip_l_name": sel.clip_l_name,
        "t5_name": sel.t5_name,
        "vae_name": sel.vae_name,
        "steps": sel.steps,
        "guidance": getattr(sel, "guidance", 3.5),
    }


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
    import shutil as _shutil

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
            _shutil.copy2(src, dest)
            copied.append(str(dest))
        except OSError:
            continue
    return copied


def build_detected_config(
    project_root: Path,
    *,
    drive_root: Path | None = None,
    scripts_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a config dict merging detected Drive root and Yap/MediaMTX .bat paths."""
    example_path = project_root / "config.example.json"
    cfg: dict[str, Any] = json.loads(example_path.read_text(encoding="utf-8")) if example_path.exists() else {}
    if drive_root is not None:
        cfg["input_dir"] = str(drive_root / "BrollInput")
        cfg["output_dir"] = str(drive_root / "BrollOutput")
        cfg["archive_dir"] = str(drive_root / "BrollInput" / "_processed")
        cfg.setdefault("watchdog", {})["yap_input_dir"] = str(drive_root / "YapInput")
        cfg["watchdog"]["bat_backup_dir"] = str(drive_root / "AI stuff" / "visual-hook-broll" / "bat_backups")
    if scripts_info:
        wd = cfg.setdefault("watchdog", {})
        if scripts_info.get("yap_bat"):
            wd["yap_bat"] = scripts_info["yap_bat"]
        if scripts_info.get("mediamtx_bat"):
            wd["mediamtx_bat"] = scripts_info["mediamtx_bat"]
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Auto-detect local Drive, Ollama, and ComfyUI Flux configuration.")
    ap.add_argument("--config", default=None, help="Target config.json path (defaults to repo root config.json)")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama base URL")
    ap.add_argument("--comfyui-url", default="http://127.0.0.1:8188", help="ComfyUI base URL")
    ap.add_argument("--input-dir", default=None, help="Override input_dir")
    ap.add_argument("--output-dir", default=None, help="Override output_dir")
    ap.add_argument("--ollama-model", default=None, help="Preferred Ollama model name")
    ap.add_argument("--create-dirs", action="store_true", help="Create detected input/output/archive dirs if parent exists")
    ap.add_argument("--write", action="store_true", help="Write config.json, scan Yap/MediaMTX scripts, and back up .bat files")
    ap.add_argument("--dry-run", action="store_true", help="Inspect environment without writing config.json")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if Ollama, ComfyUI, or Flux layout is missing")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    args = ap.parse_args(argv)

    try:
        report = run_setup_detect(
            config_path=Path(args.config) if args.config else None,
            ollama_url=args.ollama_url,
            comfyui_url=args.comfyui_url,
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            ollama_model=args.ollama_model,
            create_dirs=args.create_dirs or args.write,
            dry_run=args.dry_run,
        )
        if args.write and not args.dry_run:
            project_root = Path(__file__).resolve().parent.parent
            scripts = scan_for_scripts()
            wd = report.config_data.setdefault("watchdog", {})
            if scripts.get("yap_bat") and not wd.get("yap_bat"):
                wd["yap_bat"] = scripts["yap_bat"]
            if scripts.get("mediamtx_bat") and not wd.get("mediamtx_bat"):
                wd["mediamtx_bat"] = scripts["mediamtx_bat"]
            if report.drive.drive_root:
                wd["yap_input_dir"] = str(Path(report.drive.drive_root) / "YapInput")
                wd["bat_backup_dir"] = str(Path(report.drive.drive_root) / "AI stuff" / "visual-hook-broll" / "bat_backups")
            write_config(Path(report.config_path), report.config_data)
            if wd.get("bat_backup_dir") and report.drive.drive_root:
                backup_bat_scripts(project_root, Path(wd["bat_backup_dir"]), [wd.get("yap_bat", ""), wd.get("mediamtx_bat", "")])
    except Exception as exc:
        print(f"[ERROR] Setup detection failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(report), indent=2, default=_json_default))
    else:
        _print_human_report(report)

    if args.strict:
        if not report.ollama.reachable or not report.ollama.selected_model:
            return 2
        if not report.comfyui.reachable or report.comfyui.layout.layout == "not_found":
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
