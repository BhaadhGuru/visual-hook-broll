# Make.com Setup Guide (Mirrors `YapInput` / `YapOutput`)

If you use **Make.com** instead of n8n, create two simple scenarios connected to two Google Drive folders:
- `BrollInput`
- `BrollOutput`

---

## Scenario 1 — WhatsApp Script -> `BrollInput`

1. **Trigger:** `WhatsApp Business Cloud → Watch Events` (event: incoming text message).
2. **Module 2:** `Google Drive → Upload a File`
   - **Folder:** `BrollInput`
   - **File Name:** `wa_{{1.messages[].timestamp}}.json`
   - **Data (Raw JSON):**
     ```json
     {
       "job_id": "wa_{{1.messages[].timestamp}}",
       "sender": "{{1.messages[].from}}",
       "chat_id": "{{1.messages[].from}}",
       "message_id": "{{1.messages[].id}}",
       "script": "{{replace(1.messages[].text.body; newline; \"\\n\")}}"
     }
     ```
     *(Tip: Use Make's `JSON → Create JSON` module so quotes/newlines in the script are automatically escaped cleanly!)*
3. **Module 3 (Optional Ack):** `WhatsApp Business Cloud → Send a Message`
   - **To:** `{{1.messages[].from}}`
   - **Body:** `🎬 Script received! Generating your Visual Hook shot list + Flux storyboard locally...`

---

## Scenario 2 — `BrollOutput` -> WhatsApp Shot List + Storyboard Image

1. **Trigger:** `Google Drive → Watch Files in a Folder`
   - **Folder:** `BrollOutput`
2. **Filter** (between Module 1 and Module 2):
   - `File Name` **Ends with** `_result.json`
3. **Module 2:** `Google Drive → Download a File` (downloads the `*_result.json`)
4. **Module 3:** `JSON → Parse JSON` (input: `{{toString(2.data)}}`)
5. **Module 4 (Send Shot List Text):** `WhatsApp Business Cloud → Send a Message`
   - **To:** `{{3.sender}}`
   - **Message Body:** `{{3.whatsapp_message}}`
6. **Filter (only if storyboard image exists):** `{{3.storyboard_filename}}` **Exists**
7. **Module 5:** `Google Drive → Search for Files/Folders`
   - **Folder:** `BrollOutput`
   - **Query:** `Name equals {{3.storyboard_filename}}`
8. **Module 6:** `Google Drive → Download a File` (using File ID from Module 5)
9. **Module 7 (Upload Media to WhatsApp):** `WhatsApp Business Cloud → Upload a Media` (file from Module 6, mime `image/png`)
10. **Module 8 (Send Storyboard Image):** `WhatsApp Business Cloud → Send a Media Message`
    - **To:** `{{3.sender}}`
    - **Media Type:** `Image`
    - **Media ID:** `{{7.id}}`
    - **Caption:** `{{3.whatsapp_image_caption}}`

---

## Inline Hashtag Commands You Can Text in WhatsApp

Add any of these anywhere in your WhatsApp script message:
- `#vertical` or `#9:16` — switches Flux output & storyboard layout to vertical Reels/Shorts (`768x1024`)
- `#horizontal` or `#16:9` — landscape commercial B-roll (`1024x576`, default)
- `#shots=6` — creates a 6-panel (3x2) storyboard instead of 4-panel (2x2)
- `#noimages` — skips ComfyUI Flux and replies immediately with the WhatsApp text shot list only
