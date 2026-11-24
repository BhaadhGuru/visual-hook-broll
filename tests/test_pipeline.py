"""Unit and end-to-end integration tests for visual-hook-broll."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.comfy_client import render_workflow_template
from src.config import AppConfig, ComfyUIConfig, OllamaConfig, StoryboardConfig
from src.director import ShotListPlan, ShotPanel, parse_script_directives
from src.formatter import (
    format_markdown_shoot_card,
    format_whatsapp_image_caption,
    format_whatsapp_shot_list,
)
from src.pipeline import run_job
from src.storyboard import compose_storyboard_grid, make_placeholder_panel
from src.worker import process_once


def _sample_plan() -> ShotListPlan:
    return ShotListPlan(
        title="AMA TechTel — Tech Tip Tuesday: Card Reader Down",
        hook_0_3s_concept="Dead card reader mid-rush: tight hands checking loose power cables as customer line blurs behind.",
        curiosity_loop_strategy="Open loop on dead terminal -> isolate device vs Wi-Fi -> 30-second countdown reboot -> mobile backup + taped support number payoff.",
        consistency_lock="Warm wooden retail checkout counter, matte-black countertop EMV terminal, soft overhead pendant lights, 35-50mm shallow depth of field.",
        gear_and_location_notes="Shoot at real checkout counter with practical overhead lights; bring 35mm + 50mm lenses, a printed 'SUPPORT / 1-800-555-0199' tape strip, and a mobile tap reader.",
        shots=[
            ShotPanel(
                panel_index=1,
                panel_position="Top-Left",
                shot_label="Hook 1",
                curiosity_role="0-3s Visual Hook",
                script_beat="Card reader stops working mid-rush. Check cables and screen.",
                visual_instruction="Up-close of hands checking that power/data cables are plugged snugly into the card reader and confirming the screen powers on.",
                framing_and_lens="Extreme Close-Up (50mm f/2.8)",
                camera_movement="Tight handheld push-in with subtle urgency",
                lighting_and_props="Matte-black EMV terminal, loose-to-snug cables, warm counter practicals",
                flux_prompt="Tight close-up of hands plugging cables into a matte-black card reader on a wood counter.",
            ),
            ShotPanel(
                panel_index=2,
                panel_position="Top-Right",
                shot_label="Hook 2",
                curiosity_role="Diagnosis Beat",
                script_beat="Check one other device to confirm your internet is still up.",
                visual_instruction="Over-the-shoulder of employee checking smartphone Wi-Fi 'Connected' status while card reader sits blank beside the register.",
                framing_and_lens="Over-the-Shoulder MCU (35mm f/2.8)",
                camera_movement="Subtle rack focus from dormant card reader to live phone screen",
                lighting_and_props="Smartphone showing Wi-Fi Connected, dormant dark-screen card reader",
                flux_prompt="Over the shoulder of shop worker holding phone showing Wi-Fi connected next to dormant card reader.",
            ),
            ShotPanel(
                panel_index=3,
                panel_position="Bottom-Left",
                shot_label="Hook 3",
                curiosity_role="Action & Tension",
                script_beat="Turn it off, wait thirty seconds, and turn it back on.",
                visual_instruction="Hand powering down the terminal while other hand holds a watch or smartphone showing a 00:30 countdown timer.",
                framing_and_lens="Close-Up Insert (50mm f/2.8)",
                camera_movement="Static lock-off with clean foreground timer reveal",
                lighting_and_props="Card reader power button, phone/watch set to 00:30 timer",
                flux_prompt="Close-up of hand powering down card reader while phone shows 00:30 countdown timer.",
            ),
            ShotPanel(
                panel_index=4,
                panel_position="Bottom-Right",
                shot_label="Hook 4 & 5",
                curiosity_role="Backup & Payoff",
                script_beat="Prep mobile fallback reader and tape support numbers by register.",
                visual_instruction="Prepping a compact wireless mobile fallback reader beside smartphone while 'SUPPORT / 1-800-555-0199' is taped right on the register bezel.",
                framing_and_lens="Medium Close-Up (35mm f/2.8)",
                camera_movement="Smooth lateral slide from mobile backup reader to taped support strip",
                lighting_and_props="Wireless tap reader, smartphone, taped 'SUPPORT / 1-800-555-0199' label",
                flux_prompt="Medium close-up of wireless pocket card reader next to register with taped SUPPORT / 1-800-555-0199 label.",
            ),
        ],
    )


class TestVisualHookBroll(unittest.TestCase):
    def test_parse_script_directives(self) -> None:
        raw = "#vertical #shots=6 #noimages\nCard reader stops working mid-rush."
        d = parse_script_directives(raw)
        self.assertEqual(d.aspect, "9:16")
        self.assertEqual(d.panel_count, 6)
        self.assertFalse(d.generate_images)
        self.assertEqual(d.clean_script, "Card reader stops working mid-rush.")

    def test_comfy_template_substitution_preserves_types(self) -> None:
        tpl_path = Path(__file__).resolve().parent.parent / "comfyui_workflows" / "flux_dev_fp8_api.json"
        template = json.loads(tpl_path.read_text(encoding="utf-8"))
        rendered = render_workflow_template(
            template,
            prompt_text="Hands plugging card reader cables",
            seed=424242,
            width=1024,
            height=576,
            steps=20,
            cfg=1.0,
            guidance=3.5,
            checkpoint_name="flux1-dev-fp8.safetensors",
            filename_prefix="test_job_p01",
        )
        self.assertEqual(rendered["4"]["inputs"]["ckpt_name"], "flux1-dev-fp8.safetensors")
        self.assertEqual(rendered["5"]["inputs"]["width"], 1024)
        self.assertIsInstance(rendered["5"]["inputs"]["width"], int)
        self.assertEqual(rendered["3"]["inputs"]["seed"], 424242)
        self.assertIsInstance(rendered["3"]["inputs"]["seed"], int)
        self.assertEqual(rendered["35"]["inputs"]["guidance"], 3.5)
        self.assertIsInstance(rendered["35"]["inputs"]["guidance"], float)
        self.assertEqual(rendered["6"]["inputs"]["text"], "Hands plugging card reader cables")

    def test_formatters_include_all_photog_fields(self) -> None:
        plan = _sample_plan()
        wa_text = format_whatsapp_shot_list(plan, aspect="16:9")
        self.assertIn("AMA TechTel", wa_text)
        self.assertIn("Hook 1 (Top-Left)", wa_text)
        self.assertIn("Hook 4 & 5 (Bottom-Right)", wa_text)
        self.assertIn("Framing / Lens", wa_text)
        self.assertIn("Camera Move", wa_text)

        caption = format_whatsapp_image_caption(plan)
        self.assertIn("Hook 1 (Top-Left)", caption)

        md = format_markdown_shoot_card(plan)
        self.assertIn("## Shot List", md)
        self.assertIn("SUPPORT / 1-800-555-0199", md)

    def test_storyboard_grid_rendering(self) -> None:
        plan = _sample_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            panels = []
            for shot in plan.shots:
                p = tmp / f"p{shot.panel_index}.png"
                make_placeholder_panel(shot, p, width=640, height=360)
                panels.append(p)

            out_grid = tmp / "storyboard.png"
            compose_storyboard_grid(plan, panels, out_grid, columns=2, panel_width=640, panel_height=360)
            self.assertTrue(out_grid.exists())
            with Image.open(out_grid) as img:
                self.assertGreater(img.width, 1300)
                self.assertGreater(img.height, 1000)

    def test_worker_end_to_end_json_drop(self) -> None:
        plan = _sample_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = AppConfig(
                input_dir=root / "BrollInput",
                output_dir=root / "BrollOutput",
                archive_dir=root / "BrollInput" / "_processed",
                ollama=OllamaConfig(unload_after_generation=False),
                comfyui=ComfyUIConfig(free_vram_after_job=False),
                storyboard=StoryboardConfig(panel_width=640, panel_height=360),
            )
            cfg.input_dir.mkdir(parents=True, exist_ok=True)
            job_file = cfg.input_dir / "wa_12345.json"
            job_file.write_text(
                json.dumps(
                    {
                        "job_id": "wa_12345",
                        "sender": "15551234567",
                        "chat_id": "15551234567",
                        "message_id": "wamid.abc123",
                        "script": "Card reader stops working mid-rush. What do you do first?",
                    }
                ),
                encoding="utf-8",
            )

            with patch("src.pipeline.generate_shot_list", return_value=plan):
                count = process_once(cfg, mock_comfy=True)

            self.assertEqual(count, 1)
            self.assertFalse(job_file.exists())
            self.assertEqual(len(list(cfg.archive_dir.glob("*wa_12345.json"))), 1)

            result_path = cfg.output_dir / "wa-12345_result.json"
            self.assertTrue(result_path.exists())
            manifest = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["sender"], "15551234567")
            self.assertEqual(manifest["storyboard_filename"], "wa-12345_storyboard.png")
            self.assertTrue((cfg.output_dir / "wa-12345_storyboard.png").exists())
            self.assertTrue((cfg.output_dir / "wa-12345_whatsapp.txt").exists())


if __name__ == "__main__":
    unittest.main()
