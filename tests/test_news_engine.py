"""Unit and integration tests for the News B-Roll & SOT Engine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.config import AppConfig, NewsEngineConfig
from src.news_engine import run_news_package
from src.news_segmenter import (
    NewsShot,
    sanitize_shot_filename,
    wrap_camera_style_anchor,
)
from src.veo_client import VeoConfig, build_veo_request_payload
from src.worker import process_once
from src.yap_merge import build_resolve_markers, load_yap_markers, seconds_to_timecode


def _sample_news_shots() -> list[NewsShot]:
    items = [
        (1, "VO", "01_Suburban_Street.mp4", "Public works workers setting orange traffic cones across two wet lanes on a suburban street"),
        (2, "SOT", "02_Kitchen_Faucet_Dry.mp4", "Hand turning a stainless kitchen faucet handle with no water flowing into the sink"),
        (3, "VO", "03_Backhoe_Asphalt.mp4", "Yellow backhoe bucket lifting broken wet asphalt from a roadside trench"),
    ]
    return [
        NewsShot(
            shot_index=i,
            segment_type=seg,
            suggested_filename=fn,
            visual_description=desc,
            veo_prompt=wrap_camera_style_anchor(desc, resolution="1080p", aspect_ratio="16:9"),
        )
        for i, seg, fn, desc in items
    ]


class TestNewsBrollEngine(unittest.TestCase):
    def test_filename_sanitization_and_prenumbering(self) -> None:
        self.assertEqual(sanitize_shot_filename("Suburban Street", 1), "01_Suburban_Street.mp4")
        self.assertEqual(sanitize_shot_filename("99_Old_Name.mp4", 3), "03_Old_Name.mp4")
        self.assertEqual(sanitize_shot_filename("", 2, "People walking into city hall"), "02_People_Walking_Into_City.mp4")

    def test_camera_style_anchor_wrapper(self) -> None:
        wrapped = wrap_camera_style_anchor(
            "People walking into a municipal building",
            resolution="1080p",
            aspect_ratio="16:9",
        )
        self.assertIn("Generate a 1080p, 16:9 broadcast news video of People walking into a municipal building.", wrapped)
        self.assertIn("Shot on Sony FX9 with a 35mm lens", wrapped)
        self.assertIn("Neutral Rec.709 color profile", wrapped)

    def test_veo_request_payload_with_reference_still(self) -> None:
        cfg = VeoConfig(provider="vertex", model="veo-3.1-fast-generate-001", resolution="1080p", duration_seconds=5)
        with tempfile.TemporaryDirectory() as tmpdir:
            ref = Path(tmpdir) / "crew_ref.jpg"
            Image.new("RGB", (64, 64), (120, 140, 160)).save(ref, format="JPEG")

            payload = build_veo_request_payload("Test news shot", cfg, ref_image_path=ref, ref_mode="asset")
            self.assertEqual(payload["parameters"]["aspectRatio"], "16:9")
            self.assertEqual(payload["parameters"]["resolution"], "1080p")
            self.assertEqual(payload["parameters"]["durationSeconds"], 8)
            refs = payload["instances"][0]["referenceImages"]
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]["referenceType"], "asset")
            self.assertEqual(refs[0]["image"]["mimeType"], "image/jpeg")
            self.assertTrue(len(refs[0]["image"]["bytesBase64Encoded"]) > 10)

    def test_yap_marker_load_json_and_srt_and_resolve_export(self) -> None:
        shots = _sample_news_shots()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            srt = tmp / "yap.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:04,500\nCrews blocked two lanes.\n\n"
                "2\n00:00:04,500 --> 00:00:09,000\nMaria turned on the kitchen faucet.\n\n"
                "3\n00:00:09,000 --> 00:00:14,000\nUtility backhoe cleared asphalt.\n",
                encoding="utf-8",
            )
            slots = load_yap_markers(srt, fps=30.0)
            self.assertEqual(len(slots), 3)
            self.assertAlmostEqual(slots[1].start_seconds, 4.5, places=2)

            resolved = build_resolve_markers("Water_Main_Pkg", shots, yap_slots=slots, fps=30.0)
            markers = resolved["markers"]
            self.assertEqual(len(markers), 3)
            self.assertEqual(markers[0]["color"], "Green")
            self.assertEqual(markers[1]["color"], "Cyan")
            self.assertEqual(markers[1]["frame"], 135)
            self.assertEqual(seconds_to_timecode(4.5, 30.0), "00:00:04:15")
            self.assertIn("[02_Kitchen_Faucet_Dry.mp4]", markers[1]["note"])

    def test_news_engine_e2e_and_worker_news_routing(self) -> None:
        shots = _sample_news_shots()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            yap_audio = root / "yap_audio"
            yap_audio.mkdir()
            (yap_audio / "00_VO_Track.wav").write_bytes(b"RIFFmockwav")

            cfg = AppConfig(
                input_dir=root / "BrollInput",
                output_dir=root / "BrollOutput",
                archive_dir=root / "BrollInput" / "_processed",
                news_engine=NewsEngineConfig(
                    resolve_watch_dir=root / "ResolveWatch",
                    auto_run_veo=False,
                    fps=30.0,
                ),
            )

            # Direct engine run
            manifest = run_news_package(
                package_id="water_main_pkg",
                script_text="VO: Water main broke.\nSOT: Faucet went dry.",
                cfg=cfg,
                yap_audio_dir=yap_audio,
                prompts_only=True,
                prebuilt_shots=shots,
            )
            pkg_dir = Path(manifest["resolve_package_dir"])
            self.assertTrue((pkg_dir / "resolve_markers.json").exists())
            self.assertTrue((pkg_dir / "resolve_markers.csv").exists())
            self.assertTrue((pkg_dir / "veo_batch_prompts.json").exists())
            self.assertTrue((pkg_dir / "veo_batch_prompts.md").exists())
            self.assertTrue((pkg_dir / "00_Audio_Yap" / "00_VO_Track.wav").exists())

            # Worker #news auto-routing
            cfg.input_dir.mkdir(parents=True, exist_ok=True)
            job_file = cfg.input_dir / "wa_news_01.json"
            job_file.write_text(
                json.dumps({"job_id": "wa_news_01", "sender": "15550001111", "script": "#news\nVO: Crews blocked two lanes."}),
                encoding="utf-8",
            )
            with patch("src.news_engine.segment_news_script", return_value=shots):
                count = process_once(cfg)

            self.assertEqual(count, 1)
            out_json = cfg.output_dir / "wa_news_01_result.json"
            self.assertTrue(out_json.exists())
            res_data = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(res_data["engine"], "news-broll-sot-engine")
            self.assertIn("01_Suburban_Street.mp4", res_data["whatsapp_message"])


if __name__ == "__main__":
    unittest.main()
