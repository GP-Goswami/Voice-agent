---
title: VoiceScribe
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 🎙️ Voice → Text Converter

Upload (or record) an English voice clip and convert it to text. React frontend,
FastAPI backend, with a **two-phase** pipeline that stays free by default and
only calls an AI model for hard-to-understand audio.

> The block above is config for Hugging Face Spaces (ignored by GitHub except as
> a small table). It tells the Space to build the `Dockerfile` and serve on port
> 7860.

See [CLAUDE.md](CLAUDE.md) for the full design.

## How it works

1. **Phase 1 — free Speech Recognition.** Google Web Speech (no API key) converts
   the audio and gives a confidence score. Clear speech → labeled **easy**, kept.
2. **Phase 2 — AI fallback (Gemini).** If Phase 1 is unsure (low confidence /
   failed), the same audio is sent to Gemini (free tier). This only runs if you
   set `GEMINI_API_KEY`.

The UI shows the transcript plus the detected **level**, which **engine**
produced it, and the **confidence**.

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **ffmpeg** (required by the backend to decode webm/mp3/m4a/ogg audio)
  - Windows: `winget install Gyan.FFmpeg` (or download from ffmpeg.org and add to PATH)
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

## Run locally

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # then edit .env (optional: add GEMINI_API_KEY)
uvicorn main:app --reload
```

Backend runs at http://localhost:8000 (health: http://localhost:8000/api/health).

> Phase 2 is optional. Without `GEMINI_API_KEY` the app still works — hard audio
> just returns the best free guess. Get a free key at
> https://aistudio.google.com/apikey.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. If your backend isn't on localhost:8000, create
`frontend/.env` with `VITE_API_BASE=<your backend url>`.

## Deploy free — all-in-one on Hugging Face Spaces

The included `Dockerfile` builds the React frontend and runs FastAPI, which
serves both the API and the frontend from **one** URL. ffmpeg is installed in
the image, so audio decoding works.

1. Create a free account at https://huggingface.co.
2. **New → Space.** Owner = you, name = `voicescribe`, **SDK = Docker**,
   template = Blank, visibility = Public or Private. Create.
3. Push this project to the Space's git repo (it gives you the URL):
   ```bash
   git init
   git add .
   git commit -m "VoiceScribe"
   git remote add space https://huggingface.co/spaces/<your-username>/voicescribe
   git push space main        # use the HF access token as the password
   ```
   (Create a token at https://huggingface.co/settings/tokens → "Write".)
4. In the Space → **Settings → Variables and secrets**, add **secrets**:
   - `NVIDIA_API_KEY` = your NVIDIA key (for summary + Q&A)
   - `GEMINI_API_KEY` = your Gemini key (for Phase 2 audio fallback)
   - optional: `SUMMARY_PROVIDER` = `nvidia`
5. The Space builds the Dockerfile and goes live at
   `https://<your-username>-voicescribe.hf.space`. Open it and use the app.

> Rebuilds happen automatically on every `git push space main`.

### Keep the code on GitHub too (optional)

```bash
git remote add origin https://github.com/<you>/voicescribe.git
git push origin main
```

You can then push to both: `git push origin main` (code) and
`git push space main` (deploy). GitHub stores the code; the Space runs it.

## Project structure

```
Dockerfile   builds frontend + runs backend (for Hugging Face Spaces)
backend/     FastAPI app + two-phase pipeline; serves ./static in production
frontend/    React (Vite) upload/record UI
CLAUDE.md    design doc
```
