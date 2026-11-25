"""Unit tests for the local Baileys WhatsApp job runner (`src.run_whatsapp_job`)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.director import ShotListPlan, ShotPanel
from src.news_segmenter import NewsShot
from src.run_whatsapp_job import main, strip_whatsapp_trigger


def _sample_broll_plan() -> ShotListPlan:
    return ShotListPlan(
        title="AMA TechTel — Tech Tip Tuesday: Card Reader Down",
        hook_0_3s_concept="Dead card reader mid-rush with customer line behind.",
        curiosity_loop_strategy="Dead terminal -> Wi-Fi check -> 30s reboot -> mobile backup.",
        consistency_lock="Warm wooden checkout counter, matte-black EMV reader, 35mm-50mm.",
        gear_and_location_notes="35mm + 50mm, printed support tape strip.",
        shots=[
            ShotPanel(
                panel_index=1,
                panel_position="Top-Left",
                shot_label="Hook 1",
                curiosity_role="0-3s Visual Hook",
                script_beat="Card reader stops working mid-rush.",
                visual_instruction="Hands checking power/data cables on card reader.",
                framing_and_lens="Extreme Close-Up (50mm f/2.8)",
                camera_movement="Tight handheld push-in",
                lighting_and_props="Matte-black EMV terminal, warm counter practicals",
                flux_prompt="Close-up of hands checking card reader cables on wood counter.",
            ),
            ShotPanel(
                panel_index=2,
                panel_position="Top-Right",
                shot_label="Hook 2",
                curiosity_role="Payoff",
                script_beat="Prep mobile fallback reader.",
                visual_instruction="Wireless tap reader beside register.",
                framing_and_lens="Medium Close-Up (35mm f/2.8)",
                camera_movement="Smooth slide",
                lighting_and_props="Wireless tap reader and smartphone",
                flux_prompt="Wireless pocket card reader beside register.",
            ),
        ],
    )


class TestWhatsappBridgeJob(unittest.TestCase):
    def test_strip_whatsapp_trigger_preserves_inline_hashtags(self) -> None:
        cleaned, is_news = strip_whatsapp_trigger("#broll #vertical #shots=6 Card reader is down.")
        self.assertEqual(cleaned, "#vertical #shots=6 Card reader is down.")
        self.assertFalse(is_news)

        cleaned2, is_news2 = strip_whatsapp_trigger("b-roll: #noimages Quick tech tip")
        self.assertEqual(cleaned2, "#noimages Quick tech tip")
        self.assertFalse(is_news2)

        cleaned3, is_news3 = strip_whatsapp_trigger("#news VO: Water main broke on 5th Street.")
        self.assertEqual(cleaned3, "#news VO: Water main broke on 5th Street.")
        self.assertTrue(is_news3)

        # Stage 2 reel BROLL time-range syntax without a colon after BROLL stays untouched
        untouched, is_news4 = strip_whatsapp_trigger('BROLL 4.2-9.0 "D:\\clip.mp4" card')
        self.assertEqual(untouched, 'BROLL 4.2-9.0 "D:\\clip.mp4" card')
        self.assertFalse(is_news4)

    def test_main_broll_mode_outputs_valid_json_and_storyboard(self) -> None:
        plan = _sample_broll_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "BrollOutput"
            cfg_file = root / "config.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "input_dir": str(root / "BrollInput"),
                        "output_dir": str(out_dir),
                        "ollama": {"unload_after_generation": False},
                        "comfyui": {"free_vram_after_job": False},
                        "storyboard": {"panel_width": 640, "panel_height": 360},
                    }
                ),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with (
                patch("src.pipeline.generate_shot_list", return_value=plan),
                redirect_stdout(buf),
            ):
                rc = main(
                    [
                        "--text",
                        "broll: Card reader stops working mid-rush.",
                        "--sender",
                        "15551234567@s.whatsapp.net",
                        "--job-id",
                        "wa_bridge_01",
                        "--config",
                        str(cfg_file),
                        "--output-dir",
                        str(out_dir),
                        "--mock-comfy",
                    ]
                )

            self.assertEqual(rc, 0)
            last_line = [ln for ln in buf.getvalue().splitlines() if ln.strip()][-1]
            res = json.loads(last_line)
            self.assertTrue(res["ok"])
            self.assertEqual(res["mode"], "broll")
            self.assertEqual(res["job_id"], "wa-bridge-01")
            self.assertIn("AMA TechTel", res["whatsapp_message"])
            self.assertTrue(Path(res["storyboard_path"]).exists())

    def test_main_news_mode_outputs_valid_json(self) -> None:
        shots = [
            NewsShot(
                shot_index=1,
                segment_type="VO",
                suggested_filename="01_Main_Break.mp4",
                visual_description="Crews standing beside flooded intersection.",
                veo_prompt="Sony FX9 35mm handheld shot of crews by flooded intersection.",
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "BrollOutput"
            resolve_dir = root / "ResolveWatch"
            cfg_file = root / "config.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "input_dir": str(root / "BrollInput"),
                        "output_dir": str(out_dir),
                        "news_engine": {
                            "resolve_watch_dir": str(resolve_dir),
                            "auto_run_veo": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with (
                patch("src.news_engine.segment_news_script", return_value=shots),
                redirect_stdout(buf),
            ):
                rc = main(
                    [
                        "--text",
                        "#news VO: A water main broke downtown before sunrise.",
                        "--sender",
                        "15551234567@s.whatsapp.net",
                        "--job-id",
                        "wa_news_bridge_01",
                        "--config",
                        str(cfg_file),
                        "--output-dir",
                        str(out_dir),
                    ]
                )

            self.assertEqual(rc, 0)
            last_line = [ln for ln in buf.getvalue().splitlines() if ln.strip()][-1]
            res = json.loads(last_line)
            self.assertTrue(res["ok"])
            self.assertEqual(res["mode"], "news")
            self.assertIn("NEWS B-ROLL & SOT PACKAGE", res["whatsapp_message"])
            self.assertIsNone(res["storyboard_path"])
            self.assertTrue((Path(res["resolve_package_dir"]) / "resolve_markers.json").exists())

    def test_main_watchdog_help_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg_file = root / "config.json"
            cfg_file.write_text(json.dumps({"input_dir": str(root / "in"), "output_dir": str(root / "out")}), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--text", "#help", "--config", str(cfg_file), "--job-id", "wa_cmd_help"])
            self.assertEqual(rc, 0)
            last_line = [ln for ln in buf.getvalue().splitlines() if ln.strip()][-1]
            res = json.loads(last_line)
            self.assertTrue(res["ok"])
            self.assertEqual(res["mode"], "watchdog")
            self.assertIn("#status", res["whatsapp_message"])
            self.assertIn("#update", res["whatsapp_message"])


if __name__ == "__main__":
    unittest.main()
