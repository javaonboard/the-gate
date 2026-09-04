import { useEffect, useState } from "react";
import { FaceCard } from "./FaceCard";
import { usd } from "../api";
import { SceneGoal } from "./SceneGoal";
import { WhoIsIn } from "./WhoIsIn";
import { SectionTitle } from "./SectionTitle";

/** What we have on each person, and what we still need.
 *
 *  One row per face the system found. Tick a cell to say the scene needs it;
 *  untick to say it does not. Nothing here was typed by anyone — the faces,
 *  the names and the grid all came out of the footage. */

type Cell = {
  band: string;
  label: string;
  help: string;
  required: boolean;
  state: "have" | "missing" | "skip";
  takes: string[];
  recover_cost_usd: number;
};

type Row = {
  character_id: string;
  name: string;
  face_uri: string;
  appearances: number;
  complete: boolean;
  cells: Cell[];
};

type SceneShot = {
  shot: string;
  label: string;
  help: string;
  required: boolean;
  state: string;
  takes: string[];
  recover_cost_usd: number;
};

type Matrix = {
  takes: number;
  bands: string[];
  band_label: Record<string, string>;
  band_help: Record<string, string>;
  characters: Row[];
  scene_shots?: SceneShot[];
  summary: {
    characters: number;
    required: number;
    have: number;
    completeness: number;
    exposure_usd: number;
    missing: { name: string; label: string; recover_cost_usd: number }[];
  };
};

export function CastMatrix({ sceneId, sceneName, reloadKey, onChanged }: {
  sceneId: string;
  sceneName?: string;
  reloadKey?: number;
  onChanged: () => void;
}) {
  const [data, setData] = useState<Matrix | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [merging, setMerging] = useState<string | null>(null);
  // Clicking a face used to start a merge, which is the rarer thing to want.
  // It opens the person instead, and the merge starts from inside.
  const [looking, setLooking] = useState<string | null>(null);
  const [goal, setGoal] = useState(false);
  const [who, setWho] = useState(false);

  async function load() {
    setData(await fetch(`/api/scenes/${sceneId}/matrix`).then((r) => r.json()));
  }

  useEffect(() => {
    void load();
  }, [sceneId, reloadKey]);

  async function toggle(characterId: string, band: string, required: boolean) {
    await fetch(`/api/scenes/${sceneId}/need/${characterId}/${band}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ required }),
    });
    await load();
    onChanged();
  }

  async function rename(characterId: string) {
    const name = draft.trim();
    setEditing(null);
    if (!name) return;
    await fetch(`/api/characters/${characterId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await load();
  }

  async function mergeInto(targetId: string) {
    const from = merging;
    setMerging(null);
    if (!from || from === targetId) return;
    await fetch(`/api/characters/${from}/is/${targetId}`, { method: "POST" });
    await load();
    onChanged();
  }

  if (!data) return null;

  if (!data.characters.length) {
    // Two different situations that used to read the same. No footage means
    // drop some in; footage with nobody in it means there is nothing to be
    // short of, which is not the same as being covered.
    //
    // Either way the goal has to be reachable from here. This branch used to
    // return a sentence and nothing else, which left the scenes that most
    // need a goal — a door handle, a chase with no faces in it — as the only
    // ones with no way to set or change one.
    const where = sceneName || "this scene";
    const asked = data.scene_shots?.filter((x) => x.required) ?? [];

    return (
      <section style={{ marginTop: 28 }}>
        <SectionTitle icon="people">What {where} needs</SectionTitle>

        <div className="empty">
          {data.takes > 0 ? (
            <>
              <b>No one on camera in {where}.</b> {data.takes} take
              {data.takes > 1 ? "s" : ""} logged, but no faces were found. That
              is not a fault — a scene can be a hand on a door handle, or a
              chase with nobody's face in it. Say what it needed and it gets
              checked against that instead.
            </>
          ) : (
            <>Nothing shot in {where} yet. Drop footage in and the cast builds itself.</>
          )}
        </div>

        <div className="scene-goal-line">
          <span>
            {asked.length ? (
              <>
                Needs{" "}
                {asked.map((x) => (
                  <span key={x.shot} className="tag" data-state={x.state}>
                    {x.label.toLowerCase()}
                  </span>
                ))}
              </>
            ) : (
              "Nothing asked of this scene yet."
            )}
          </span>
          <span className="scene-goal-actions">
            {data.takes > 0 && (
              <button className="secondary small" onClick={() => setWho(true)}>
                Say who is in it
              </button>
            )}
            <button className="secondary small" onClick={() => setGoal(true)}>
              {asked.length ? "Change the goal" : "Set the goal"}
            </button>
          </span>
        </div>

        {who && (
          <WhoIsIn
            sceneId={sceneId}
            sceneName={where}
            onClose={() => setWho(false)}
            onSaved={() => {
              void load();
              onChanged();
            }}
          />
        )}

        {goal && (
          <SceneGoal
            sceneId={sceneId}
            sceneName={where}
            onClose={() => setGoal(false)}
            onSaved={() => {
              void load();
              onChanged();
            }}
          />
        )}
      </section>
    );
  }

  const { summary } = data;

  return (
    <section style={{ marginTop: 28 }}>
      <SectionTitle
        icon="people"
        aside={
          <>
            {summary.have} of {summary.required} shots
            {summary.exposure_usd > 0 && (
              <> · {usd(summary.exposure_usd)} to get the rest later</>
            )}
          </>
        }
      >
        What {sceneName || "this scene"} needs
      </SectionTitle>

      {data.scene_shots && (
        <div className="scene-goal-line">
          <span>
            {data.scene_shots.some((x) => x.required) ? (
              <>
                Also needs{" "}
                {data.scene_shots
                  .filter((x) => x.required)
                  .map((x) => (
                    <span key={x.shot} className="tag"
                          data-state={x.state}>{x.label.toLowerCase()}</span>
                  ))}
              </>
            ) : (
              "Nothing asked of the scene itself — only coverage of the people below."
            )}
          </span>
          <span className="scene-goal-actions">
            <button className="secondary small" onClick={() => setWho(true)}>
              Someone missing?
            </button>
            <button className="secondary small" onClick={() => setGoal(true)}>
              {data.scene_shots.some((x) => x.required)
                ? "Change the goal"
                : "Set the goal"}
            </button>
          </span>
        </div>
      )}

      <div className="matrix">
        <div className="matrix-head">
          <div />
          {data.bands.map((b) => (
            <div key={b} title={data.band_help[b]}>
              {data.band_label[b]}
            </div>
          ))}
        </div>

        {data.characters.map((row) => (
          <div className="matrix-row" key={row.character_id}>
            <div className="person">
              <img
                src={row.face_uri}
                alt={row.name}
                className="face"
                data-merging={merging === row.character_id}
                data-target={!!merging && merging !== row.character_id}
                title={
                  merging === row.character_id
                    ? "Click another face to say they are the same person"
                    : merging
                      ? `Same person as the one you picked? Click to merge.`
                      : "Click to see them bigger, and every take they are in"
                }
                onClick={() =>
                  merging ? mergeInto(row.character_id) : setLooking(row.character_id)
                }
                onError={(e) => {
                  (e.target as HTMLImageElement).style.visibility = "hidden";
                }}
              />
              <div>
                {editing === row.character_id ? (
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => rename(row.character_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") rename(row.character_id);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    className="rename"
                  />
                ) : (
                  <b
                    onClick={() => {
                      setEditing(row.character_id);
                      setDraft(row.name);
                    }}
                    title="Click to name them"
                  >
                    {row.name}
                  </b>
                )}
                <small>in {row.appearances} takes</small>
              </div>
            </div>

            {data.bands.map((band) => {
              const cell = row.cells.find((c) => c.band === band)!;
              return (
                <button
                  key={band}
                  className="cell"
                  data-state={cell.state}
                  onClick={() => toggle(row.character_id, band, !cell.required)}
                  title={
                    cell.state === "have"
                      ? `${cell.takes.length} take(s): ${cell.takes.join(", ")}`
                      : cell.state === "missing"
                        ? `Missing — ${usd(cell.recover_cost_usd)} to get later. Click to say it isn't needed.`
                        : "Not needed. Click if you want it."
                  }
                >
                  {cell.state === "have"
                    ? "✓"
                    : cell.state === "missing"
                      ? "✕"
                      : "–"}
                  {cell.state === "have" && cell.takes.length > 1 && (
                    <span className="count">{cell.takes.length}</span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {who && (
        <WhoIsIn
          sceneId={sceneId}
          sceneName={sceneName || "this scene"}
          onClose={() => setWho(false)}
          onSaved={() => {
            void load();
            onChanged();
          }}
        />
      )}

      {goal && (
        <SceneGoal
          sceneId={sceneId}
          sceneName={sceneName || "this scene"}
          onClose={() => setGoal(false)}
          onSaved={() => {
            void load();
            onChanged();
          }}
        />
      )}

      {looking && (() => {
        const row = data.characters.find((c) => c.character_id === looking);
        return row ? (
          <FaceCard
            name={row.name}
            faceUri={row.face_uri}
            appearances={row.appearances}
            cells={row.cells}
            bandLabel={data.band_label}
            onClose={() => setLooking(null)}
            onMerge={() => { setMerging(looking); setLooking(null); }}
          />
        ) : null;
      })()}

      {merging && (
        <div className="merge-hint">
          Now click the face that is the same person.
          <button onClick={() => setMerging(null)}>cancel</button>
        </div>
      )}

      <div className="matrix-key">
        <span><b>✓</b> got it</span>
        <span><b>✕</b> needed, not shot</span>
        <span><b>–</b> not needed</span>
        <span style={{ marginLeft: "auto", color: "var(--faint)" }}>
          click a square to change what's needed · click a face to merge two of
          the same person
        </span>
      </div>
    </section>
  );
}
