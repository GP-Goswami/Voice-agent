"""Two-phase voice-to-text pipeline.

Phase 1: free Speech Recognition (Google Web Speech via the SpeechRecognition
         library). Produces a transcript + confidence and a difficulty "level".
Phase 2: AI fallback (Gemini) — only used when Phase 1 says the audio is "hard".

Both phases return the same dict shape so the API contract stays stable:

    {
      "text": str,
      "level": "easy" | "hard",
      "engine": "speech_recognition" | "gemini",
      "confidence": float,
      "phase": 1 | 2,
      "note": str,
    }
"""

import glob
import io
import os
import shutil

import speech_recognition as sr
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

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
MIN_WORDS = int(os.getenv("MIN_WORDS", "1"))

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


# --- audio decoding ---------------------------------------------------------

# Google's free Web Speech endpoint only reliably transcribes short clips
# (~10-15s). For longer audio we split into chunks on silence and transcribe
# each, so nothing gets cut off.
CHUNK_MAX_MS = 30_000  # cap each chunk at ~30s for the free recognizer


def decode_to_segment(raw: bytes) -> AudioSegment:
    """Decode any uploaded/recorded audio into a 16 kHz mono AudioSegment."""
    audio = AudioSegment.from_file(io.BytesIO(raw))
    return audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)


def segment_to_wav_bytes(audio: AudioSegment) -> bytes:
    out = io.BytesIO()
    audio.export(out, format="wav")
    return out.getvalue()


def decode_to_wav(raw: bytes) -> bytes:
    """Back-compat helper: decode any audio to 16 kHz mono WAV bytes."""
    return segment_to_wav_bytes(decode_to_segment(raw))


def _split_into_chunks(audio: AudioSegment) -> list[AudioSegment]:
    """Split audio into <=CHUNK_MAX_MS pieces, preferring silence boundaries."""
    if len(audio) <= CHUNK_MAX_MS:
        return [audio]

    from pydub.silence import split_on_silence

    # Split on pauses so we never cut a word in half.
    pieces = split_on_silence(
        audio,
        min_silence_len=400,
        silence_thresh=audio.dBFS - 16,
        keep_silence=300,
    )
    if not pieces:
        pieces = [audio]

    # Merge tiny pieces up to the cap; hard-slice anything still too long.
    chunks: list[AudioSegment] = []
    buf: AudioSegment | None = None
    for piece in pieces:
        if len(piece) > CHUNK_MAX_MS:
            if buf is not None:
                chunks.append(buf)
                buf = None
            for i in range(0, len(piece), CHUNK_MAX_MS):
                chunks.append(piece[i:i + CHUNK_MAX_MS])
        elif buf is None:
            buf = piece
        elif len(buf) + len(piece) <= CHUNK_MAX_MS:
            buf += piece
        else:
            chunks.append(buf)
            buf = piece
    if buf is not None:
        chunks.append(buf)
    return chunks


# --- Phase 1: free speech recognition --------------------------------------

def _recognize_chunk(recognizer: sr.Recognizer, chunk: AudioSegment) -> tuple[str, float]:
    """Recognize one chunk; returns (text, confidence). Empty on failure."""
    with sr.AudioFile(io.BytesIO(segment_to_wav_bytes(chunk))) as source:
        audio_data = recognizer.record(source)
    try:
        raw = recognizer.recognize_google(audio_data, language="en-US", show_all=True)
    except (sr.UnknownValueError, sr.RequestError):
        return "", 0.0
    return _best_alternative(raw)


def phase1_speech_recognition(audio: AudioSegment) -> dict:
    """Run Google's free Web Speech recognizer over (chunked) audio and grade it.

    Long audio is split on silence and transcribed chunk-by-chunk, then joined,
    so nothing is dropped. `level` is "easy" when we're confident enough to keep
    the result, otherwise "hard" (caller may then run Phase 2).
    """
    recognizer = sr.Recognizer()
    chunks = _split_into_chunks(audio)

    parts: list[str] = []
    confidences: list[float] = []
    for chunk in chunks:
        text, conf = _recognize_chunk(recognizer, chunk)
        if text:
            parts.append(text)
            confidences.append(conf)

    full_text = " ".join(parts).strip()
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    word_count = len(full_text.split())
    recognized_chunks = len(parts)
    multi = len(chunks) > 1

    if not full_text or word_count < MIN_WORDS:
        level = "hard"
    elif confidence >= CONFIDENCE_THRESHOLD:
        level = "easy"
    else:
        level = "hard"

    # If some chunks of a multi-part clip failed, the result is incomplete —
    # treat as "hard" so Phase 2 (Gemini) can transcribe the whole thing.
    if multi and recognized_chunks < len(chunks):
        level = "hard"
        note = (
            f"Recognized {recognized_chunks}/{len(chunks)} segments; "
            "audio may be incomplete."
        )
    elif level == "easy":
        note = f"Clear speech ({len(chunks)} segment(s))."
    else:
        note = "Low confidence speech."

    return {
        "text": full_text,
        "level": level,
        "engine": "speech_recognition",
        "confidence": round(confidence, 3),
        "phase": 1,
        "note": note,
    }


def _best_alternative(raw) -> tuple[str, float]:
    """Pull the top transcript + confidence out of recognize_google(show_all)."""
    if not raw or not isinstance(raw, dict):
        return "", 0.0
    alternatives = raw.get("alternative", [])
    if not alternatives:
        return "", 0.0
    best = alternatives[0]
    text = best.get("transcript", "").strip()
    # Google only attaches "confidence" to the top alternative, and not always.
    confidence = float(best.get("confidence", 0.0))
    if confidence == 0.0 and text:
        # No score returned but we did get text — assume moderate confidence so a
        # clean result isn't needlessly pushed to the paid/AI phase.
        confidence = 0.8
    return text, confidence


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
    }


# --- orchestration ----------------------------------------------------------

def transcribe(raw: bytes) -> dict:
    """Full pipeline: decode -> Phase 1 (chunked) -> (maybe) Phase 2."""
    audio = decode_to_segment(raw)

    result = phase1_speech_recognition(audio)

    # "easy" + has text -> we're done with the cheap path.
    if result["level"] == "easy" and result["text"]:
        return result

    # Hard / incomplete audio: try the AI fallback if it's configured.
    if gemini_available():
        ai = phase2_gemini(segment_to_wav_bytes(audio))
        if ai["text"]:
            return ai
        # AI produced nothing — fall through to whatever Phase 1 had.
        if result["text"]:
            result["note"] = "Phase 2 returned nothing; showing Phase 1 result."
            return result
        return ai

    # No AI configured: return the best Phase 1 result, flagged hard.
    if result["text"]:
        result["note"] = (
            "Hard/long speech and no AI key set — showing best free guess. "
            "Set GEMINI_API_KEY to improve accuracy on long or unclear audio."
        )
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
