"""FastAPI app exposing the two-phase voice-to-text pipeline."""

import os

from dotenv import load_dotenv

# Load .env before importing the service so its module-level config picks it up.
load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import speech_service  # noqa: E402


class SummarizeRequest(BaseModel):
    text: str


class AskRequest(BaseModel):
    text: str
    question: str
    history: list[dict] | None = None

app = FastAPI(title="Voice-to-Text Converter", version="1.0.0")

_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
allow_origins = ["*"] if _origins_env in ("", "*") else [
    o.strip() for o in _origins_env.split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_enabled": speech_service.gemini_available(),
        "nvidia_enabled": speech_service.nvidia_available(),
        "summary_provider": speech_service.summary_provider(),
    }


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        result = speech_service.transcribe(raw)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {exc}. "
            "If this mentions ffmpeg, install ffmpeg on the server.",
        ) from exc

    return result


@app.post("/api/summarize")
def summarize(req: SummarizeRequest):
    try:
        return speech_service.summarize_tasks(req.text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Summary failed: {exc}") from exc


@app.post("/api/ask")
def ask(req: AskRequest):
    try:
        return speech_service.ask_question(req.text, req.question, req.history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Q&A failed: {exc}") from exc


# Serve the built React frontend (production / Docker) if it's present, so the
# whole app runs from one server. Mounted last so /api/* routes take priority.
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")


# Allow `python main.py` for convenience.
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
