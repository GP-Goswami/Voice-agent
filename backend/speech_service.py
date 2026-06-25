"""Two-phase voice-to-text pipeline.

Phase 1: free, offline Speech-to-Text using faster-whisper (OpenAI Whisper,
         optimized). Transcribes the FULL audio reliably (its own VAD + 30s
         windowing) — no API key, no length cutoff. Produces a transcript +
         confidence and a difficulty "level".
Phase 2: AI fallback (Gemini) — only used when Phase 1 returns nothing / very
         low confidence.

Both phases return the same dict shape so the API contract stays stable:

    {
      "text": str,
      "level": "easy" | "hard",
      "engine": "whisper" | "gemini",
      "confidence": float,
      "phase": 1 | 2,
      "note": str,
    }
"""

import glob
import io
import math
import os
import shutil

from pydub import AudioSegment


# --- locate ffmpeg ----------------------------------------------------------
# pydub needs ffmpeg/ffprobe to decode webm/mp3/m4a/ogg. On Windows the winget
# install often isn't on PATH for the running process, so we find the binary
# ourselves and point pydub at it. Order: explicit env var -> PATH -> known
# install locations (winget, choco, scoop, Program Files).

def _find_ffmpeg() -> str | None:
    explicit = os.getenv("FFMPEG_PATH", "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit

    found = shutil.which("ffmpeg")
    if found:
        return found

    local = os.getenv("LOCALAPPDATA", "")
    candidates = []
    if local:
        candidates += glob.glob(
            os.path.join(local, "Microsoft", "WinGet", "Packages",
                         "Gyan.FFmpeg*", "**", "bin", "ffmpeg.exe"),
            recursive=True,
        )
    candidates += glob.glob(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe")
    if local:
        candidates += glob.glob(os.path.join(local, "Microsoft", "WinGet",
                                             "Links", "ffmpeg.exe"))
    candidates += glob.glob(r"C:\ffmpeg\bin\ffmpeg.exe")

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _configure_ffmpeg() -> None:
    ffmpeg_path = _find_ffmpeg()
    if not ffmpeg_path:
        return
    bin_dir = os.path.dirname(ffmpeg_path)
    ffprobe_path = os.path.join(bin_dir, "ffprobe.exe")

    AudioSegment.converter = ffmpeg_path
    AudioSegment.ffmpeg = ffmpeg_path
    if os.path.isfile(ffprobe_path):
        AudioSegment.ffprobe = ffprobe_path
    # Also expose to PATH so child calls resolve it.
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


_configure_ffmpeg()

# --- config (read from environment / .env) ---------------------------------

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.45"))
MIN_WORDS = int(os.getenv("MIN_WORDS", "1"))

# Whisper (offline) — Phase 1 engine. Larger models are more accurate but slower.
# tiny.en | base.en | small.en | medium.en | large-v3
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en").strip()
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip()
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8").strip()

# Gemini — used for Phase 2 audio transcription (audio-capable model).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

# NVIDIA (OpenAI-compatible) — used for the text task-summary feature.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct").strip()
NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
).strip()

# Which provider summarizes transcripts into tasks: "nvidia" or "gemini".
# Defaults to NVIDIA when its key is present, else Gemini.
SUMMARY_PROVIDER = os.getenv(
    "SUMMARY_PROVIDER", "nvidia" if NVIDIA_API_KEY else "gemini"
).strip().lower()


# --- audio decoding (only needed for the optional Gemini fallback) ----------

def decode_to_segment(raw: bytes) -> AudioSegment:
    """Decode any uploaded/recorded audio into a 16 kHz mono AudioSegment."""
    audio = AudioSegment.from_file(io.BytesIO(raw))
    return audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)


def segment_to_wav_bytes(audio: AudioSegment) -> bytes:
    out = io.BytesIO()
    audio.export(out, format="wav")
    return out.getvalue()


def decode_to_wav(raw: bytes) -> bytes:
    """Decode any audio to 16 kHz mono WAV bytes (for Gemini)."""
    return segment_to_wav_bytes(decode_to_segment(raw))


# --- Phase 1: free offline Speech-to-Text (faster-whisper) ------------------

_whisper_model = None


def _get_whisper():
    """Lazy-load the Whisper model once (downloaded on first use, then cached)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
        )
    return _whisper_model


def phase1_whisper(raw: bytes) -> dict:
    """Transcribe the full audio offline with Whisper.

    faster-whisper decodes the bytes itself (via PyAV) and processes the entire
    clip with voice-activity detection, so long audio is never cut off.
    """
    model = _get_whisper()

    # vad_filter trims silence so long recordings stay accurate and don't repeat.
    segments, _info = model.transcribe(
        io.BytesIO(raw),
        language="en",
        beam_size=5,
        vad_filter=True,
    )

    parts: list[str] = []
    logprobs: list[float] = []
    raw_segments: list[dict] = []
    for seg in segments:  # generator — iterating runs the transcription
        chunk = seg.text.strip()
        if chunk:
            parts.append(chunk)
            logprobs.append(seg.avg_logprob)
            raw_segments.append({"start": seg.start, "end": seg.end, "text": chunk})

    text = " ".join(parts).strip()
    # avg_logprob (<=0) -> probability via exp(); average across segments.
    confidence = (
        sum(math.exp(lp) for lp in logprobs) / len(logprobs) if logprobs else 0.0
    )
    word_count = len(text.split())

    if not text or word_count < MIN_WORDS:
        level = "hard"
    elif confidence >= CONFIDENCE_THRESHOLD:
        level = "easy"
    else:
        level = "hard"

    note = (
        f"Transcribed offline by Whisper ({WHISPER_MODEL})."
        if level == "easy"
        else f"Low-confidence audio (Whisper {WHISPER_MODEL})."
    )

    return {
        "text": text,
        "level": level,
        "engine": "whisper",
        "confidence": round(confidence, 3),
        "phase": 1,
        "note": note,
        # Timestamped lines (grouped to ~15s) for play-along highlighting.
        "segments": _group_segments(raw_segments),
    }


def _group_segments(segs: list[dict], max_sec: float = 15.0) -> list[dict]:
    """Merge Whisper segments into lines of up to ~max_sec, keeping timestamps."""
    lines: list[dict] = []
    cur: dict | None = None
    for s in segs:
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
        elif s["end"] - cur["start"] <= max_sec:
            cur["text"] += " " + s["text"]
            cur["end"] = s["end"]
        else:
            lines.append(cur)
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
    if cur is not None:
        lines.append(cur)
    # Round times so the JSON is small and clean.
    for ln in lines:
        ln["start"] = round(ln["start"], 2)
        ln["end"] = round(ln["end"], 2)
    return lines


# --- Phase 2: AI fallback (Gemini) -----------------------------------------

def gemini_available() -> bool:
    return bool(GEMINI_API_KEY)


def _gemini_model():
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(GEMINI_MODEL)


def phase2_gemini(wav_bytes: bytes) -> dict:
    """Transcribe with Gemini. Only call when Phase 1 graded the audio "hard"."""
    if not gemini_available():
        return {
            "text": "",
            "level": "hard",
            "engine": "gemini",
            "confidence": 0.0,
            "phase": 2,
            "note": "Phase 2 skipped: GEMINI_API_KEY is not set.",
            "segments": [],
        }

    model = _gemini_model()
    prompt = (
        "Transcribe this English audio to plain text in full, from start to "
        "finish. Return ONLY the transcript with normal punctuation, no extra "
        "commentary."
    )
    response = model.generate_content(
        [prompt, {"mime_type": "audio/wav", "data": wav_bytes}]
    )
    text = (response.text or "").strip()

    return {
        "text": text,
        "level": "hard",
        "engine": "gemini",
        # Gemini doesn't expose a numeric confidence; treat a non-empty result as
        # high quality since it's our dedicated "hard audio" engine.
        "confidence": 0.9 if text else 0.0,
        "phase": 2,
        "note": "Transcribed by AI model (Gemini) for full, accurate coverage.",
        "segments": [],
    }


# --- orchestration ----------------------------------------------------------

def transcribe(raw: bytes) -> dict:
    """Full pipeline: Phase 1 (offline Whisper) -> (rarely) Phase 2 (Gemini).

    Whisper handles full-length audio accurately, so it is the primary engine.
    Gemini only runs as a safety net when Whisper returns empty/near-empty text.
    """
    result = phase1_whisper(raw)

    # Whisper got a usable transcript -> done. (It transcribes the full clip.)
    if result["text"] and result["level"] == "easy":
        return result

    # Very low confidence / empty: try the AI fallback if configured.
    if gemini_available():
        try:
            ai = phase2_gemini(decode_to_wav(raw))
        except Exception as exc:  # noqa: BLE001 - decode/AI issue, keep Whisper
            if result["text"]:
                return result
            raise
        if ai["text"]:
            return ai
        # AI produced nothing — fall back to whatever Whisper had.
        if result["text"]:
            result["note"] = "Phase 2 returned nothing; showing Whisper result."
        return result

    # No AI key: return Whisper's result regardless (it's still the full clip).
    return result


# --- task summary (Fathom-style action items) ------------------------------

def nvidia_available() -> bool:
    return bool(NVIDIA_API_KEY)


def summary_provider() -> str:
    """The text provider that will actually run, given configured keys."""
    if SUMMARY_PROVIDER == "nvidia" and nvidia_available():
        return "nvidia"
    if SUMMARY_PROVIDER == "gemini" and gemini_available():
        return "gemini"
    # Fall back to whatever key we do have.
    if nvidia_available():
        return "nvidia"
    if gemini_available():
        return "gemini"
    return "none"


SUMMARY_PROMPT = (
    "You are a meeting-notes assistant like Fathom. Read the transcript below "
    "and produce three things:\n"
    "1. summary  - a clear summary of the conversation (2-4 sentences).\n"
    "2. tasks    - the action items / things to do.\n"
    "3. doubts   - any open questions, doubts, unclear points, or things that "
    "need follow-up.\n"
    "Return ONLY valid JSON of the form:\n"
    '{"summary": "<short paragraph>", '
    '"tasks": ["<task 1>", "<task 2>"], '
    '"doubts": ["<doubt 1>", "<doubt 2>"]}\n'
    "Each task and doubt must be a short, clear bullet. Use an empty array when "
    "there are none. Do not wrap the JSON in code fences.\n\n"
    "TRANSCRIPT:\n"
)


def _nvidia_chat(prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY)
    completion = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        top_p=0.7,
        max_tokens=1024,
    )
    return (completion.choices[0].message.content or "").strip()


def _gemini_text(prompt: str) -> str:
    response = _gemini_model().generate_content(prompt)
    return (response.text or "").strip()


def _parse_summary(raw: str) -> dict:
    """Parse the model's JSON; fall back to bullet lines if it isn't clean."""
    import json
    import re

    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
        summary = str(data.get("summary", "")).strip()
        tasks = [str(t).strip() for t in data.get("tasks", []) if str(t).strip()]
        doubts = [str(d).strip() for d in data.get("doubts", []) if str(d).strip()]
        return {"summary": summary, "tasks": tasks, "doubts": doubts, "note": ""}
    except (json.JSONDecodeError, AttributeError):
        # Couldn't parse JSON — treat any bullet-like lines as tasks.
        tasks = [
            line.lstrip("-*• ").strip()
            for line in raw.splitlines()
            if line.strip().startswith(("-", "*", "•"))
        ]
        return {"summary": "", "tasks": tasks, "doubts": [], "note": ""}


def summarize_tasks(text: str) -> dict:
    """Pull a short summary + action-item tasks from a transcript.

    Uses the configured text provider (NVIDIA Llama by default, or Gemini).
    Returns: {"summary": str, "tasks": [str, ...], "provider": str, "note": str}.
    """
    text = (text or "").strip()
    if not text:
        return {"summary": "", "tasks": [], "doubts": [], "provider": "none",
                "note": "No text to summarize."}

    provider = summary_provider()
    prompt = SUMMARY_PROMPT + text

    if provider == "nvidia":
        raw = _nvidia_chat(prompt)
    elif provider == "gemini":
        raw = _gemini_text(prompt)
    else:
        return {
            "summary": "", "tasks": [], "doubts": [], "provider": "none",
            "note": "Summary needs NVIDIA_API_KEY or GEMINI_API_KEY in backend/.env.",
        }

    result = _parse_summary(raw)
    result["provider"] = provider
    return result


# --- Q&A over the transcript -----------------------------------------------

QA_SYSTEM = (
    "You are a helpful assistant answering questions about a voice transcript. "
    "Use the transcript below as your main context. If the answer isn't in the "
    "transcript, say you couldn't find it there, then you may add a short, "
    "clearly-labeled general note. Keep answers concise and clear."
)


def ask_question(transcript: str, question: str, history=None) -> dict:
    """Answer a user's question/doubt about the transcript.

    Uses the same text provider as summaries (NVIDIA Llama by default).
    `history` is an optional list of {"q": ..., "a": ...} prior turns so
    follow-up questions keep context.
    Returns: {"answer": str, "provider": str, "note": str}.
    """
    transcript = (transcript or "").strip()
    question = (question or "").strip()
    if not question:
        return {"answer": "", "provider": "none", "note": "No question asked."}

    provider = summary_provider()
    if provider == "none":
        return {
            "answer": "", "provider": "none",
            "note": "Q&A needs NVIDIA_API_KEY or GEMINI_API_KEY in backend/.env.",
        }

    convo = ""
    for turn in (history or []):
        q = (turn.get("q") or "").strip()
        a = (turn.get("a") or "").strip()
        if q:
            convo += f"\nUser: {q}\nAssistant: {a}"

    prompt = (
        f"{QA_SYSTEM}\n\nTRANSCRIPT:\n{transcript}\n"
        f"{convo}\n\nUser: {question}\nAssistant:"
    )

    raw = _nvidia_chat(prompt) if provider == "nvidia" else _gemini_text(prompt)
    return {"answer": raw.strip(), "provider": provider, "note": ""}
