# Voice-to-Text Converter

A simple personal app to upload (or record) English voice and convert it to text.
React frontend + FastAPI backend. Built to run locally and to be deployable on
free platforms.

## Goal

1. Upload a voice clip (English) OR record from the mic in the browser.
2. Backend converts the voice to text.
3. The transcript is shown below the upload box, along with a detected
   **difficulty level** of the speech (easy / hard).

## The 2-phase backend pipeline

The core idea: try the **free, offline-friendly** path first, and only fall back
to an AI model when the audio is genuinely hard to understand. This keeps it free
by default and future-proof.

- **Phase 1 — Whisper (free, offline, default)**
  - Uses `faster-whisper` (OpenAI Whisper, optimized) — fully offline, no API
    key. It transcribes the **entire** clip reliably (built-in VAD + 30s
    windowing), so long audio is never cut off. It also decodes webm/mp3/m4a
    itself (via PyAV), so it doesn't depend on system ffmpeg.
  - Returns the transcript plus a **confidence** (from segment avg log-probs).
  - We classify the result:
    - `confidence >= THRESHOLD` (default 0.45) -> level = **easy**, keep it.
    - empty / very low confidence -> level = **hard**, optionally try Phase 2.
  - Model is configurable via `WHISPER_MODEL` (tiny.en/base.en/small.en/...).

- **Phase 2 — AI model fallback (Gemini, free tier)**
  - Safety net that runs only when Phase 1 returns empty / near-empty text.
  - Uses Google `gemini-2.5-flash-lite` (free tier) via `google-generativeai`.
  - Requires `GEMINI_API_KEY`. If missing, Phase 2 is skipped and we return the
    Whisper result (which is already the full clip).

So every response tells the user: the text, the detected level, and which engine
produced the final text (`whisper` or `gemini`).

## Why these choices (free / future)

- `faster-whisper` = free, offline, accurate, handles full-length audio with no
  API key and no length cutoff. This is the main engine.
- Gemini free tier = optional safety net for the rare empty result.
- Summary/Q&A use a text LLM (NVIDIA Llama by default, Gemini optional).
- Everything is open source / free-tier. Swappable without changing the API.

## Project layout

```
Project/
  CLAUDE.md            <- this file
  README.md            <- setup & run & deploy instructions
  backend/
    main.py            <- FastAPI app, /api/transcribe endpoint, CORS
    speech_service.py  <- the 2-phase pipeline (audio decode -> phase 1 -> phase 2)
    requirements.txt
    .env.example       <- GEMINI_API_KEY, CONFIDENCE_THRESHOLD, etc.
  frontend/
    index.html
    vite.config.js
    package.json
    src/
      main.jsx
      App.jsx          <- upload + record UI, shows transcript + level
      App.css
```

## API contract

`POST /api/transcribe`  (multipart/form-data, field name: `file`)

Response JSON:
```json
{
  "text": "the recognized transcript",
  "level": "easy | hard",
  "engine": "whisper | gemini",
  "confidence": 0.0,
  "phase": 1,
  "note": "optional human-readable note"
}
```

`GET /api/health` -> `{ "status": "ok" }`

## Audio handling

- Browsers record `audio/webm`; uploads may be mp3/m4a/wav/ogg.
- Phase 1 (Whisper) decodes the input bytes itself via PyAV — **no system
  ffmpeg required** for transcription.
- Phase 2 (Gemini) reuses `pydub` to make a 16 kHz mono WAV; that path needs
  ffmpeg, but it only runs in the rare empty-result fallback.

## Config (backend/.env)

- `WHISPER_MODEL`         - default `base.en` (tiny.en/base.en/small.en/...).
- `WHISPER_DEVICE`        - default `cpu`.
- `WHISPER_COMPUTE`       - default `int8`.
- `GEMINI_API_KEY`        - optional; enables the Phase 2 safety net.
- `GEMINI_MODEL`          - default `gemini-2.5-flash-lite`.
- `CONFIDENCE_THRESHOLD`  - default `0.45`; below this -> level "hard".
- `MIN_WORDS`             - default `1`; fewer recognized words -> treat as hard.
- `NVIDIA_API_KEY`        - text LLM for summary + Q&A (default provider).
- `SUMMARY_PROVIDER`      - `nvidia` or `gemini` (default `nvidia`).
- `ALLOWED_ORIGINS`       - CORS origins, comma separated. Default allows all.

## Run locally

Backend:  `cd backend && pip install -r requirements.txt && uvicorn main:app --reload`
Frontend: `cd frontend && npm install && npm run dev`
Frontend talks to backend at `VITE_API_BASE` (default `http://localhost:8000`).

## Deploy (free)

- Backend: Render / Railway / Hugging Face Spaces (all have free tiers and let
  you install ffmpeg). Set `GEMINI_API_KEY` as a secret.
- Frontend: Vercel / Netlify / GitHub Pages (static build via `npm run build`).
  Set `VITE_API_BASE` to the deployed backend URL.

## Conventions

- Keep it simple — this is a single-user personal tool.
- Don't break the API contract above; the frontend depends on those exact fields.
- New STT engines should plug into `speech_service.py` and still return that shape.
