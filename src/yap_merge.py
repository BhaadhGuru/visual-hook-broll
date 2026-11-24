"""Merge News B-Roll shots with Yap audio markers & export DaVinci Resolve marker kits."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .news_segmenter import NewsShot


@dataclass
class YapMarkerSlot:
    index: int
    start_seconds: float
    end_seconds: float
    label: str = ""
    audio_file: str = ""


def seconds_to_frame(seconds: float, fps: float = 29.97) -> int:
    return int(round(max(0.0, seconds) * fps))


def seconds_to_timecode(seconds: float, fps: float = 29.97) -> str:
    nominal_fps = int(round(fps))
    total_frames = int(round(max(0.0, seconds) * fps))
    frames = total_frames % nominal_fps
    total_sec = total_frames // nominal_fps
    secs = total_sec % 60
    mins = (total_sec // 60) % 60
    hours = total_sec // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def _parse_srt_timestamp(ts: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        return 0.0
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def load_yap_markers(marker_path: Path, *, fps: float = 29.97) -> list[YapMarkerSlot]:
    """Load audio/timeline markers from Yap JSON (Resolve-compatible) or SRT/VTT transcripts."""
    if not marker_path.exists():
        raise FileNotFoundError(f"Yap marker file not found: {marker_path}")

    suffix = marker_path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        items = raw if isinstance(raw, list) else raw.get("markers") or raw.get("segments") or []
        slots: list[YapMarkerSlot] = []
        for idx, item in enumerate(items, start=1):
            if "start" in item:
                start = float(item["start"])
            elif "frame" in item:
                start = float(item["frame"]) / fps
            else:
                start = (idx - 1) * 4.0
            if "end" in item:
                end = float(item["end"])
            elif "duration" in item and float(item["duration"]) > 1:
                end = start + float(item["duration"]) / fps
            else:
                end = start + 4.0
            slots.append(
                YapMarkerSlot(
                    index=int(item.get("index", idx)),
                    start_seconds=start,
                    end_seconds=max(end, start + 3.0),
                    label=str(item.get("name") or item.get("note") or item.get("text") or ""),
                    audio_file=str(item.get("audio_file") or item.get("source") or ""),
                )
            )
        return slots

    if suffix in {".srt", ".vtt"}:
        text = marker_path.read_text(encoding="utf-8-sig")
        blocks = re.split(r"\r?\n\r?\n+", text.strip())
        slots = []
        idx = 1
        for block in blocks:
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            time_line = next((ln for ln in lines if "-->" in ln), None)
            if not time_line:
                continue
            parts = time_line.split("-->")
            start = _parse_srt_timestamp(parts[0])
            end = _parse_srt_timestamp(parts[1].split()[0])
            caption = " ".join(ln for ln in lines if ln != time_line and not ln.isdigit())
            slots.append(YapMarkerSlot(index=idx, start_seconds=start, end_seconds=end, label=caption))
            idx += 1
        return slots

    raise ValueError(f"Unsupported Yap marker format: {marker_path.suffix} (expected .json, .srt, or .vtt)")


def build_resolve_markers(
    project_name: str,
    shots: list[NewsShot],
    *,
    yap_slots: list[YapMarkerSlot] | None = None,
    fps: float = 29.97,
    default_pacing_seconds: float = 4.0,
) -> dict[str, Any]:
    """Map News B-Roll shots onto Yap audio timecodes and emit resolve_markers.py-compatible JSON."""
    markers: list[dict[str, Any]] = []
    cursor = 0.0

    for idx, shot in enumerate(shots):
        if yap_slots and idx < len(yap_slots):
            slot = yap_slots[idx]
            start_s = slot.start_seconds
            end_s = max(slot.end_seconds, start_s + 3.0)
            yap_ref = slot.label
        else:
            start_s = cursor
            end_s = cursor + default_pacing_seconds
            yap_ref = ""

        cursor = end_s
        frame = seconds_to_frame(start_s, fps)
        duration_frames = max(1, seconds_to_frame(end_s - start_s, fps))
        tc = seconds_to_timecode(start_s, fps)

        # Green = VO B-roll, Cyan = SOT cover shot (matches existing Resolve marker conventions)
        color = "Cyan" if shot.segment_type == "SOT" else "Green"
        note_parts = [shot.visual_description, f"[{shot.suggested_filename}]"]
        if yap_ref:
            note_parts.append(f"(Yap: {yap_ref[:80]})")

        markers.append(
            {
                "index": shot.shot_index,
                "decision": "broll",
                "segment_type": shot.segment_type,
                "suggested_filename": shot.suggested_filename,
                "start": round(start_s, 3),
                "end": round(end_s, 3),
                "frame": frame,
                "timecode": tc,
                "duration": duration_frames,
                "color": color,
                "name": f"B-ROLL [{shot.segment_type}]: {shot.suggested_filename}",
                "note": " ".join(note_parts),
                "customData": f"news:{shot.segment_type.lower()}:{shot.shot_index}:{shot.suggested_filename}",
            }
        )

    return {
        "project": project_name,
        "engine": "news-broll-sot-engine",
        "fps": fps,
        "shot_count": len(shots),
        "markers": markers,
    }


def write_resolve_marker_csv(marker_payload: dict[str, Any], csv_path: Path) -> Path:
    """Export standard DaVinci Resolve Edit Index / Marker CSV alongside the JSON."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["#", "Timecode", "Frame", "DurationFrames", "Color", "SegmentType", "Filename", "Name", "Note"]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for m in marker_payload.get("markers", []):
            writer.writerow(
                {
                    "#": m["index"],
                    "Timecode": m["timecode"],
                    "Frame": m["frame"],
                    "DurationFrames": m["duration"],
                    "Color": m["color"],
                    "SegmentType": m["segment_type"],
                    "Filename": m["suggested_filename"],
                    "Name": m["name"],
                    "Note": m["note"],
                }
            )
    return csv_path


def stage_yap_audio_tracks(yap_dir: Path | None, resolve_package_dir: Path) -> list[str]:
    """Copy matching Yap VO/SOT audio tracks into the DaVinci Resolve watched package folder."""
    if yap_dir is None or not yap_dir.exists():
        return []
    audio_exts = {".wav", ".mp3", ".m4a", ".aac", ".flac"}
    staged: list[str] = []
    audio_dest = resolve_package_dir / "00_Audio_Yap"
    for f in sorted(yap_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in audio_exts:
            audio_dest.mkdir(parents=True, exist_ok=True)
            dst = audio_dest / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
            staged.append(str(dst))
    return staged
