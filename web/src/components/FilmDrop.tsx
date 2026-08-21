import { useRef, useState } from "react";

/** Drop a whole film in.
 *
 *  For anyone arriving with a video and no idea how this is meant to be
 *  organised. It cuts the file at every camera change, watches each shot,
 *  groups them into scenes by where they were filmed, finds the faces and
 *  checks for problems. Nothing is typed. */
export function FilmDrop({ onStarted }: { onStarted: (runId: string) => void }) {
  const [over, setOver] = useState(false);
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  async function send(files: FileList | null) {
    const film = files?.[0];
    if (!film) return;
    if (!film.type.startsWith("video/")) {
      setNote("That wasn't a video file.");
      return;
    }

    setSending(true);
    setNote(null);
    const body = new FormData();
    body.append("file", film);

    try {
      const res = await fetch("/api/film", { method: "POST", body });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const { run_id } = (await res.json()) as { run_id: string };
      setNote("Cutting it up — this takes a few minutes");
      onStarted(run_id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="filmdrop"
      data-over={over}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        void send(e.dataTransfer.files);
      }}
      onClick={() => picker.current?.click()}
    >
      <input
        ref={picker}
        type="file"
        accept="video/*"
        hidden
        onChange={(e) => send(e.target.files)}
      />
      <span>
        {sending ? "Uploading…" : note ?? "…or drop a whole film and it sorts itself into scenes"}
      </span>
    </div>
  );
}
