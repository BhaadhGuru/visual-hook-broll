"""Veo 3.1 REST API client for News B-Roll clip generation (Vertex AI GA & Gemini API).

Note on API endpoints (as of Nov 2026):
- Gemini API preview IDs (`veo-3.1-generate-preview`) were retired Oct 22, 2026.
- Vertex AI / Gemini Enterprise GA uses `veo-3.1-generate-001` or `veo-3.1-fast-generate-001`
  via `predictLongRunning` + `fetchPredictOperation`.
- Both Vertex AI GA and custom Gemini API endpoints are supported cleanly via config.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VeoConfig:
    provider: str = "vertex"  # "vertex" (GA veo-3.1-generate-001) or "gemini_api"
    model: str = "veo-3.1-fast-generate-001"
    resolution: str = "1080p"  # "720p", "1080p", or "4k"
    aspect_ratio: str = "16:9"
    duration_seconds: int = 8  # Veo 3.1 1080p & referenceImages require 8s; trim to 3-5s in Resolve
    generate_audio: bool = False  # News B-roll typically uses natural SOT/VO tracks in Resolve
    poll_interval_seconds: float = 10.0
    timeout_seconds: int = 600
    # Vertex AI settings (also readable from env: VERTEX_PROJECT_ID, VERTEX_LOCATION, GOOGLE_ACCESS_TOKEN)
    vertex_project_id: str = ""
    vertex_location: str = "us-central1"
    # Gemini API settings (also readable from env: GEMINI_API_KEY)
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"


def encode_reference_image(image_path: Path) -> dict[str, str]:
    """Encode a local reference still for Veo 3.1 (`bytesBase64Encoded` + `mimeType`)."""
    if not image_path.exists():
        raise FileNotFoundError(f"Reference image not found: {image_path}")
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "bytesBase64Encoded": encoded,
        "mimeType": mime_type,
    }


def build_veo_request_payload(
    prompt: str,
    cfg: VeoConfig,
    *,
    ref_image_path: Path | None = None,
    ref_mode: str = "asset",  # "asset" (referenceImages style/lighting match) or "first_frame"
) -> dict[str, Any]:
    """Build a Veo 3.1 `predictLongRunning` JSON request body."""
    instance: dict[str, Any] = {"prompt": prompt}

    duration = cfg.duration_seconds
    if ref_image_path is not None:
        img_obj = encode_reference_image(ref_image_path)
        if ref_mode == "first_frame":
            instance["image"] = img_obj
        else:
            instance["referenceImages"] = [
                {
                    "image": img_obj,
                    "referenceType": "asset",
                }
            ]
            # Veo 3.1 referenceImages requires 8s duration
            duration = 8

    if cfg.resolution in {"1080p", "4k"} and duration < 8:
        duration = 8

    parameters: dict[str, Any] = {
        "aspectRatio": cfg.aspect_ratio,
        "resolution": cfg.resolution,
        "durationSeconds": duration,
        "sampleCount": 1,
    }
    if cfg.provider == "vertex":
        parameters["generateAudio"] = cfg.generate_audio

    return {
        "instances": [instance],
        "parameters": parameters,
    }


class VeoClient:
    """Minimal stdlib HTTP client for Veo 3.1 video generation."""

    def __init__(self, cfg: VeoConfig) -> None:
        self.cfg = cfg

    def _get_vertex_token(self) -> str:
        token = os.environ.get("GOOGLE_ACCESS_TOKEN", "").strip()
        if token:
            return token
        try:
            res = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                text=True,
                capture_output=True,
                check=True,
                timeout=15,
            )
            token = res.stdout.strip()
            if token:
                return token
        except Exception as exc:
            raise RuntimeError(
                "Vertex AI Veo 3.1 requires GOOGLE_ACCESS_TOKEN env var or an active `gcloud auth login` session."
            ) from exc
        raise RuntimeError("Empty access token returned by gcloud.")

    def _headers(self) -> dict[str, str]:
        if self.cfg.provider == "vertex":
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._get_vertex_token()}",
            }
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required for gemini_api provider.")
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

    def _predict_url(self) -> str:
        if self.cfg.provider == "vertex":
            project = self.cfg.vertex_project_id or os.environ.get("VERTEX_PROJECT_ID", "").strip()
            location = self.cfg.vertex_location or os.environ.get("VERTEX_LOCATION", "us-central1").strip()
            if not project:
                raise RuntimeError(
                    "Vertex AI requires vertex_project_id in config.json or VERTEX_PROJECT_ID env var."
                )
            return (
                f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
                f"/locations/{location}/publishers/google/models/{self.cfg.model}:predictLongRunning"
            )
        base = self.cfg.gemini_base_url.rstrip("/")
        return f"{base}/models/{self.cfg.model}:predictLongRunning"

    def _poll_operation(self, op_name: str, headers: dict[str, str]) -> dict[str, Any]:
        deadline = time.monotonic() + self.cfg.timeout_seconds
        while time.monotonic() < deadline:
            if self.cfg.provider == "vertex":
                project = self.cfg.vertex_project_id or os.environ.get("VERTEX_PROJECT_ID", "").strip()
                location = self.cfg.vertex_location or os.environ.get("VERTEX_LOCATION", "us-central1").strip()
                poll_url = (
                    f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
                    f"/locations/{location}/publishers/google/models/{self.cfg.model}:fetchPredictOperation"
                )
                req = urllib.request.Request(
                    poll_url,
                    data=json.dumps({"operationName": op_name}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
            else:
                base = self.cfg.gemini_base_url.rstrip("/")
                poll_url = f"{base}/{op_name.lstrip('/')}"
                req = urllib.request.Request(poll_url, headers=headers, method="GET")

            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("done"):
                if "error" in data:
                    raise RuntimeError(f"Veo 3.1 operation failed: {data['error']}")
                return data
            time.sleep(self.cfg.poll_interval_seconds)

        raise TimeoutError(f"Timed out after {self.cfg.timeout_seconds}s waiting for Veo operation {op_name}")

    def generate_clip(
        self,
        prompt: str,
        output_path: Path,
        *,
        ref_image_path: Path | None = None,
        ref_mode: str = "asset",
    ) -> Path:
        """Submit one wrapped news B-roll prompt to Veo 3.1 and save the resulting MP4."""
        headers = self._headers()
        payload = build_veo_request_payload(
            prompt,
            self.cfg,
            ref_image_path=ref_image_path,
            ref_mode=ref_mode,
        )
        req = urllib.request.Request(
            self._predict_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                op = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Veo 3.1 HTTP {exc.code}: {detail}") from exc

        op_name = op.get("name")
        if not op_name:
            raise RuntimeError(f"Veo 3.1 did not return an operation name: {op}")

        finished = self._poll_operation(op_name, headers)
        video_bytes = _extract_video_bytes(finished, headers)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(video_bytes)
        return output_path


def _extract_video_bytes(operation_result: dict[str, Any], headers: dict[str, str]) -> bytes:
    """Support both Vertex AI inline base64 `videos[0].bytesBase64Encoded` and Gemini API `uri`."""
    resp = operation_result.get("response", {})

    # 1. Vertex AI format: response.videos[0].bytesBase64Encoded
    videos = resp.get("videos", [])
    if videos:
        first = videos[0]
        if first.get("bytesBase64Encoded"):
            return base64.b64decode(first["bytesBase64Encoded"])
        if first.get("gcsUri"):
            raise RuntimeError(
                f"Veo returned a GCS URI ({first['gcsUri']}) instead of inline bytes. "
                "Omit storageUri or download from GCS with `gcloud storage cp`."
            )

    # 2. Gemini API format: response.generateVideoResponse.generatedSamples[0].video.uri
    samples = resp.get("generateVideoResponse", {}).get("generatedSamples", [])
    if samples:
        video_info = samples[0].get("video", {})
        if video_info.get("bytesBase64Encoded"):
            return base64.b64decode(video_info["bytesBase64Encoded"])
        uri = video_info.get("uri")
        if uri:
            dl_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            req = urllib.request.Request(uri, headers=dl_headers, method="GET")
            with urllib.request.urlopen(req, timeout=180) as dl_resp:
                return dl_resp.read()

    raise RuntimeError(f"Could not find video payload in Veo 3.1 response: {json.dumps(operation_result)[:600]}")
