# 🎙️ Voice → Text Converter

Upload (or record) an English voice clip and convert it to text. React frontend,
FastAPI backend, with a **two-phase** pipeline that stays free by default and
only calls an AI model for hard-to-understand audio.

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

## Deploy (free)

**Backend** → Render / Railway / Hugging Face Spaces (free tiers; can install
ffmpeg).
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- On Render, add a `render.yaml` or an "apt" build step for ffmpeg, or use a
  Docker image with ffmpeg preinstalled.
- Set env vars: `GEMINI_API_KEY`, and `ALLOWED_ORIGINS` = your frontend URL.

**Frontend** → Vercel / Netlify / GitHub Pages.
- Build: `npm run build` → static files in `frontend/dist`.
- Set `VITE_API_BASE` to your deployed backend URL at build time.

## Project structure

```
backend/   FastAPI app + two-phase pipeline
frontend/  React (Vite) upload/record UI
CLAUDE.md  design doc
```
