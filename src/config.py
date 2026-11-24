"""Configuration loader for visual-hook-broll."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:14b"
    timeout_seconds: int = 180
    temperature: float = 0.4
    unload_after_generation: bool = True


@dataclass
class ComfyUIConfig:
    base_url: str = "http://127.0.0.1:8188"
    workflow_template: str = "comfyui_workflows/flux_dev_fp8_api.json"
    checkpoint_name: str = "flux1-dev-fp8.safetensors"
    unet_name: str = ""
    weight_dtype: str = "fp8_e4m3fn"
    clip_l_name: str = "clip_l.safetensors"
    t5_name: str = "t5xxl_fp8_e4m3fn.safetensors"
    vae_name: str = "ae.safetensors"
    width: int = 1024
    height: int = 576
    steps: int = 20
    cfg: float = 1.0
    guidance: float = 3.5
    timeout_seconds: int = 300
    free_vram_after_job: bool = True


@dataclass
class StoryboardConfig:
    default_panel_count: int = 4
    grid_columns: int = 2
    panel_width: int = 960
    panel_height: int = 540
    include_individual_panels: bool = True


@dataclass
class NewsEngineConfig:
    veo_provider: str = "vertex"
    veo_model: str = "veo-3.1-fast-generate-001"
    resolution: str = "1080p"
    aspect_ratio: str = "16:9"
    veo_duration_seconds: int = 8
    veo_generate_audio: bool = False
    fps: float = 29.97
    default_pacing_seconds: float = 4.0
    vertex_project_id: str = ""
    vertex_location: str = "us-central1"
    resolve_watch_dir: Path = Path("./ResolveWatch")
    yap_output_dir: Path = Path("./YapOutput")
    auto_run_veo: bool = False


@dataclass
class AppConfig:
    input_dir: Path = Path("./BrollInput")
    output_dir: Path = Path("./BrollOutput")
    archive_dir: Path = Path("./BrollInput/_processed")
    poll_interval_seconds: float = 3.0
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    comfyui: ComfyUIConfig = field(default_factory=ComfyUIConfig)
    storyboard: StoryboardConfig = field(default_factory=StoryboardConfig)
    news_engine: NewsEngineConfig = field(default_factory=NewsEngineConfig)

    def resolve_workflow_path(self) -> Path:
        candidate = Path(self.comfyui.workflow_template)
        if candidate.is_absolute():
            return candidate
        return (self.project_root / candidate).resolve()


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load config from JSON file with environment variable overrides."""
    project_root = Path(__file__).resolve().parent.parent
    raw: dict = {}

    if config_path is not None:
        cfg_file = Path(config_path)
    else:
        default_cfg = project_root / "config.json"
        cfg_file = default_cfg if default_cfg.exists() else project_root / "config.example.json"

    if cfg_file.exists():
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))

    ollama_raw = raw.get("ollama", {})
    comfy_raw = raw.get("comfyui", {})
    sb_raw = raw.get("storyboard", {})
    news_raw = raw.get("news_engine", {})

    ollama_cfg = OllamaConfig(
        base_url=os.environ.get("OLLAMA_BASE_URL", ollama_raw.get("base_url", "http://127.0.0.1:11434")),
        model=os.environ.get("OLLAMA_MODEL", ollama_raw.get("model", "qwen2.5:14b")),
        timeout_seconds=int(ollama_raw.get("timeout_seconds", 180)),
        temperature=float(ollama_raw.get("temperature", 0.4)),
        unload_after_generation=bool(ollama_raw.get("unload_after_generation", True)),
    )

    comfy_cfg = ComfyUIConfig(
        base_url=os.environ.get("COMFYUI_BASE_URL", comfy_raw.get("base_url", "http://127.0.0.1:8188")),
        workflow_template=os.environ.get(
            "COMFYUI_WORKFLOW", comfy_raw.get("workflow_template", "comfyui_workflows/flux_dev_fp8_api.json")
        ),
        checkpoint_name=os.environ.get(
            "FLUX_CHECKPOINT", comfy_raw.get("checkpoint_name", "flux1-dev-fp8.safetensors")
        ),
        unet_name=os.environ.get("FLUX_UNET", comfy_raw.get("unet_name", "")),
        weight_dtype=os.environ.get("FLUX_WEIGHT_DTYPE", comfy_raw.get("weight_dtype", "fp8_e4m3fn")),
        clip_l_name=os.environ.get("FLUX_CLIP_L", comfy_raw.get("clip_l_name", "clip_l.safetensors")),
        t5_name=os.environ.get("FLUX_T5", comfy_raw.get("t5_name", "t5xxl_fp8_e4m3fn.safetensors")),
        vae_name=os.environ.get("FLUX_VAE", comfy_raw.get("vae_name", "ae.safetensors")),
        width=int(comfy_raw.get("width", 1024)),
        height=int(comfy_raw.get("height", 576)),
        steps=int(comfy_raw.get("steps", 20)),
        cfg=float(comfy_raw.get("cfg", 1.0)),
        guidance=float(comfy_raw.get("guidance", 3.5)),
        timeout_seconds=int(comfy_raw.get("timeout_seconds", 300)),
        free_vram_after_job=bool(comfy_raw.get("free_vram_after_job", True)),
    )

    sb_cfg = StoryboardConfig(
        default_panel_count=int(sb_raw.get("default_panel_count", 4)),
        grid_columns=int(sb_raw.get("grid_columns", 2)),
        panel_width=int(sb_raw.get("panel_width", 960)),
        panel_height=int(sb_raw.get("panel_height", 540)),
        include_individual_panels=bool(sb_raw.get("include_individual_panels", True)),
    )

    news_cfg = NewsEngineConfig(
        veo_provider=os.environ.get("VEO_PROVIDER", news_raw.get("veo_provider", "vertex")),
        veo_model=os.environ.get("VEO_MODEL", news_raw.get("veo_model", "veo-3.1-fast-generate-001")),
        resolution=news_raw.get("resolution", "1080p"),
        aspect_ratio=news_raw.get("aspect_ratio", "16:9"),
        veo_duration_seconds=int(news_raw.get("veo_duration_seconds", 8)),
        veo_generate_audio=bool(news_raw.get("veo_generate_audio", False)),
        fps=float(news_raw.get("fps", 29.97)),
        default_pacing_seconds=float(news_raw.get("default_pacing_seconds", 4.0)),
        vertex_project_id=os.environ.get("VERTEX_PROJECT_ID", news_raw.get("vertex_project_id", "")),
        vertex_location=os.environ.get("VERTEX_LOCATION", news_raw.get("vertex_location", "us-central1")),
        resolve_watch_dir=Path(
            os.environ.get("RESOLVE_WATCH_DIR", news_raw.get("resolve_watch_dir", "./ResolveWatch"))
        ),
        yap_output_dir=Path(os.environ.get("YAP_OUTPUT_DIR", news_raw.get("yap_output_dir", "./YapOutput"))),
        auto_run_veo=bool(news_raw.get("auto_run_veo", False)),
    )

    input_dir = Path(os.environ.get("BROLL_INPUT_DIR", raw.get("input_dir", "./BrollInput")))
    output_dir = Path(os.environ.get("BROLL_OUTPUT_DIR", raw.get("output_dir", "./BrollOutput")))
    archive_dir = Path(os.environ.get("BROLL_ARCHIVE_DIR", raw.get("archive_dir", str(input_dir / "_processed"))))

    return AppConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        archive_dir=archive_dir,
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 3.0)),
        project_root=project_root,
        ollama=ollama_cfg,
        comfyui=comfy_cfg,
        storyboard=sb_cfg,
        news_engine=news_cfg,
    )
