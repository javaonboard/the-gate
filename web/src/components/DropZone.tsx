import { useRef, useState } from "react";

/** Footage in.
 *
 *  Drop clips here and nothing else is asked of you. Each one is watched,
 *  the faces are found and matched against the people already in the scene,
 *  and the call reruns. */
export function DropZone({ sceneId, setupId, onIngested }: {
  sceneId: string;
  setupId: string;
  onIngested: (runId: string) => void;
}) {
  const [over, setOver] = useState(false);
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  async function send(files: FileList | null) {
    if (!files?.length) return;
    const clips = Array.from(files).filter((f) => f.type.startsWith("video/"));
    if (!clips.length) {
      setNote("Those weren't video files.");
      return;
    }

    setSending(true);
    setNote(null);
    const body = new FormData();
    for (const f of clips) body.append("files", f);
    body.append("setup_id", setupId);

    try {
      const res = await fetch(`/api/scenes/${sceneId}/footage`, {
        method: "POST",
        body,
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const { run_id } = (await res.json()) as { run_id: string };
      setNote(`${clips.length} clip${clips.length > 1 ? "s" : ""} in — watching now`);
      onIngested(run_id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="drop"
      data-over={over}
      data-busy={sending}
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
        multiple
        hidden
        onChange={(e) => send(e.target.files)}
      />
      <span className="drop-icon">{sending ? "◴" : "＋"}</span>
      <span>
        {sending
          ? "Taking it in…"
          : note ??
            (setupId
              ? `Drop footage for ${setupId.split("_").slice(-1)[0]}, or click to pick`
              : "Drop footage off the card, or click to pick")}
      </span>
    </div>
  );
}
