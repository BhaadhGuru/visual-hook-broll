# Visual Hook & Curiosity-Loop B-Roll Shot List Generator

A **100% local Windows GPU** pipeline that mirrors your `YapInput` / `YapOutput` Google Drive + WhatsApp automation — built specifically to turn any raw script texted over WhatsApp into:

1. A **Visual Hook (0–3s) & Curiosity-Loop B-Roll Shot List** formatted for WhatsApp (framing, focal length, camera movement, props/lighting, and script beat mapping).
2. **Photorealistic reference images** rendered locally on your GPU via **ComfyUI + Flux**, automatically stitched into a **labeled 2×2 (or 3×2) photographer visual instruction storyboard** (`Top-Left`, `Top-Right`, `Bottom-Left`, `Bottom-Right`).

---

## Architecture (Mirrors `YapInput` / `YapOutput`)

```text
WhatsApp (Send Script Text)
   │
   ▼
n8n OR Make.com
   └─ Drops <job_id>.json into Google Drive: BrollInput/
   │
   ▼  (Google Drive for Desktop G:\My Drive\BrollInput  OR  rclone sync)
Windows GPU Worker (src/worker.py)
   ├─ 1. Local Ollama (qwen2.5:14b / llama3.1 / qwen3)
   │      → Structured JSON shot list + visual consistency lock + Flux prompts
   │      → Explicitly unloads Ollama from GPU VRAM (keep_alive: 0)
   ├─ 2. Local ComfyUI API (Flux.1 Dev FP8 or Flux.1 Schnell)
   │      → Renders each shot panel locally on your GPU
   │      → Explicitly frees ComfyUI VRAM (/free) after batch
   ├─ 3. Pillow Compositor (src/storyboard.py)
   │      → Stitches panels into labeled 2×2 photog instruction sheet
   └─ 4. Atomic write to Google Drive: BrollOutput/
          • <job_id>_storyboard.png   (2×2 visual guide for photog)
          • <job_id>_whatsapp.txt     (formatted WhatsApp shot list)
          • <job_id>_result.json      (written LAST to trigger n8n / Make.com)
          • <job_id>/                 (full shoot kit: individual panels + shoot_card.md)
   │
   ▼
n8n OR Make.com (Watches BrollOutput/ for *_result.json)
   └─ Replies in WhatsApp with the Shot List Text + 2×2 Storyboard Image
```

---

## Quick Start on Windows

### 1. Create the two Google Drive folders
Create `BrollInput` and `BrollOutput` in Google Drive (right next to `YapInput` / `YapOutput`) and make sure Google Drive for Desktop (`G:\My Drive\...`) or your `rclone` sync mounts them locally.

### 2. Configure local paths & models
Copy `config.example.json` to `config.json` and edit:
- `input_dir` / `output_dir` (e.g., `G:\My Drive\BrollInput` and `G:\My Drive\BrollOutput`)
- `ollama.model` (e.g., `qwen2.5:14b`, `qwen3:14b`, or `llama3.1:8b`)
- `comfyui.checkpoint_name` (exact filename in your `ComfyUI\models\checkpoints\`)
- `comfyui.workflow_template` (`comfyui_workflows/flux_dev_fp8_api.json` or `comfyui_workflows/flux_schnell_api.json`)

> **Using your own custom ComfyUI Flux graph (GGUF, UNETLoader + DualCLIPLoader, LoRAs)?**
> In ComfyUI, enable **Dev Mode**, click **Save (API Format)**, drop the JSON into `comfyui_workflows/`, and replace the prompt text, seed, width, height, and filename prefix with `{{PROMPT}}`, `{{SEED}}`, `{{WIDTH}}`, `{{HEIGHT}}`, and `{{FILENAME_PREFIX}}`.

### 3. Verify Ollama + ComfyUI + Drive paths
```powershell
.\scripts\check_health.ps1
```

### 4. Start the worker (or install as a Windows logon task)
```powershell
# Run in foreground
.\scripts\start_worker.ps1

# Or register Windows Task Scheduler auto-start
.\scripts\install_scheduled_task.ps1
```

### 5. Connect WhatsApp via n8n or Make.com
- **n8n:** Import `workflows/n8n_whatsapp_broll_workflow.json` and fill in your WhatsApp Phone Number ID + `BrollInput` / `BrollOutput` Google Drive folder IDs.
- **Make.com:** Follow `workflows/MAKE_COM_SETUP.md` (2-scenario setup mirroring `YapInput`/`YapOutput`).

---

## WhatsApp Inline Commands

Include any hashtag anywhere in the script you text to WhatsApp:

| Tag | Effect |
| --- | --- |
| `#vertical` / `#9:16` | Renders vertical 9:16 Reels/Shorts frames (`768×1024`) |
| `#horizontal` / `#16:9` | Renders landscape 16:9 commercial frames (`1024×576`, default) |
| `#shots=6` | Generates a 6-panel (3×2) storyboard instead of 4-panel (2×2) |
| `#noimages` | Fast text-only mode — skips ComfyUI Flux and sends only the shot list |

---

## Single-GPU VRAM Hygiene (Ollama + Flux)

Running an Ollama LLM and ComfyUI Flux on the same Windows GPU will normally CUDA-OOM if Ollama keeps its weights loaded in VRAM. By default:
1. After Ollama returns the shot list JSON, `director.unload_ollama_model()` sends `keep_alive: 0` so Ollama immediately releases VRAM.
2. After ComfyUI finishes the 4 panels, `comfy_client.free_vram()` calls `POST /free` (`unload_models: true, free_memory: true`) so your GPU returns to idle cleanly for DaVinci Resolve or the next job.

---

## CLI Usage & Automated Tests

```bash
# Run a single script directly from CLI (add --mock-comfy to test without ComfyUI)
python -m src.pipeline examples/ama_techtel_card_reader.txt --mock-comfy

# Run the full unit test suite
python -m unittest discover -s tests -v
```
