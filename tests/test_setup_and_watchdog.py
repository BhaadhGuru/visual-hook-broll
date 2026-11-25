"""Unit tests for setup auto-detection, Flux split/GGUF templates, and the Watchdog supervisor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.comfy_client import render_workflow_template
from src.config import AppConfig, WatchdogConfig
from src.setup_detect import (
    backup_bat_scripts,
    build_detected_config,
    detect_flux_from_object_info,
    pick_ollama_model,
    scan_for_scripts,
)
from src.watchdog import ServiceSpec, WatchdogSupervisor, parse_command
from src.worker import _is_watchdog_command


class FakeProcessController:
    def __init__(self, initially_running: set[str] | None = None) -> None:
        self.running: set[str] = set(initially_running or set())
        self.events: list[tuple[str, str]] = []

    def is_running(self, spec: ServiceSpec) -> bool:
        return spec.key in self.running

    def start(self, spec: ServiceSpec, project_root: Path) -> tuple[bool, str]:
        self.running.add(spec.key)
        self.events.append(("start", spec.key))
        return True, f"Started {spec.label}"

    def stop(self, spec: ServiceSpec) -> tuple[bool, str]:
        self.running.discard(spec.key)
        self.events.append(("stop", spec.key))
        return True, f"Stopped {spec.label}"


class SetupDetectTests(unittest.TestCase):
    def test_pick_ollama_model_skips_embeddings_and_prefers_qwen(self) -> None:
        models = ["nomic-embed-text:latest", "llama3.1:8b", "qwen2.5:14b"]
        self.assertEqual(pick_ollama_model(models), "qwen2.5:14b")

    def test_detect_flux_all_three_layouts(self) -> None:
        # 1. All-in-one checkpoint
        ckpt_info = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["flux1-dev-fp8.safetensors"]]}}}
        }
        res1 = detect_flux_from_object_info(ckpt_info)
        self.assertEqual(res1["layout"], "checkpoint")
        self.assertEqual(res1["workflow_template"], "comfyui_workflows/flux_dev_fp8_api.json")

        # 2. Split UNET + DualCLIP + VAE
        unet_info = {
            "UNETLoader": {"input": {"required": {"unet_name": [["flux1-dev-fp8.safetensors"]]}}},
            "DualCLIPLoader": {
                "input": {"required": {"clip_name1": [["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"]]}}
            },
            "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
        }
        res2 = detect_flux_from_object_info(unet_info)
        self.assertEqual(res2["layout"], "unet_dualclip")
        self.assertEqual(res2["workflow_template"], "comfyui_workflows/flux_unet_dualclip_api.json")
        self.assertEqual(res2["weight_dtype"], "fp8_e4m3fn")

        # 3. GGUF UNET
        gguf_info = {
            "UnetLoaderGGUF": {"input": {"required": {"unet_name": [["flux1-dev-Q8_0.gguf"]]}}},
            "DualCLIPLoader": {
                "input": {"required": {"clip_name1": [["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"]]}}
            },
            "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
        }
        res3 = detect_flux_from_object_info(gguf_info)
        self.assertEqual(res3["layout"], "gguf")
        self.assertEqual(res3["workflow_template"], "comfyui_workflows/flux_gguf_api.json")

    def test_render_split_and_gguf_templates(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for rel in ("comfyui_workflows/flux_unet_dualclip_api.json", "comfyui_workflows/flux_gguf_api.json"):
            tpl = json.loads((root / rel).read_text(encoding="utf-8"))
            rendered = render_workflow_template(
                tpl,
                prompt_text="Close-up of hands checking cables",
                seed=42,
                width=1024,
                height=576,
                steps=20,
                cfg=1.0,
                guidance=3.5,
                checkpoint_name="",
                filename_prefix="test_panel",
                unet_name="flux1-dev.gguf",
                weight_dtype="fp8_e4m3fn",
                clip_l_name="clip_l.safetensors",
                t5_name="t5xxl_fp8_e4m3fn.safetensors",
                vae_name="ae.safetensors",
            )
            dumped = json.dumps(rendered)
            self.assertNotIn("{{", dumped)
            self.assertIn("flux1-dev.gguf", dumped)
            self.assertIn("ae.safetensors", dumped)

    def test_scan_for_scripts_and_backup_bat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "yap").mkdir()
            yap_bat = base / "yap" / "start_yap.bat"
            yap_bat.write_text("@echo off\necho yap\n", encoding="utf-8")
            (base / "mtx").mkdir()
            mtx_bat = base / "mtx" / "run_mediamtx.bat"
            mtx_bat.write_text("@echo off\nmediamtx.exe\n", encoding="utf-8")

            found = scan_for_scripts([base])
            self.assertEqual(found["yap_bat"], str(yap_bat))
            self.assertEqual(found["mediamtx_bat"], str(mtx_bat))

            proj = Path(__file__).resolve().parent.parent
            backup_dir = base / "drive_backup"
            copied = backup_bat_scripts(proj, backup_dir, [found["yap_bat"], found["mediamtx_bat"]])
            names = {Path(p).name for p in copied}
            self.assertIn("SETUP.bat", names)
            self.assertIn("START_BROLL.bat", names)
            self.assertIn("START_WATCHDOG.bat", names)
            self.assertIn("start_yap.bat", names)
            self.assertIn("run_mediamtx.bat", names)

            cfg = build_detected_config(proj, drive_root=base, scripts_info=found)
            self.assertEqual(cfg["watchdog"]["yap_bat"], str(yap_bat))
            self.assertEqual(cfg["watchdog"]["mediamtx_bat"], str(mtx_bat))


class WatchdogTests(unittest.TestCase):
    def _make_supervisor(self, tmp: Path, running: set[str] | None = None):
        cfg = AppConfig(
            input_dir=tmp / "BrollInput",
            output_dir=tmp / "BrollOutput",
            archive_dir=tmp / "BrollInput" / "_processed",
            project_root=tmp,
            watchdog=WatchdogConfig(
                broll_bat="START_BROLL.bat",
                yap_bat="start_yap.bat",
                mediamtx_bat="start_mediamtx.bat",
                yap_input_dir=tmp / "YapInput",
                mediamtx_auto_restart=True,
                notify_phone="15551234567",
            ),
        )
        ctrl = FakeProcessController(running)
        sup = WatchdogSupervisor(
            cfg,
            controller=ctrl,
            ollama_probe=lambda _: {"reachable": True, "selected_model": "qwen2.5:14b"},
            comfy_probe=lambda _: {"reachable": True, "layout": "checkpoint"},
        )
        return cfg, ctrl, sup

    def test_parse_command_and_worker_filter(self) -> None:
        self.assertEqual(parse_command("#status"), ("#status", "all"))
        self.assertEqual(parse_command("#restart mediamtx"), ("#restart", "stream"))
        self.assertEqual(parse_command("#stop livestream"), ("#stop", "stream"))
        self.assertEqual(parse_command("#start yap"), ("#start", "yap"))
        self.assertIsNone(parse_command("Hook, first 3 seconds: Card reader stops working"))
        self.assertTrue(_is_watchdog_command("#restart stream"))
        self.assertFalse(_is_watchdog_command("Card reader stops working mid-rush."))

    def test_whatsapp_command_handling_and_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg, ctrl, sup = self._make_supervisor(tmp, running={"broll", "stream"})
            cfg.input_dir.mkdir(parents=True)

            cmd_file = cfg.input_dir / "wa_cmd_1.json"
            cmd_file.write_text(
                json.dumps({"job_id": "wa_cmd_1", "sender": "15559990000", "script": "#restart stream"}),
                encoding="utf-8",
            )

            res = sup.tick()
            self.assertEqual(res["commands_handled"], 1)
            self.assertIn(("stop", "stream"), ctrl.events)
            self.assertIn(("start", "stream"), ctrl.events)

            out_file = cfg.output_dir / "wa_cmd_1_result.json"
            self.assertTrue(out_file.exists())
            out = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertIsNone(out["storyboard_filename"])
            self.assertEqual(out["sender"], "15559990000")
            self.assertIn("Restarted MediaMTX Livestream", out["whatsapp_message"])

    def test_mediamtx_crash_auto_restart_and_manual_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg, ctrl, sup = self._make_supervisor(tmp, running={"broll", "yap"})
            # Stream is down -> tick should auto-restart it and write WhatsApp alert
            res = sup.tick()
            self.assertTrue(ctrl.is_running(sup.services["stream"]))
            self.assertTrue(any("auto-restart stream" in a for a in res["auto_actions"]))
            alerts = list(cfg.output_dir.glob("watchdog_stream_*_result.json"))
            self.assertEqual(len(alerts), 1)

            # Manual #stop stream should stop it and prevent auto-restart
            sup.execute_command("#stop", "stream")
            self.assertFalse(ctrl.is_running(sup.services["stream"]))
            sup._last_restart_ts["stream"] = 0.0
            sup.tick()
            self.assertFalse(ctrl.is_running(sup.services["stream"]))

    def test_on_demand_launch_when_script_lands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg, ctrl, sup = self._make_supervisor(tmp, running={"stream"})
            cfg.input_dir.mkdir(parents=True)
            cfg.watchdog.yap_input_dir.mkdir(parents=True)
            (cfg.input_dir / "script1.txt").write_text("Hello world script", encoding="utf-8")
            (cfg.watchdog.yap_input_dir / "yap1.txt").write_text("Yap job", encoding="utf-8")

            sup.tick()
            self.assertTrue(ctrl.is_running(sup.services["broll"]))
            self.assertTrue(ctrl.is_running(sup.services["yap"]))

    def test_whatsapp_update_command(self) -> None:
        self.assertEqual(parse_command("#update"), ("#update", "all"))
        self.assertTrue(_is_watchdog_command("#update"))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "SETUP.bat").write_text("@echo off\n", encoding="utf-8")
            backup_dir = tmp / "drive_backups"
            cfg, ctrl, sup = self._make_supervisor(tmp, running={"broll", "stream"})
            cfg.watchdog.bat_backup_dir = str(backup_dir)
            called = []
            sup.updater = lambda root: (called.append(root) or True, "git pull OK (Already up to date.)")
            cfg.input_dir.mkdir(parents=True)
            (cfg.input_dir / "wa_upd.json").write_text(
                json.dumps({"job_id": "wa_upd", "sender": "15559990000", "script": "#update"}),
                encoding="utf-8",
            )

            res = sup.tick()
            self.assertEqual(res["commands_handled"], 1)
            self.assertEqual(len(called), 1)
            self.assertTrue((backup_dir / "SETUP.bat").exists())
            self.assertIn(("stop", "broll"), ctrl.events)
            self.assertIn(("start", "broll"), ctrl.events)
            out = json.loads((cfg.output_dir / "wa_upd_result.json").read_text(encoding="utf-8"))
            self.assertIn("Update complete", out["whatsapp_message"])
            self.assertIn("Restarted B-Roll Worker", out["whatsapp_message"])


if __name__ == "__main__":
    unittest.main()
