# START HERE — 5-Minute Setup Guide (Windows + n8n + WhatsApp)

No coding or manual config editing needed.

---

## Step 1: Open the folder on your Windows PC
If your PC syncs Google Drive `AI stuff` to `C:\Projects\visual-hook-broll`, open that folder.
Otherwise, download the ZIP from GitHub (`BhaadhGuru/visual-hook-broll` → green **Code** button → **Download ZIP**) and unzip it to `C:\Projects\visual-hook-broll`.

---

## Step 2: Double-click `SETUP.bat` (one time)
Make sure **Google Drive**, **Ollama**, and **ComfyUI** are running, then double-click **`SETUP.bat`**.

It automatically:
1. Sets up Python and installs `Pillow`.
2. Finds your `G:\My Drive\BrollInput`, `BrollOutput`, and `YapInput` folders.
3. Detects your installed **Ollama** model and **ComfyUI Flux** layout (all-in-one checkpoint, split UNET + CLIP + VAE, or GGUF) and writes `config.json`.
4. Scans for your **Yap `.bat`** and **MediaMTX `.bat`/`.exe`** (if not found automatically, you can drag-and-drop them into the window or press Enter to skip).
5. Backs up a copy of all `.bat` files into `G:\My Drive\AI stuff\visual-hook-broll\bat_backups\`.
6. Runs a quick test with the AMA TechTel script so you can see the storyboard appear in `BrollOutput`.
7. Installs the **always-on Windows Watchdog** so B-Roll, Yap, and MediaMTX start automatically when Windows boots.

*(You can also double-click `START_BROLL.bat` or `START_WATCHDOG.bat` anytime to watch them in a visible window.)*

---

## Step 3: Connect WhatsApp (100% Free — Local QR Bridge)
No n8n, Zapier, or paid cloud subscription needed:
1. Double-click **`START_WHATSAPP_BRIDGE.bat`** inside your `visual-hook-broll` folder.
2. The first time it opens, scan the **WhatsApp QR code** in the window using your phone (**WhatsApp → Settings → Linked Devices → Link a Device**).
3. Leave it running — you can now text yourself directly on WhatsApp!

*(Alternative: if you run self-hosted **n8n** locally, import `workflows/n8n_whatsapp_broll_workflow.json` from Google Drive `AI stuff/visual-hook-broll/workflows/`.)*

---

## Step 4: Test from WhatsApp!
Text yourself on WhatsApp:
- **`#status`** → replies with live status of B-Roll, Yap, MediaMTX, Ollama, and ComfyUI.
- **Paste any script** → replies with your shot list + 2×2 Flux storyboard image.

### WhatsApp Remote Control Commands
| Command | What it does |
|---|---|
| `#status` | Shows what's running (B-Roll, Yap, MediaMTX, Ollama, ComfyUI) |
| `#start all` | Starts B-Roll, Yap, and MediaMTX if any aren't running |
| `#start broll` / `#start yap` / `#start stream` | Starts one specific service |
| `#restart stream` | Force-restarts MediaMTX Livestream if it freezes |
| `#restart yap` / `#restart broll` / `#restart all` | Force-restarts one or all workers |
| `#stop stream` | Stops MediaMTX remotely (pauses auto-restart until `#start stream`) |
| `#update` | Pulls the latest code from GitHub, backs up `.bat` scripts to Drive, and restarts B-Roll |
| `#help` | Lists all commands |

> **Hands-free crash protection:** Even if you don't text anything, the Watchdog automatically restarts **MediaMTX** within ~5 seconds if it crashes, and auto-launches **B-Roll** or **Yap** whenever a script lands in `BrollInput` or `YapInput`.
