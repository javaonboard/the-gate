import { useEffect, useState } from "react";

/** The camera positions for this scene.
 *
 *  A setup is one place the camera stands. You shoot several takes of it,
 *  call it good, and move on — so takes belong to a setup, not to a scene.
 *
 *  Pick one and dropped footage lands there. Pick none and the system files
 *  each clip under its own setup, named after the framing, rather than
 *  quietly mixing unrelated angles together. */

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
      <span className="strip-label">Camera positions</span>

      <button
        className="chip"
        data-active={selected === ""}
        onClick={() => onSelect("")}
        title="Let the system file each clip by its framing"
      >
        auto
      </button>

      {setups.map((s) => (
        <button
          key={s.setup_id}
          className="chip"
          data-active={selected === s.setup_id}
          data-shot={s.shot}
          onClick={() => onSelect(s.setup_id)}
          title={
            s.takes
              ? `${s.takes} take${s.takes > 1 ? "s" : ""}${s.shot_size ? ` · ${s.shot_size}` : ""}`
              : "Nothing shot here yet"
          }
        >
          {s.label}
          {s.takes > 0 && <span className="chip-count">{s.takes}</span>}
        </button>
      ))}

      <button className="chip add" onClick={add} disabled={adding}>
        ＋
      </button>
    </div>
  );
}
