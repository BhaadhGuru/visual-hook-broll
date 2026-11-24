# Visual Hook B-Roll — Windows Start Here

Turn WhatsApp scripts into **Visual Hook B-Roll Storyboards** (Ollama + ComfyUI Flux) and **Broadcast News VO/SOT Packages** (Sony FX9 Veo 3.1 prompts + DaVinci Resolve markers) — 100% locally on your Windows GPU.

---

## 2-Double-Click Setup

1. **Make sure your local apps are open first:**
   - **Google Drive for Desktop** (signed in, mounted at `G:\My Drive\`)
   - **Ollama** (running locally with a text model such as `qwen2.5:14b` or `qwen2.5-coder:7b`)
   - **ComfyUI** running with Flux on `http://127.0.0.1:8188` (if using ComfyUI CLI, launch with `--listen 127.0.0.1 --port 8188`)
2. **Double-click `SETUP.bat`**
   - Creates `.venv` and installs Pillow (the only third-party Python package needed).
   - Auto-detects `G:\My Drive\BrollInput`, `BrollOutput`, and `YapOutput`.
   - Probes Ollama (`/api/tags`) and picks your best installed text/JSON model (skipping vision-only models like `moondream` or embedding models).
   - Probes ComfyUI (`/object_info`) and automatically configures whichever Flux layout you have installed:
     - **Single-file checkpoint** (`flux1-dev-fp8.safetensors` / `flux1-schnell`)
     - **Split-file Flux** (`UNETLoader` + `DualCLIPLoader` + `VAELoader`)
     - **GGUF Flux** (`UnetLoaderGGUF` + `DualCLIPLoader` + `VAELoader`)
   - Writes `config.json`, runs offline unit tests, checks live health, and asks if you want Windows logon auto-start.
3. **Double-click `START_BROLL.bat`** (Drive folder watcher) **or `START_WHATSAPP_BRIDGE.bat`** (Local Baileys WhatsApp QR Bridge — no n8n/Make or Drive sync needed):
   - **`START_BROLL.bat`**: starts `src/worker.py` watching `BrollInput\` for incoming `.json` or `.txt` scripts.
   - **`START_WHATSAPP_BRIDGE.bat`**: starts your local Baileys WhatsApp QR Bridge (`C:\Projects\whatsapp-reel-workflow\bridge.mjs`) and replies directly in WhatsApp when you send:
     - `broll: <your script>` or `#broll <your script>` (also `#vertical`, `#shots=6`, `#noimages`)
     - `#news <VO/SOT script>` (News B-Roll & SOT Engine)

---

## Tip for 8 GB VRAM GPUs (e.g. Quadro RTX 4000 / RTX 3070 / 4060)

- **Ollama + Flux never fight for VRAM:** By default (`unload_after_generation: true` and `free_vram_after_job: true`), the pipeline explicitly evicts Ollama (`keep_alive: 0`) before calling ComfyUI, and calls ComfyUI `POST /free` once the panels finish.
- **Recommended models for 8 GB VRAM:**
  - **Ollama:** a 7B model (`qwen2.5:7b` or `qwen2.5-coder:7b`) — fast and reliable for structured JSON shot lists.
  - **ComfyUI Flux:** a **GGUF `Q4_K_S` or `Q4_K_M`** Flux model with `t5xxl_fp8_e4m3fn.safetensors`, `clip_l.safetensors`, and `ae.safetensors`, or `flux1-dev-fp8` / `flux1-schnell`.

---

## WhatsApp Inline Commands

Include any hashtag anywhere in your WhatsApp message:

| Tag | Effect |
| --- | --- |
| *(none)* | 4-panel (2×2) 16:9 storyboard + WhatsApp shot list |
| `#vertical` / `#9:16` | Switch frames to vertical Reels/Shorts (`768×1024`) |
| `#horizontal` / `#16:9` | Landscape `1024×576` commercial frames (default) |
| `#shots=6` | 6-panel (3×2) storyboard instead of 4-panel (2×2) |
| `#noimages` | Fast text-only shot list (skips ComfyUI Flux) |
| `#news` | Routes script to the News B-Roll & SOT Engine (Sony FX9 prompts + DaVinci Resolve markers) |

---

## Troubleshooting

- **Ollama says offline or wrong model selected:**
  Open Ollama, or run `ollama pull qwen2.5:7b` in a terminal, then re-run `SETUP.bat`.
- **ComfyUI was closed when you ran `SETUP.bat`:**
  Start ComfyUI (`127.0.0.1:8188`), load/confirm your Flux models are in `ComfyUI\models\...`, and double-click `SETUP.bat` again to update `config.json`.
- **BrollInput / BrollOutput don't show up under `G:\My Drive\` yet:**
  Right-click the Google Drive tray icon and make sure Drive is synced/streaming (`G:\My Drive`), or re-run `SETUP.bat`.
- **Test everything manually without ComfyUI:**
  ```powershell
  .\.venv\Scripts\python.exe -m src.pipeline examples\ama_techtel_card_reader.txt --mock-comfy
  ```
