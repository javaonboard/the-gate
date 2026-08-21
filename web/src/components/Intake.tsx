import { useRef, useState } from "react";

/** The three ways footage gets in — or gets thrown away.
 *
 *  Left: a whole film, which sorts itself into scenes.
 *  Middle: clips for the scene that is open.
 *  Right: clear everything and start from nothing. */

type Props = {
  sceneId: string;
  sceneName: string;
  onStarted: (runId: string) => void;
  onCleared: () => void;
};

export function Intake({ sceneId, sceneName, onStarted, onCleared }: Props) {
  const [busy, setBusy] = useState<"film" | "clips" | "reset" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [over, setOver] = useState<"film" | "clips" | null>(null);
  const filmPicker = useRef<HTMLInputElement>(null);
  const clipPicker = useRef<HTMLInputElement>(null);

  async function sendFilm(files: FileList | null) {
    const film = files?.[0];
    if (!film?.type.startsWith("video/")) return setNote("Not a video file.");

    setBusy("film");
    setNote(null);
    const body = new FormData();
    body.append("file", film);
    try {
      const res = await fetch("/api/film", { method: "POST", body });
      if (!res.ok) throw new Error(`${res.status}`);
      const { run_id } = await res.json();
      setNote("Cutting it into shots — a few minutes");
      onStarted(run_id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function sendClips(files: FileList | null) {
    const clips = Array.from(files ?? []).filter((f) =>
      f.type.startsWith("video/")
    );
    if (!clips.length) return setNote("No video files there.");

    setBusy("clips");
    setNote(null);
    const body = new FormData();
    for (const c of clips) body.append("files", c);
    body.append("setup_id", "");
    try {
      const res = await fetch(`/api/scenes/${sceneId}/footage`, {
        method: "POST",
        body,
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const { run_id } = await res.json();
      setNote(`${clips.length} clip${clips.length > 1 ? "s" : ""} in`);
      onStarted(run_id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    if (!confirm("Throw away every scene, take and face, and start empty?")) return;
    setBusy("reset");
    try {
      await fetch("/api/workspace", { method: "DELETE" });
      onCleared();
      setNote("Cleared — start by dropping a film");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="intake">
      <button
        className="intake-card"
        data-over={over === "film"}
        data-busy={busy === "film"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver("film");
        }}
        onDragLeave={() => setOver(null)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(null);
          void sendFilm(e.dataTransfer.files);
        }}
        onClick={() => filmPicker.current?.click()}
      >
        <input ref={filmPicker} type="file" accept="video/*" hidden
               onChange={(e) => sendFilm(e.target.files)} />
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect x="2.5" y="4.5" width="19" height="15" rx="2.5"
                stroke="currentColor" strokeWidth="1.6" />
          <path d="M8 4.5v15M16 4.5v15M2.5 12h19" stroke="currentColor"
                strokeWidth="1.4" />
        </svg>
        <b>{busy === "film" ? "Uploading…" : "A whole film"}</b>
        <small>
          Cut at every camera change, sorted into scenes by where it was
          filmed. Nothing to fill in.
        </small>
      </button>

      <button
        className="intake-card"
        data-over={over === "clips"}
        data-busy={busy === "clips"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver("clips");
        }}
        onDragLeave={() => setOver(null)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(null);
          void sendClips(e.dataTransfer.files);
        }}
        onClick={() => clipPicker.current?.click()}
      >
        <input ref={clipPicker} type="file" accept="video/*" multiple hidden
               onChange={(e) => sendClips(e.target.files)} />
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect x="2" y="5" width="14" height="14" rx="2.5"
                stroke="currentColor" strokeWidth="1.6" />
          <path d="M16 10.5 21.2 7.4a.7.7 0 0 1 1.05.6v8a.7.7 0 0 1-1.05.6L16 13.5z"
                stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
        <b>{busy === "clips" ? "Uploading…" : "Takes off the card"}</b>
        <small>
          Goes straight into {sceneName || "the open scene"}. Use this when you
          already know which scene it belongs to.
        </small>
      </button>

      <button className="intake-card danger" onClick={reset}
              data-busy={busy === "reset"}>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M4 7h16M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7M6.5 7l.8 12.1c0 .8.7 1.4 1.5 1.4h6.4c.8 0 1.5-.6 1.5-1.4L17.5 7"
                stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <b>{busy === "reset" ? "Clearing…" : "Start from nothing"}</b>
        <small>
          Throw away every scene, take and face in your copy. The demo is left
          alone.
        </small>
      </button>

      {note && <div className="intake-note">{note}</div>}
    </div>
  );
}
