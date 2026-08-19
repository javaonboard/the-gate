import { useEffect, useState } from "react";

/** The scenes in the day's work.
 *
 *  A scene is one continuous piece of story in one place — the canal street,
 *  the workshop. It is what people say out loud, and it is what footage gets
 *  shot into. Camera positions live inside a scene and are worked out from the
 *  footage; nobody should have to file them by hand. */

export type Scene = {
  scene_id: string;
  number: string;
  place: string;
  where: string;
  when: string;
  takes: number;
  positions: number;
  people: number;
  have: number;
  required: number;
  missing: number;
  exposure_usd: number;
  complete: boolean;
};

export function SceneBar({ selected, onSelect, reloadKey }: {
  selected: string;
  onSelect: (scene: Scene) => void;
  reloadKey: number;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [adding, setAdding] = useState(false);
  const [place, setPlace] = useState("");
  const [interior, setInterior] = useState(false);

  async function load() {
    const rows: Scene[] = await fetch("/api/scenes").then((r) => r.json());
    setScenes(rows);
    if (!selected && rows.length) onSelect(rows[0]);
  }

  useEffect(() => {
    void load();
  }, [reloadKey]);

  async function create() {
    if (!place.trim()) return;
    const s = await fetch("/api/scenes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place, interior, when: "DAY" }),
    }).then((r) => r.json());
    setPlace("");
    setAdding(false);
    const rows: Scene[] = await fetch("/api/scenes").then((r) => r.json());
    setScenes(rows);
    const made = rows.find((r) => r.scene_id === s.scene_id);
    if (made) onSelect(made);
  }

  return (
    <div className="scenebar">
      {scenes.map((s) => (
        <button
          key={s.scene_id}
          className="scene"
          data-active={selected === s.scene_id}
          onClick={() => onSelect(s)}
        >
          <span className="scene-no">{s.number}</span>
          <span className="scene-body">
            <b>{s.place}</b>
            <small>
              {s.where} · {s.takes ? `${s.takes} takes` : "nothing shot"}
              {s.people > 0 && ` · ${s.people} on camera`}
            </small>
            {s.required > 0 && (
              <span className="scene-state">
                <span className="scene-meter">
                  <span
                    style={{
                      width: `${(s.have / s.required) * 100}%`,
                      background: s.complete ? "var(--go)" : "var(--warn)",
                    }}
                  />
                </span>
                <em data-short={!s.complete}>
                  {s.complete
                    ? "covered"
                    : `${s.missing} shot${s.missing > 1 ? "s" : ""} short`}
                </em>
              </span>
            )}
          </span>
        </button>
      ))}

      {adding ? (
        <div className="scene new">
          <input
            autoFocus
            value={place}
            placeholder="where is it?"
            onChange={(e) => setPlace(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void create();
              if (e.key === "Escape") setAdding(false);
            }}
          />
          <label>
            <input
              type="checkbox"
              checked={interior}
              onChange={(e) => setInterior(e.target.checked)}
            />
            inside
          </label>
          <button className="primary" onClick={create}>
            add
          </button>
        </div>
      ) : (
        <button className="scene add" onClick={() => setAdding(true)}>
          <span className="scene-no">＋</span>
          <span className="scene-body">
            <b>new scene</b>
            <small>another place or time</small>
          </span>
        </button>
      )}
    </div>
  );
}
