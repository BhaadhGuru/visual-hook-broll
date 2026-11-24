"""Formatters for WhatsApp shot lists, image captions, and printable Markdown shoot cards."""

from __future__ import annotations

from .director import ShotListPlan


def format_whatsapp_shot_list(plan: ShotListPlan, aspect: str = "16:9") -> str:
    """Format a scannable, mobile-friendly WhatsApp message for the photographer."""
    lines: list[str] = [
        f"🎬 *{plan.title}*",
        f"📐 Aspect: {aspect} | Panels: {len(plan.shots)}",
        "",
        f"⚡ *0–3s Visual Hook:*",
        plan.hook_0_3s_concept,
        "",
        f"🔁 *Curiosity Loop Arc:*",
        plan.curiosity_loop_strategy,
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📸 *PHOTOG VISUAL SHOT LIST*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for shot in plan.shots:
        lines.extend(
            [
                "",
                f"*{shot.shot_label} ({shot.panel_position})* — _{shot.curiosity_role}_",
                f"• *Shoot:* {shot.visual_instruction}",
                f"• *Framing / Lens:* {shot.framing_and_lens}",
                f"• *Camera Move:* {shot.camera_movement}",
                f"• *Props / Light:* {shot.lighting_and_props}",
            ]
        )
        if shot.script_beat:
            lines.append(f"• *Covers Line:* \"{shot.script_beat}\"")

    if plan.gear_and_location_notes:
        lines.extend(
            [
                "",
                "🧰 *On-Set Notes & Consistency Lock:*",
                plan.gear_and_location_notes,
            ]
        )

    return "\n".join(lines).strip()


def format_whatsapp_image_caption(plan: ShotListPlan) -> str:
    """Short caption sent directly on the 2x2 storyboard image in WhatsApp."""
    panel_summary = " | ".join(f"{s.shot_label} ({s.panel_position})" for s in plan.shots)
    return f"🎬 {plan.title} — Photog Visual Storyboard\n{panel_summary}"


def format_markdown_shoot_card(plan: ShotListPlan, aspect: str = "16:9") -> str:
    """Render a full Markdown shoot sheet suitable for archiving or printing."""
    lines: list[str] = [
        f"# {plan.title}",
        "",
        f"- **Aspect Ratio:** `{aspect}`",
        f"- **0–3s Visual Hook:** {plan.hook_0_3s_concept}",
        f"- **Curiosity Loop Strategy:** {plan.curiosity_loop_strategy}",
        f"- **Visual Consistency Lock:** {plan.consistency_lock}",
        f"- **Gear & Location Notes:** {plan.gear_and_location_notes}",
        "",
        "## Shot List",
        "",
    ]
    for shot in plan.shots:
        lines.extend(
            [
                f"### {shot.shot_label} ({shot.panel_position}) — {shot.curiosity_role}",
                f"- **Script Beat:** {shot.script_beat}",
                f"- **Visual Instruction:** {shot.visual_instruction}",
                f"- **Framing & Lens:** {shot.framing_and_lens}",
                f"- **Camera Movement:** {shot.camera_movement}",
                f"- **Lighting & Props:** {shot.lighting_and_props}",
                f"- **Flux Prompt:** `{shot.flux_prompt}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
