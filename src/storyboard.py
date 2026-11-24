"""Pillow storyboard contact-sheet compositor for photographer visual instructions."""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .director import ShotListPlan, ShotPanel


FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fit_panel_image(image_path: Path, target_w: int, target_h: int) -> Image.Image:
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        return ImageOps.fit(rgb, (target_w, target_h), method=Image.Resampling.LANCZOS)


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    bg_color: tuple[int, int, int],
    fg_color: tuple[int, int, int],
    pad_x: int = 12,
    pad_y: int = 5,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    rect = (x, y, x + tw + pad_x * 2, y + th + pad_y * 2)
    draw.rounded_rectangle(rect, radius=8, fill=bg_color)
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=fg_color)
    return rect[2]


def compose_storyboard_grid(
    plan: ShotListPlan,
    panel_images: list[Path],
    output_path: Path,
    *,
    columns: int = 2,
    panel_width: int = 960,
    panel_height: int = 540,
    aspect: str = "16:9",
) -> Path:
    """Stitch individual shot reference frames into a labeled photographer shoot sheet."""
    if not plan.shots:
        raise ValueError("Cannot compose storyboard with zero shots.")
    if len(panel_images) != len(plan.shots):
        raise ValueError(f"Expected {len(plan.shots)} panel images, got {len(panel_images)}.")

    if aspect == "9:16" and panel_width > panel_height:
        panel_width, panel_height = 600, 800

    cols = max(1, min(columns, len(plan.shots)))
    rows = math.ceil(len(plan.shots) / cols)

    bg_canvas = (15, 18, 24)
    card_bg = (24, 29, 39)
    card_border = (48, 57, 74)
    accent_amber = (245, 158, 11)
    accent_cyan = (56, 189, 248)
    text_primary = (244, 246, 250)
    text_secondary = (176, 187, 204)
    text_muted = (128, 142, 164)

    margin = 36
    gutter = 28
    header_h = 124
    card_pad = 16
    caption_h = 166
    card_w = panel_width + card_pad * 2
    card_h = panel_height + card_pad * 2 + caption_h

    canvas_w = margin * 2 + cols * card_w + (cols - 1) * gutter
    canvas_h = margin * 2 + header_h + rows * card_h + (rows - 1) * gutter

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_canvas)
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(34, bold=True)
    font_subtitle = _load_font(19, bold=False)
    font_badge = _load_font(17, bold=True)
    font_meta = _load_font(16, bold=True)
    font_body = _load_font(17, bold=False)

    # Header bar
    draw.text((margin, margin), plan.title.upper(), font=font_title, fill=text_primary)
    header_pill_text = f"PHOTOG VISUAL GUIDE • {aspect}"
    hb = draw.textbbox((0, 0), header_pill_text, font=font_badge)
    header_pill_w = (hb[2] - hb[0]) + 24
    _draw_pill(
        draw,
        canvas_w - margin - header_pill_w,
        margin + 6,
        header_pill_text,
        font_badge,
        bg_color=(36, 48, 68),
        fg_color=accent_cyan,
    )
    hook_summary = f"0-3s Hook: {plan.hook_0_3s_concept}" if plan.hook_0_3s_concept else plan.curiosity_loop_strategy
    wrapped_hook = textwrap.shorten(hook_summary, width=145, placeholder="...")
    draw.text((margin, margin + 48), wrapped_hook, font=font_subtitle, fill=accent_amber)
    draw.line(
        [(margin, margin + header_h - 18), (canvas_w - margin, margin + header_h - 18)],
        fill=card_border,
        width=2,
    )

    for idx, (shot, img_path) in enumerate(zip(plan.shots, panel_images)):
        r = idx // cols
        c = idx % cols
        card_x = margin + c * (card_w + gutter)
        card_y = margin + header_h + r * (card_h + gutter)

        draw.rounded_rectangle(
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=16,
            fill=card_bg,
            outline=card_border,
            width=2,
        )

        img_x = card_x + card_pad
        img_y = card_y + card_pad
        fitted = _fit_panel_image(img_path, panel_width, panel_height)
        canvas.paste(fitted, (img_x, img_y))

        # Top-left overlay badge on the panel image
        badge_text = f"{shot.shot_label.upper()}  •  {shot.panel_position.upper()}"
        pill_right = _draw_pill(
            draw,
            img_x + 14,
            img_y + 14,
            badge_text,
            font_badge,
            bg_color=(15, 18, 24),
            fg_color=accent_amber,
        )
        _draw_pill(
            draw,
            pill_right + 10,
            img_y + 14,
            shot.curiosity_role.upper(),
            font_badge,
            bg_color=(29, 78, 216),
            fg_color=text_primary,
        )

        # Caption area below image
        cap_y = img_y + panel_height + 14
        meta_line = f"{shot.framing_and_lens}   |   Move: {shot.camera_movement}"
        draw.text(
            (img_x, cap_y),
            textwrap.shorten(meta_line, width=96, placeholder="..."),
            font=font_meta,
            fill=accent_cyan,
        )

        instr_lines = textwrap.wrap(shot.visual_instruction, width=92)[:3]
        line_y = cap_y + 30
        for line in instr_lines:
            draw.text((img_x, line_y), line, font=font_body, fill=text_primary)
            line_y += 24

        props_line = f"Props / Lighting: {shot.lighting_and_props}"
        draw.text(
            (img_x, card_y + card_h - 32),
            textwrap.shorten(props_line, width=102, placeholder="..."),
            font=font_body,
            fill=text_muted,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def make_placeholder_panel(shot: ShotPanel, output_path: Path, width: int = 1024, height: int = 576) -> Path:
    """Create a clean wireframe placeholder frame when running in --mock-comfy test mode."""
    img = Image.new("RGB", (width, height), (30, 38, 52))
    draw = ImageDraw.Draw(img)
    font_big = _load_font(28, bold=True)
    font_small = _load_font(18, bold=False)

    draw.rectangle((16, 16, width - 16, height - 16), outline=(96, 165, 250), width=3)
    draw.text((40, 44), f"[MOCK COMFYUI FRAME] {shot.shot_label} ({shot.panel_position})", font=font_big, fill=(245, 158, 11))
    y = 104
    for line in textwrap.wrap(shot.flux_prompt or shot.visual_instruction, width=74)[:10]:
        draw.text((40, y), line, font=font_small, fill=(226, 232, 240))
        y += 28

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG")
    return output_path
