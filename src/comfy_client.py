"""Local ComfyUI REST API client for Flux storyboard frame generation."""

from __future__ import annotations

import copy
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .config import ComfyUIConfig


def render_workflow_template(
    template: dict[str, Any],
    *,
    prompt_text: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    guidance: float,
    checkpoint_name: str,
    filename_prefix: str,
) -> dict[str, Any]:
    """Deep-substitute placeholder tokens inside an exported ComfyUI API workflow dict."""
    replacements: dict[str, Any] = {
        "{{PROMPT}}": prompt_text,
        "{{SEED}}": seed,
        "{{WIDTH}}": width,
        "{{HEIGHT}}": height,
        "{{STEPS}}": steps,
        "{{CFG}}": cfg,
        "{{GUIDANCE}}": guidance,
        "{{CHECKPOINT}}": checkpoint_name,
        "{{FILENAME_PREFIX}}": filename_prefix,
    }

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            if node in replacements:
                return replacements[node]
            updated = node
            for token, value in replacements.items():
                if token in updated:
                    updated = updated.replace(token, str(value))
            return updated
        return node

    return _walk(copy.deepcopy(template))


class ComfyUIClient:
    """Minimal zero-dependency HTTP client for a local ComfyUI server."""

    def __init__(self, cfg: ComfyUIConfig, workflow_path: Path) -> None:
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.workflow_path = workflow_path
        if not workflow_path.exists():
            raise FileNotFoundError(f"ComfyUI workflow template not found: {workflow_path}")
        self.template = json.loads(workflow_path.read_text(encoding="utf-8"))
        self.client_id = f"visual-hook-broll-{uuid.uuid4().hex[:8]}"

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ComfyUI HTTP {exc.code} at {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach ComfyUI at {url}: {exc.reason}") from exc

    def health_check(self) -> dict[str, Any]:
        return self._request_json("GET", "/system_stats", timeout=10)

    def free_vram(self) -> None:
        """Ask ComfyUI to unload models and free GPU VRAM after finishing a batch."""
        self._request_json("POST", "/free", {"unload_models": True, "free_memory": True}, timeout=30)

    def generate_image(
        self,
        prompt_text: str,
        output_path: Path,
        *,
        width: int | None = None,
        height: int | None = None,
        seed: int | None = None,
        filename_prefix: str = "broll_panel",
    ) -> Path:
        """Queue a single Flux workflow in ComfyUI, wait for completion, and save the PNG."""
        actual_seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        workflow = render_workflow_template(
            self.template,
            prompt_text=prompt_text,
            seed=actual_seed,
            width=width or self.cfg.width,
            height=height or self.cfg.height,
            steps=self.cfg.steps,
            cfg=self.cfg.cfg,
            guidance=self.cfg.guidance,
            checkpoint_name=self.cfg.checkpoint_name,
            filename_prefix=filename_prefix,
        )

        queued = self._request_json(
            "POST",
            "/prompt",
            {"prompt": workflow, "client_id": self.client_id},
            timeout=30,
        )
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not return prompt_id: {queued}")

        deadline = time.monotonic() + self.cfg.timeout_seconds
        history_entry: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            history = self._request_json("GET", f"/history/{prompt_id}", timeout=20)
            if prompt_id in history:
                history_entry = history[prompt_id]
                status = history_entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI execution error for {prompt_id}: {status}")
                if history_entry.get("outputs"):
                    break
            time.sleep(1.0)

        if not history_entry or not history_entry.get("outputs"):
            raise TimeoutError(
                f"Timed out after {self.cfg.timeout_seconds}s waiting for ComfyUI prompt {prompt_id}"
            )

        image_meta = _extract_first_image(history_entry["outputs"])
        if image_meta is None:
            raise RuntimeError(f"ComfyUI finished prompt {prompt_id} without any image output.")

        query = urllib.parse.urlencode(
            {
                "filename": image_meta["filename"],
                "subfolder": image_meta.get("subfolder", ""),
                "type": image_meta.get("type", "output"),
            }
        )
        view_url = f"{self.base_url}/view?{query}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(view_url, timeout=60) as resp:
            output_path.write_bytes(resp.read())

        return output_path


def _extract_first_image(outputs: dict[str, Any]) -> dict[str, Any] | None:
    for node_output in outputs.values():
        images = node_output.get("images")
        if images:
            return images[0]
    return None
