"""Unit tests for zero-config environment auto-detection (`src.setup_detect`)."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from src.config import load_config
from src.setup_detect import (
    choose_best_ollama_model,
    detect_comfyui,
    detect_drive_folders,
    detect_ollama,
    extract_combo_options,
    run_setup_detect,
    select_flux_layout,
)


class TestSetupDetect(unittest.TestCase):
    def test_detect_drive_folders_prefers_existing_broll_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / "project"
            proj.mkdir()
            empty_drive = root / "H_MyDrive"
            empty_drive.mkdir()
            active_drive = root / "G_MyDrive"
            (active_drive / "BrollInput").mkdir(parents=True)
            (active_drive / "YapOutput").mkdir(parents=True)

            res = detect_drive_folders(
                proj,
                search_roots=[empty_drive, active_drive],
                resolve_root_override=root,
            )
            self.assertTrue(res.is_google_drive)
            self.assertEqual(res.drive_root, active_drive)
            self.assertEqual(res.input_dir, active_drive / "BrollInput")
            self.assertEqual(res.output_dir, active_drive / "BrollOutput")
            self.assertEqual(res.yap_output_dir, active_drive / "YapOutput")
            self.assertTrue(res.input_exists)
            self.assertFalse(res.output_exists)

    def test_detect_drive_folders_uses_first_drive_when_broll_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / "project"
            proj.mkdir()
            g_drive = root / "G_MyDrive"
            g_drive.mkdir()

            res = detect_drive_folders(
                proj,
                search_roots=[root / "Missing", g_drive],
                resolve_root_override=root,
            )
            self.assertTrue(res.is_google_drive)
            self.assertEqual(res.drive_root, g_drive)
            self.assertEqual(res.input_dir, g_drive / "BrollInput")
            self.assertEqual(res.output_dir, g_drive / "BrollOutput")

    def test_detect_drive_folders_fallback_when_no_drive_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / "project"
            proj.mkdir()

            res = detect_drive_folders(
                proj,
                search_roots=[root / "NoSuchDrive"],
                resolve_root_override=root,
            )
            self.assertFalse(res.is_google_drive)
            self.assertIsNone(res.drive_root)
            self.assertEqual(res.input_dir, proj / "BrollInput")
            self.assertEqual(res.output_dir, proj / "BrollOutput")
            self.assertIn("fallback", res.warning.lower())

    def test_choose_best_ollama_model_skips_vision_and_ranks_json_models(self) -> None:
        installed = ["moondream:latest", "nomic-embed-text:latest", "qwen2.5:3b", "qwen2.5-coder:7b"]
        self.assertEqual(choose_best_ollama_model(installed), "qwen2.5-coder:7b")

        installed_with_14b = installed + ["qwen2.5:14b"]
        self.assertEqual(choose_best_ollama_model(installed_with_14b), "qwen2.5:14b")

        # Explicit user preference honored when present
        self.assertEqual(choose_best_ollama_model(installed_with_14b, preferred="qwen2.5:3b"), "qwen2.5:3b")

        # Only vision/embedding models -> returns None
        self.assertIsNone(choose_best_ollama_model(["moondream:latest", "nomic-embed-text:latest"]))

    def test_select_flux_layout_checkpoint_dev_and_schnell(self) -> None:
        dev_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["sd_xl_base.safetensors", "flux1-dev-fp8.safetensors"]]}}
            }
        }
        sel = select_flux_layout(dev_info)
        self.assertEqual(sel.layout, "checkpoint")
        self.assertEqual(sel.workflow_template, "comfyui_workflows/flux_dev_fp8_api.json")
        self.assertEqual(sel.checkpoint_name, "flux1-dev-fp8.safetensors")
        self.assertEqual(sel.steps, 20)

        schnell_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["flux1-schnell-fp8.safetensors"]]}}
            }
        }
        sel_s = select_flux_layout(schnell_info)
        self.assertEqual(sel_s.layout, "checkpoint")
        self.assertEqual(sel_s.workflow_template, "comfyui_workflows/flux_schnell_api.json")
        self.assertEqual(sel_s.steps, 4)

    def test_select_flux_layout_split_unet_dualclip(self) -> None:
        split_info = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["v1-5-pruned.safetensors"]]}}},
            "UNETLoader": {
                "input": {
                    "required": {
                        "unet_name": [["flux1-dev-fp8.safetensors"]],
                        "weight_dtype": [["default", "fp8_e4m3fn", "fp8_e5m2"]],
                    }
                }
            },
            "DualCLIPLoader": {
                "input": {
                    "required": {
                        "clip_name1": [["t5xxl_fp8_e4m3fn.safetensors", "clip_l.safetensors"]],
                        "clip_name2": [["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"]],
                    }
                }
            },
            "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
        }
        sel = select_flux_layout(split_info)
        self.assertEqual(sel.layout, "unet_dualclip")
        self.assertEqual(sel.workflow_template, "comfyui_workflows/flux_unet_dualclip_api.json")
        self.assertEqual(sel.unet_name, "flux1-dev-fp8.safetensors")
        self.assertEqual(sel.weight_dtype, "fp8_e4m3fn")
        self.assertEqual(sel.clip_l_name, "clip_l.safetensors")
        self.assertEqual(sel.t5_name, "t5xxl_fp8_e4m3fn.safetensors")
        self.assertEqual(sel.vae_name, "ae.safetensors")
        self.assertEqual(sel.steps, 20)

    def test_select_flux_layout_gguf_with_comfy_v3_combo_schema(self) -> None:
        gguf_info = {
            "UnetLoaderGGUF": {
                "input": {
                    "required": {
                        "unet_name": [
                            "COMBO",
                            {"options": ["flux1-dev-Q8_0.gguf", "flux1-dev-Q4_K_S.gguf"]},
                        ]
                    }
                }
            },
            "DualCLIPLoader": {
                "input": {
                    "required": {
                        "clip_name1": ["COMBO", {"options": ["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"]}],
                        "clip_name2": ["COMBO", {"options": ["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"]}],
                    }
                }
            },
            "VAELoader": {"input": {"required": {"vae_name": ["COMBO", {"options": ["ae.safetensors"]}]}}},
        }
        self.assertEqual(
            extract_combo_options(gguf_info, "UnetLoaderGGUF", "unet_name"),
            ["flux1-dev-Q8_0.gguf", "flux1-dev-Q4_K_S.gguf"],
        )
        sel = select_flux_layout(gguf_info)
        self.assertEqual(sel.layout, "gguf")
        self.assertEqual(sel.workflow_template, "comfyui_workflows/flux_gguf_api.json")
        self.assertEqual(sel.unet_name, "flux1-dev-Q4_K_S.gguf")
        self.assertEqual(sel.clip_l_name, "clip_l.safetensors")
        self.assertEqual(sel.t5_name, "t5xxl_fp8_e4m3fn.safetensors")
        self.assertEqual(sel.vae_name, "ae.safetensors")

    def test_select_flux_layout_not_found(self) -> None:
        empty_info = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sdxl_turbo.safetensors"]]}}}
        }
        sel = select_flux_layout(empty_info)
        self.assertEqual(sel.layout, "not_found")
        self.assertEqual(sel.workflow_template, "")

    def test_unreachable_ollama_and_comfyui_handled_gracefully(self) -> None:
        with patch("src.setup_detect._http_get_json", side_effect=urllib.error.URLError("connection refused")):
            o = detect_ollama("http://127.0.0.1:11434")
            c = detect_comfyui("http://127.0.0.1:8188")

        self.assertFalse(o.reachable)
        self.assertIsNone(o.selected_model)
        self.assertIn("Could not reach Ollama", o.error)

        self.assertFalse(c.reachable)
        self.assertEqual(c.layout.layout, "not_found")
        self.assertIn("Could not reach ComfyUI", c.error)

    def test_run_setup_detect_preserves_user_fields_and_roundtrips_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = Path(__file__).resolve().parent.parent
            cfg_path = root / "config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "news_engine": {
                            "vertex_project_id": "my-gcp-project",
                            "auto_run_veo": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            fake_drive_root = root / "My Drive"
            fake_drive_root.mkdir()
            (fake_drive_root / "BrollInput").mkdir()
            (fake_drive_root / "BrollOutput").mkdir()

            def _fake_get(url: str, timeout: int = 5):
                if url.endswith("/api/tags"):
                    return {"models": [{"name": "moondream:latest"}, {"name": "qwen2.5-coder:7b"}]}
                if url.endswith("/system_stats"):
                    return {"devices": [{"name": "Quadro RTX 4000", "vram_total": 8589934592}]}
                if url.endswith("/object_info/UnetLoaderGGUF"):
                    return {"input": {"required": {"unet_name": [["flux1-dev-Q4_K_S.gguf"]]}}}
                if url.endswith("/object_info/DualCLIPLoader"):
                    return {
                        "input": {
                            "required": {
                                "clip_name1": [["t5xxl_fp8_e4m3fn.safetensors"]],
                                "clip_name2": [["clip_l.safetensors"]],
                            }
                        }
                    }
                if url.endswith("/object_info/VAELoader"):
                    return {"input": {"required": {"vae_name": [["ae.safetensors"]]}}}
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

            drive_res = detect_drive_folders(
                repo_root,
                search_roots=[fake_drive_root],
                resolve_root_override=root,
            )
            with (
                patch("src.setup_detect.detect_drive_folders", return_value=drive_res),
                patch("src.setup_detect._http_get_json", side_effect=_fake_get),
            ):
                report = run_setup_detect(project_root=repo_root, config_path=cfg_path)

            self.assertTrue(report.wrote_config)
            loaded = load_config(cfg_path)
            self.assertEqual(loaded.input_dir, fake_drive_root / "BrollInput")
            self.assertEqual(loaded.output_dir, fake_drive_root / "BrollOutput")
            self.assertEqual(loaded.ollama.model, "qwen2.5-coder:7b")
            self.assertEqual(loaded.comfyui.workflow_template, "comfyui_workflows/flux_gguf_api.json")
            self.assertEqual(loaded.comfyui.unet_name, "flux1-dev-Q4_K_S.gguf")
            self.assertEqual(loaded.comfyui.clip_l_name, "clip_l.safetensors")
            self.assertEqual(loaded.comfyui.t5_name, "t5xxl_fp8_e4m3fn.safetensors")
            self.assertEqual(loaded.comfyui.vae_name, "ae.safetensors")
            self.assertEqual(loaded.news_engine.vertex_project_id, "my-gcp-project")
            self.assertTrue(loaded.news_engine.auto_run_veo)


if __name__ == "__main__":
    unittest.main()
