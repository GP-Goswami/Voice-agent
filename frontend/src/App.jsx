import { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function App() {
  const [file, setFile] = useState(null);
  const [fileName, setFileName] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  // summary / tasks
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);

  // Q&A
  const [chat, setChat] = useState([]); // [{q, a}]
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);

  // recording state
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);

  // Clean up the object URL whenever it changes / on unmount.
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  function setAudio(newFile, label) {
    setFile(newFile);
    setFileName(label);
    setResult(null);
    setSummary(null);
    setChat([]);
    setQuestion("");
    setError("");
    setCopied(false);
    setAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(newFile);
    });
  }

  function pickFile(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setAudio(f, f.name);
  }

  async function startRecording() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const recorded = new File([blob], "recording.webm", { type: "audio/webm" });
        setAudio(recorded, "recording.webm (from mic)");
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = mr;
      mr.start();
      setRecording(true);
      setResult(null);
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch (err) {
      setError("Could not access microphone: " + err.message);
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    clearInterval(timerRef.current);
    setRecording(false);
  }

  async function transcribe() {
    if (!file) {
      setError("Choose a file or record something first.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setSummary(null);
    setChat([]);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/api/transcribe`, {
        method: "POST",
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Request failed");
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function copyText() {
    if (!result?.text) return;
    navigator.clipboard.writeText(result.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function summarize() {
    if (!result?.text) return;
    setSummarizing(true);
    setSummary(null);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/summarize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: result.text }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Summary failed");
      setSummary(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setSummarizing(false);
    }
  }

  async function ask(e) {
    e?.preventDefault();
    const q = question.trim();
    if (!q || !result?.text) return;
    setAsking(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: result.text, question: q, history: chat }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Q&A failed");
      setChat((c) => [...c, { q, a: data.answer || "(no answer)" }]);
      setQuestion("");
    } catch (err) {
      setError(err.message);
    } finally {
      setAsking(false);
    }
  }

  const mmss = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(
    seconds % 60
  ).padStart(2, "0")}`;

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <span className="logo">🎙️</span>
          <div>
            <div className="brand-title">VoiceScribe</div>
            <div className="brand-sub">English voice → text</div>
          </div>
        </div>
        <a
          className="ghost-link"
          href={`${API_BASE}/api/health`}
          target="_blank"
          rel="noreferrer"
        >
          API status
        </a>
      </header>

      <main className="card">
        <h1>Convert your voice to text</h1>
        <p className="subtitle">
          Upload an audio file or record from your mic, preview it, then convert.
          Clear speech uses fast free recognition; hard audio falls back to AI.
        </p>

        {/* Step 1 — input */}
        <div className="step-label">1 · Add audio</div>
        <section className="dropzone">
          <label className="file-label">
            <input type="file" accept="audio/*" onChange={pickFile} />
            <span className="file-btn">📁 Choose file</span>
          </label>

          <span className="divider">or</span>

          {!recording ? (
            <button className="btn record" onClick={startRecording}>
              ⏺ Record
            </button>
          ) : (
            <button className="btn stop" onClick={stopRecording}>
              <span className="rec-dot" /> Stop · {mmss}
            </button>
          )}
        </section>

        {/* Step 2 — preview / play */}
        {audioUrl && (
          <div className="preview">
            <div className="step-label">2 · Preview &amp; test</div>
            <div className="player-row">
              <audio controls src={audioUrl} className="player" />
            </div>
            <div className="filename">
              <span className="file-chip">🎵 {fileName}</span>
            </div>
          </div>
        )}

        {/* Step 3 — convert */}
        <div className="step-label">3 · Convert</div>
        <button
          className="btn convert"
          onClick={transcribe}
          disabled={loading || recording || !file}
        >
          {loading ? (
            <>
              <span className="spinner" /> Converting…
            </>
          ) : (
            "Convert to text"
          )}
        </button>

        {error && <div className="error">⚠️ {error}</div>}

        {/* Result */}
        {result && (
          <div className="result">
            <div className="result-head">
              <span className="result-title">Transcript</span>
              <div className="badges">
                <span className={`badge level-${result.level}`}>
                  {result.level === "easy" ? "🟢 Easy" : "🔴 Hard"}
                </span>
                <span className="badge engine">
                  {result.engine === "gemini" ? "🤖 AI (Gemini)" : "⚡ Speech Recognition"}
                </span>
                {typeof result.confidence === "number" && (
                  <span className="badge conf">
                    {(result.confidence * 100).toFixed(0)}% conf
                  </span>
                )}
              </div>
            </div>

            <div className="transcript">
              {result.text ? result.text : <em>(no text recognized)</em>}
            </div>

            <div className="result-actions">
              <button
                className="btn small"
                onClick={copyText}
                disabled={!result.text}
              >
                {copied ? "✓ Copied" : "📋 Copy"}
              </button>
              <button
                className="btn small summarize-btn"
                onClick={summarize}
                disabled={!result.text || summarizing}
              >
                {summarizing ? (
                  <>
                    <span className="spinner dark" /> Finding tasks…
                  </>
                ) : (
                  "✨ Summary & tasks"
                )}
              </button>
              {result.note && <span className="note">{result.note}</span>}
            </div>

            {summary && (
              <div className="summary">
                <div className="summary-title">
                  📌 Action items
                  {summary.provider && summary.provider !== "none" && (
                    <span className="provider-tag">
                      via {summary.provider === "nvidia" ? "NVIDIA Llama" : "Gemini"}
                    </span>
                  )}
                </div>
                {summary.summary && (
                  <p className="summary-overview">{summary.summary}</p>
                )}
                {summary.tasks && summary.tasks.length > 0 ? (
                  <ul className="task-list">
                    {summary.tasks.map((t, i) => (
                      <li key={i}>
                        <span className="check">✓</span>
                        {t}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="note">
                    {summary.note || "No clear tasks found in this transcript."}
                  </p>
                )}
              </div>
            )}

            {/* Q&A — ask doubts about the transcript */}
            <div className="qa">
              <div className="qa-title">💬 Ask about this transcript</div>

              {chat.length > 0 && (
                <div className="qa-thread">
                  {chat.map((m, i) => (
                    <div className="qa-turn" key={i}>
                      <div className="qa-q">
                        <span className="qa-tag you">You</span>
                        {m.q}
                      </div>
                      <div className="qa-a">
                        <span className="qa-tag ai">AI</span>
                        <span>{m.a}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <form className="qa-form" onSubmit={ask}>
                <input
                  type="text"
                  className="qa-input"
                  placeholder="Ask a question or doubt… e.g. What are the deadlines?"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  disabled={asking}
                />
                <button
                  type="submit"
                  className="btn qa-send"
                  disabled={asking || !question.trim()}
                >
                  {asking ? <span className="spinner" /> : "Ask"}
                </button>
              </form>
            </div>
          </div>
        )}
      </main>

      <footer>Connected to {API_BASE}</footer>
    </div>
  );
}
