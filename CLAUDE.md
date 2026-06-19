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

- **Phase 1 — Speech Recognition (free, default)**
  - Uses the `SpeechRecognition` Python library with Google's free Web Speech
    endpoint (no API key required).
  - Returns the best transcript plus a **confidence** score.
  - We classify the result:
    - `confidence >= THRESHOLD` (default 0.75) -> level = **easy**, we keep the result.
    - `confidence < THRESHOLD` or recognition fails -> level = **hard**, go to Phase 2.

- **Phase 2 — AI model fallback (Gemini, free tier)**
  - Only runs when Phase 1 says the speech is "hard" (low confidence / failed /
    too few words).
  - Uses Google `gemini-2.0-flash` (free tier) via `google-generativeai` to
    transcribe the same audio.
  - Requires `GEMINI_API_KEY` in the backend env. If the key is missing, Phase 2
    is skipped and we return the best Phase-1 result with a note.

So every response tells the user: the text, the detected level, and which engine
produced the final text (`speech_recognition` or `gemini`).

## Why these choices (free / future)

- `SpeechRecognition` + Google Web Speech = free, no key, good for clear English.
- Gemini free tier = strong fallback for hard/accented/noisy audio.
- Everything else is open source. No paid services are required to run the app.
- Swappable: Phase 1 can later be replaced with Vosk or faster-whisper (fully
  offline) without changing the API contract; Phase 2 can be any LLM.

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
  "engine": "speech_recognition | gemini",
  "confidence": 0.0,
  "phase": 1,
  "note": "optional human-readable note"
}
```

`GET /api/health` -> `{ "status": "ok" }`

## Audio handling

- Browsers record `audio/webm`; uploads may be mp3/m4a/wav/ogg.
- The backend uses `pydub` to decode any input to 16 kHz mono WAV before
  Phase 1. **`pydub` needs `ffmpeg` installed** on the machine/host.
- Gemini (Phase 2) accepts the original bytes directly (it handles many formats),
  so it does not strictly need ffmpeg, but we reuse the decoded WAV for consistency.

## Config (backend/.env)

- `GEMINI_API_KEY`        - optional; enables Phase 2.
- `GEMINI_MODEL`          - default `gemini-2.0-flash`.
- `CONFIDENCE_THRESHOLD`  - default `0.75`; below this -> level "hard" -> Phase 2.
- `MIN_WORDS`             - default `1`; fewer recognized words -> treat as hard.
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
