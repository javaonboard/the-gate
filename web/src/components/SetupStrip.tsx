import { useEffect, useState } from "react";

/** The camera positions for this scene.
 *
 *  A setup is one place the camera stands. You shoot several takes of it,
 *  call it good, and move on — so takes belong to a setup, not to a scene.
 *
 *  Pick one and dropped footage lands there. Pick none and the system files
 *  each clip under its own setup, named after the framing, rather than
 *  quietly mixing unrelated angles together. */

const FRAMING: Record<string, string> = {
  ELS: "very wide",
  LS: "wide",
  MLS: "wide-ish",
  MS: "medium",
  MCU: "medium close",
  CU: "close-up",
  ECU: "very close",
};

type Setup = {
  setup_id: string;
  label: string;
  takes: number;
  shot_size: string;
  shot: boolean;
};

export function SetupStrip({ sceneId, selected, onSelect, reloadKey }: {
  sceneId: string;
  selected: string;
  onSelect: (setupId: string) => void;
  reloadKey: number;
}) {
  const [setups, setSetups] = useState<Setup[]>([]);
  const [adding, setAdding] = useState(false);

  async function load() {
    setSetups(await fetch(`/api/scenes/${sceneId}/setups`).then((r) => r.json()));
  }

  useEffect(() => {
    void load();
  }, [sceneId, reloadKey]);

  async function add() {
    setAdding(true);
    try {
      const s = await fetch(`/api/scenes/${sceneId}/setups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: "" }),
      }).then((r) => r.json());
      await load();
      onSelect(s.setup_id);
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="strip">
      <div className="strip-label">
        Camera positions
        <small>
          each one is a place the camera stands — a wide, a close-up, an
          over-the-shoulder. Several attempts get shot from each.
        </small>
      </div>

      <button
        className="chip auto"
        data-active={selected === ""}
        onClick={() => onSelect("")}
        title="Each clip gets filed under its own position, by framing"
      >
        <b>auto</b>
        <small>sort it for me</small>
      </button>

      {setups.map((s) => (
        <button
          key={s.setup_id}
          className="chip"
          data-active={selected === s.setup_id}
          data-shot={s.shot}
          onClick={() => onSelect(s.setup_id)}
          title={`Position ${s.label} — one place the camera stands. `
            + (s.takes ? `${s.takes} attempt(s) recorded here.` : "Nothing shot here yet.")}
        >
          <b>
            {s.label}
            {s.shot_size && (
              <span className="chip-framing">
                {FRAMING[s.shot_size] ?? s.shot_size.toLowerCase()}
              </span>
            )}
          </b>
          <small>
            {s.takes === 0
              ? "not shot"
              : `${s.takes} take${s.takes > 1 ? "s" : ""}`}
          </small>
        </button>
      ))}

      <button className="chip add" onClick={add} disabled={adding} title="Plan another camera position">
        <b>＋</b>
        <small>position</small>
      </button>
    </div>
  );
}
