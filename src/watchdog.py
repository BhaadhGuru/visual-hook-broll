"""Always-on Windows watchdog & WhatsApp remote controller for B-Roll, Yap, and MediaMTX.

Provides three safety nets:
1. Auto-starts B-Roll, Yap, and MediaMTX at Windows logon.
2. Auto-restarts MediaMTX within seconds if it crashes, and auto-starts B-Roll/Yap
   on demand whenever a script lands in BrollInput or YapInput.
3. Accepts WhatsApp control commands (#status, #start, #restart, #stop, #help)
   dropped into BrollInput (or BrollInput/_control) by n8n and replies via
   BrollOutput/*_result.json.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig, load_config
from .setup_detect import backup_bat_scripts, detect_comfyui, detect_ollama


GITHUB_ARCHIVE_URL = "https://github.com/BhaadhGuru/visual-hook-broll/archive/refs/heads/main.zip"
CONTROL_COMMANDS = {"#start", "#restart", "#stop", "#status", "#update", "#help"}
SERVICE_ALIASES = {
    "all": "all",
    "broll": "broll",
    "b-roll": "broll",
    "worker": "broll",
    "yap": "yap",
    "stream": "stream",
    "mediamtx": "stream",
    "livestream": "stream",
    "mtx": "stream",
}


@dataclass
class ServiceSpec:
    key: str
    label: str
    command_path: str
    match_tokens: tuple[str, ...]
    auto_restart_on_crash: bool = False


class ProcessController(Protocol):
    def is_running(self, spec: ServiceSpec) -> bool: ...
    def start(self, spec: ServiceSpec, project_root: Path) -> tuple[bool, str]: ...
    def stop(self, spec: ServiceSpec) -> tuple[bool, str]: ...


class WindowsProcessController:
    """Real Windows process inspector/launcher using CIM + subprocess."""

    def _find_pids(self, spec: ServiceSpec) -> list[int]:
        if os.name != "nt":
            return []
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return []
            raw = json.loads(proc.stdout)
            rows = raw if isinstance(raw, list) else [raw]
        except Exception:
            return []

        my_pid = os.getpid()
        pids: list[int] = []
        for row in rows:
            pid = int(row.get("ProcessId") or 0)
            if pid <= 0 or pid == my_pid:
                continue
            haystack = f"{row.get('Name') or ''} {row.get('CommandLine') or ''}".lower()
            if "src.watchdog" in haystack:
                continue
            if any(tok.lower() in haystack for tok in spec.match_tokens if tok):
                pids.append(pid)
        return pids

    def is_running(self, spec: ServiceSpec) -> bool:
        return bool(self._find_pids(spec))

    def start(self, spec: ServiceSpec, project_root: Path) -> tuple[bool, str]:
        if not spec.command_path:
            return False, f"{spec.label} path is not configured yet (run SETUP.bat)."
        target = Path(spec.command_path)
        if not target.is_absolute():
            target = (project_root / target).resolve()
        if not target.exists():
            return False, f"{spec.label} file not found: {target}"

        cwd = str(target.parent)
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            if target.suffix.lower() in {".bat", ".cmd"}:
                subprocess.Popen(
                    ["cmd.exe", "/c", str(target)],
                    cwd=cwd,
                    creationflags=creationflags,
                )
            else:
                subprocess.Popen([str(target)], cwd=cwd, creationflags=creationflags)
            return True, f"Started {spec.label}"
        except Exception as exc:
            return False, f"Failed to start {spec.label}: {exc}"

    def stop(self, spec: ServiceSpec) -> tuple[bool, str]:
        pids = self._find_pids(spec)
        if not pids:
            return True, f"{spec.label} was not running"
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                continue
        return True, f"Stopped {spec.label}"


def build_service_specs(cfg: AppConfig) -> dict[str, ServiceSpec]:
    """Build the three supervised service definitions from AppConfig."""
    broll_path = cfg.watchdog.broll_bat or "START_BROLL.bat"
    yap_path = cfg.watchdog.yap_bat
    mtx_path = cfg.watchdog.mediamtx_bat

    yap_tokens = tuple(t for t in (Path(yap_path).name if yap_path else "", "yap") if t)
    mtx_tokens = tuple(t for t in (Path(mtx_path).name if mtx_path else "", "mediamtx") if t)

    return {
        "broll": ServiceSpec(
            key="broll",
            label="B-Roll Worker",
            command_path=broll_path,
            match_tokens=("src.worker", "start_broll.bat"),
            auto_restart_on_crash=False,
        ),
        "yap": ServiceSpec(
            key="yap",
            label="Yap Worker",
            command_path=yap_path,
            match_tokens=yap_tokens,
            auto_restart_on_crash=False,
        ),
        "stream": ServiceSpec(
            key="stream",
            label="MediaMTX Livestream",
            command_path=mtx_path,
            match_tokens=mtx_tokens,
            auto_restart_on_crash=cfg.watchdog.mediamtx_auto_restart,
        ),
    }


def pull_latest_from_github(project_root: Path, archive_url: str = GITHUB_ARCHIVE_URL) -> tuple[bool, str]:
    """Update project_root from GitHub via git pull (if git repo) or ZIP download (if unzipped)."""
    git_dir = project_root / ".git"
    if git_dir.exists():
        try:
            proc = subprocess.run(
                ["git", "pull", "--ff-only", "origin", "main"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0:
                summary = (proc.stdout.strip().splitlines() or ["Updated"])[-1]
                return True, f"git pull OK ({summary})"
            return False, f"git pull failed: {(proc.stderr or proc.stdout).strip()[:200]}"
        except Exception as exc:
            return False, f"git pull error: {exc}"

    protected = {"config.json", ".env"}
    try:
        req = urllib.request.Request(archive_url, headers={"User-Agent": "visual-hook-broll-watchdog"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_zip = resp.read()
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            updated_count = 0
            for member in zf.infolist():
                if member.is_dir():
                    continue
                parts = Path(member.filename).parts
                if len(parts) < 2:
                    continue
                rel = Path(*parts[1:])
                if rel.parts[0] in {".venv", "venv", ".git"} or rel.name in protected:
                    continue
                dest = project_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                updated_count += 1
        return True, f"Downloaded & extracted {updated_count} file(s) from GitHub"
    except Exception as exc:
        return False, f"ZIP update failed: {exc}"


def parse_command(text: str) -> tuple[str, str] | None:
    """Parse '#start all', '#restart stream', '#stop stream', '#status', '#update', '#help'."""
    parts = text.strip().split()
    if not parts:
        return None
    action = parts[0].lower()
    if action not in CONTROL_COMMANDS:
        return None
    if action in {"#status", "#help", "#update"}:
        return action, "all"
    raw_target = parts[1].lower() if len(parts) > 1 else "all"
    target = SERVICE_ALIASES.get(raw_target, raw_target)
    return action, target


class WatchdogSupervisor:
    """Supervises B-Roll, Yap, and MediaMTX and handles WhatsApp control messages."""

    def __init__(
        self,
        cfg: AppConfig,
        controller: ProcessController | None = None,
        *,
        ollama_probe=detect_ollama,
        comfy_probe=detect_comfyui,
        updater=pull_latest_from_github,
    ) -> None:
        self.cfg = cfg
        self.controller = controller or WindowsProcessController()
        self.ollama_probe = ollama_probe
        self.comfy_probe = comfy_probe
        self.updater = updater
        self.services = build_service_specs(cfg)
        self.manually_stopped: set[str] = set()
        self.last_sender: str = cfg.watchdog.notify_phone
        self._last_restart_ts: dict[str, float] = {}

    def _write_whatsapp_reply(
        self,
        *,
        job_id: str,
        sender: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> Path:
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "mode": "watchdog",
            "sender": sender,
            "chat_id": chat_id or sender,
            "message_id": message_id,
            "whatsapp_message": text,
            "storyboard_filename": None,
            "whatsapp_image_caption": "",
        }
        out_path = self.cfg.output_dir / f"{job_id}_result.json"
        tmp_path = self.cfg.output_dir / f".{job_id}_result.json.tmp"
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(out_path)
        return out_path

    def format_status(self) -> str:
        lines = ["🖥️ *PC Studio Status*", ""]
        for key in ("broll", "yap", "stream"):
            spec = self.services[key]
            if not spec.command_path:
                lines.append(f"⚪ {spec.label}: Not configured")
            elif self.controller.is_running(spec):
                lines.append(f"🟢 {spec.label}: Running")
            elif key in self.manually_stopped:
                lines.append(f"⏸️ {spec.label}: Stopped (manual)")
            else:
                lines.append(f"🔴 {spec.label}: Stopped")

        ollama = self.ollama_probe(self.cfg.ollama.base_url)
        comfy = self.comfy_probe(self.cfg.comfyui.base_url)
        o_ok = ollama.get("reachable") if isinstance(ollama, dict) else getattr(ollama, "reachable", False)
        o_model = ollama.get("selected_model") if isinstance(ollama, dict) else getattr(ollama, "selected_model", "")
        c_ok = comfy.get("reachable") if isinstance(comfy, dict) else getattr(comfy, "reachable", False)
        c_layout_obj = comfy.get("layout", "Reachable") if isinstance(comfy, dict) else getattr(comfy, "layout", "Reachable")
        c_layout = c_layout_obj if isinstance(c_layout_obj, str) else getattr(c_layout_obj, "layout", "Reachable")
        lines.append(f"{'🟢' if o_ok else '🔴'} Ollama: {o_model or 'Reachable' if o_ok else 'Offline'}")
        lines.append(f"{'🟢' if c_ok else '🔴'} ComfyUI: {c_layout if c_ok else 'Offline'}")
        lines.append("")
        lines.append(
            "Commands: `#status`, `#start all`, `#restart stream`, `#restart yap`, `#restart broll`, `#stop stream`, `#update`"
        )
        return "\n".join(lines)

    def _run_self_update(self) -> str:
        ok, detail = self.updater(self.cfg.project_root)
        if not ok:
            return f"⚠️ Update failed: {detail}"

        lines = [f"✅ *Update complete:* {detail}"]
        backup_dir_str = self.cfg.watchdog.bat_backup_dir
        if backup_dir_str:
            try:
                copied = backup_bat_scripts(
                    self.cfg.project_root,
                    Path(backup_dir_str),
                    [self.cfg.watchdog.yap_bat, self.cfg.watchdog.mediamtx_bat],
                )
                if copied:
                    lines.append(f"💾 Backed up {len(copied)} `.bat` script(s) to Google Drive.")
            except Exception as exc:
                lines.append(f"⚠️ `.bat` backup skipped: {exc}")

        broll_spec = self.services["broll"]
        if broll_spec.command_path and "broll" not in self.manually_stopped:
            self.controller.stop(broll_spec)
            rok, rmsg = self.controller.start(broll_spec, self.cfg.project_root)
            lines.append(f"{'🔄' if rok else '⚠️'} Restarted B-Roll Worker" if rok else f"⚠️ {rmsg}")
        return "\n".join(lines)

    def execute_command(self, action: str, target: str) -> str:
        if action == "#help":
            return (
                "🛠️ *PC Watchdog Commands*\n"
                "• `#status` — Check B-Roll, Yap, MediaMTX, Ollama & ComfyUI\n"
                "• `#start all` (or `broll` / `yap` / `stream`) — Start if not running\n"
                "• `#restart all` (or `broll` / `yap` / `stream`) — Force-restart\n"
                "• `#stop stream` (or `broll` / `yap` / `all`) — Stop service\n"
                "• `#update` — Pull latest code from GitHub, back up `.bat` scripts, restart B-Roll"
            )
        if action == "#status":
            return self.format_status()
        if action == "#update":
            return self._run_self_update()

        keys = ["broll", "yap", "stream"] if target == "all" else [target]
        unknown = [k for k in keys if k not in self.services]
        if unknown:
            return f"⚠️ Unknown service `{unknown[0]}`. Use `all`, `broll`, `yap`, or `stream`."

        results: list[str] = []
        for key in keys:
            spec = self.services[key]
            if not spec.command_path:
                results.append(f"⚪ {spec.label}: not configured")
                continue

            if action == "#stop":
                self.manually_stopped.add(key)
                ok, msg = self.controller.stop(spec)
                results.append(f"{'🛑' if ok else '⚠️'} {msg}")
            elif action == "#start":
                self.manually_stopped.discard(key)
                if self.controller.is_running(spec):
                    results.append(f"🟢 {spec.label}: already running")
                else:
                    ok, msg = self.controller.start(spec, self.cfg.project_root)
                    results.append(f"{'✅' if ok else '⚠️'} {msg}")
            elif action == "#restart":
                self.manually_stopped.discard(key)
                self.controller.stop(spec)
                ok, msg = self.controller.start(spec, self.cfg.project_root)
                results.append(f"{'🔄' if ok else '⚠️'} Restarted {spec.label}" if ok else f"⚠️ {msg}")

        return "\n".join(results)

    def boot_start(self) -> list[str]:
        """Ensure configured services are running when the watchdog boots."""
        if not self.cfg.watchdog.auto_start_on_boot:
            return []
        started: list[str] = []
        for key in ("broll", "yap", "stream"):
            spec = self.services[key]
            if spec.command_path and not self.controller.is_running(spec):
                ok, msg = self.controller.start(spec, self.cfg.project_root)
                if ok:
                    started.append(msg)
        return started

    def _has_pending_files(self, folder: Path, *, ignore_commands: bool = False) -> bool:
        if not folder.exists() or not folder.is_dir():
            return False
        for p in folder.iterdir():
            if not p.is_file() or p.name.startswith(".") or p.name.endswith(".tmp"):
                continue
            if p.suffix.lower() not in {".json", ".txt"}:
                continue
            if ignore_commands:
                try:
                    raw = p.read_text(encoding="utf-8-sig")
                    text = json.loads(raw).get("script", "") if p.suffix.lower() == ".json" else raw
                    if parse_command(str(text)) is not None:
                        continue
                except Exception:
                    pass
            return True
        return False

    def _check_control_files(self) -> int:
        handled = 0
        self.cfg.input_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.archive_dir.mkdir(parents=True, exist_ok=True)
        scan_dirs = [self.cfg.input_dir]
        ctrl_dir = self.cfg.input_dir / "_control"
        if ctrl_dir.exists():
            scan_dirs.append(ctrl_dir)

        for folder in scan_dirs:
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in {".json", ".txt"}:
                    continue
                if path.name.startswith(".") or path.name.endswith(".tmp"):
                    continue
                try:
                    if path.suffix.lower() == ".json":
                        data = json.loads(path.read_text(encoding="utf-8-sig"))
                        text = str(data.get("script") or data.get("text") or data.get("body") or "")
                        sender = str(data.get("sender") or data.get("from") or "")
                        chat_id = str(data.get("chat_id") or sender)
                        message_id = str(data.get("message_id") or "")
                        job_id = str(data.get("job_id") or path.stem)
                    else:
                        text = path.read_text(encoding="utf-8-sig")
                        sender = chat_id = message_id = ""
                        job_id = f"wd_{path.stem}"
                except Exception:
                    continue

                parsed = parse_command(text)
                if parsed is None:
                    continue

                if sender:
                    self.last_sender = sender

                action, target = parsed
                reply = self.execute_command(action, target)
                self._write_whatsapp_reply(
                    job_id=job_id,
                    sender=sender or self.last_sender,
                    chat_id=chat_id or sender or self.last_sender,
                    message_id=message_id,
                    text=reply,
                )
                dest = self.cfg.archive_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
                shutil.move(str(path), str(dest))
                print(f"[watchdog] Executed {action} {target} ({path.name})")
                handled += 1
        return handled

    def _check_auto_recovery_and_on_demand(self, now: float | None = None) -> list[str]:
        ts = now if now is not None else time.time()
        actions: list[str] = []

        # 1. MediaMTX crash auto-restart (15s cooldown to avoid rapid-fire loops)
        stream_spec = self.services["stream"]
        if (
            stream_spec.command_path
            and stream_spec.auto_restart_on_crash
            and "stream" not in self.manually_stopped
            and not self.controller.is_running(stream_spec)
        ):
            if ts - self._last_restart_ts.get("stream", 0.0) >= 15.0:
                ok, msg = self.controller.start(stream_spec, self.cfg.project_root)
                self._last_restart_ts["stream"] = ts
                actions.append(f"auto-restart stream: {msg}")
                if self.last_sender:
                    self._write_whatsapp_reply(
                        job_id=f"watchdog_stream_{int(ts)}",
                        sender=self.last_sender,
                        chat_id=self.last_sender,
                        message_id="",
                        text=(
                            f"🔄 *MediaMTX Livestream* stopped unexpectedly and was automatically restarted."
                            if ok
                            else f"⚠️ *MediaMTX Livestream* crashed and auto-restart failed: {msg}"
                        ),
                    )

        # 2. On-demand start for B-Roll if a script landed in BrollInput
        broll_spec = self.services["broll"]
        if (
            broll_spec.command_path
            and "broll" not in self.manually_stopped
            and not self.controller.is_running(broll_spec)
            and self._has_pending_files(self.cfg.input_dir, ignore_commands=True)
        ):
            if ts - self._last_restart_ts.get("broll", 0.0) >= 15.0:
                ok, msg = self.controller.start(broll_spec, self.cfg.project_root)
                self._last_restart_ts["broll"] = ts
                actions.append(f"on-demand broll: {msg}")

        # 3. On-demand start for Yap if a script landed in YapInput
        yap_spec = self.services["yap"]
        if (
            yap_spec.command_path
            and "yap" not in self.manually_stopped
            and not self.controller.is_running(yap_spec)
            and self._has_pending_files(self.cfg.watchdog.yap_input_dir)
        ):
            if ts - self._last_restart_ts.get("yap", 0.0) >= 15.0:
                ok, msg = self.controller.start(yap_spec, self.cfg.project_root)
                self._last_restart_ts["yap"] = ts
                actions.append(f"on-demand yap: {msg}")

        return actions

    def tick(self) -> dict[str, Any]:
        """Run one watchdog cycle: handle WhatsApp commands + crash recovery + on-demand launch."""
        commands_handled = self._check_control_files()
        auto_actions = self._check_auto_recovery_and_on_demand()
        return {"commands_handled": commands_handled, "auto_actions": auto_actions}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Always-on watchdog for B-Roll, Yap, and MediaMTX.")
    ap.add_argument("--config", default=None, help="Path to config.json")
    ap.add_argument("--once", action="store_true", help="Run a single check cycle and exit")
    ap.add_argument("--no-boot-start", action="store_true", help="Skip auto-starting services on launch")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    sup = WatchdogSupervisor(cfg)

    print(f"[watchdog] Supervising B-Roll, Yap, and MediaMTX (poll={cfg.watchdog.poll_interval_seconds}s)")
    if not args.no_boot_start:
        started = sup.boot_start()
        for msg in started:
            print(f"[watchdog] Boot: {msg}")

    if args.once:
        sup.tick()
        return 0

    try:
        while True:
            sup.tick()
            time.sleep(cfg.watchdog.poll_interval_seconds)
    except KeyboardInterrupt:
        print("\n[watchdog] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
