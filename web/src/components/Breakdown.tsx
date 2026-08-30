import { useEffect, useState } from "react";
import type { GateCall } from "../api";
import { SectionTitle } from "./SectionTitle";

/** The scene, opened up.
 *
 *  Every level is the reason for the one below it. A scene is short because a
 *  take is unusable; a take is unusable because of what happened in it; a
 *  character is missing their close-up because the only one is in that take.
 *  Shown as separate lists those connections are invisible, which is why this
 *  is one tree and not four panels. */

type Person = {
  character_id: string;
  name: string;
  face_uri: string;
  prominence: string;
};

type Gives = { character_id: string; band: string; band_label: string };

type Problem = {
  severity: string;
  category: string;
  what: string;
  where: string;
  at_seconds: number;
};

type Take = {
  take_id: string;
  setup_id: string;
  shot_size_plain: string;
  movement_plain: string;
  seconds: number;
  circled: boolean;
  playable: boolean;
  usable: boolean;
  characters: Person[];
  gives: Gives[];
  problems: Problem[];
};

type Cell = { band: string; required: boolean; takes: string[]; recover_cost_usd: number };

type CharacterRow = {
  character_id: string;
  name: string;
  face_uri: string;
  appearances: number;
  cells: Record<string, Cell>;
};

type Data = {
  scene: {
    scene_id: string; place: string; int_ext: string; day_night: string;
    synopsis: string; takes: number; positions: number;
  };
  bands: string[];
  band_label: Record<string, string>;
  characters: CharacterRow[];
  summary: { completeness: number; exposure_usd: number; judged: boolean };
  takes: Take[];
};

const length = (s: number) =>
  s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;

const money = (n: number) => `$${n.toLocaleString()}`;

export function Breakdown({ sceneId, reloadKey, call }: {
  sceneId: string;
  reloadKey: number;
  call?: GateCall | null;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [open, setOpen] = useState<string>("");
  const [playing, setPlaying] = useState<string>("");

  useEffect(() => {
    if (!sceneId) return;
    setData(null);
    void fetch(`/api/scenes/${sceneId}/breakdown`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, [sceneId, reloadKey]);

  if (!data) return null;
  const { scene, characters, takes, band_label } = data;

  /** What this person still needs, across the whole scene. */
  const standing = (id: string) => {
    const row = characters.find((c) => c.character_id === id);
    if (!row) return null;
    const missing = Object.values(row.cells)
      .filter((c) => c.required && c.takes.length === 0)
      .map((c) => band_label[c.band] ?? c.band);
    const have = Object.values(row.cells)
      .filter((c) => c.takes.length > 0)
      .map((c) => band_label[c.band] ?? c.band);
    return { missing, have };
  };

  return (
    <section className="breakdown">
      <SectionTitle icon="scenes">The scene, opened up</SectionTitle>

      <div className="bd-head">
        <div>
          <span className="bd-verdict">
            {call && (
              <span className="badge small" data-go={call.go}
                    data-unjudged={call.coverage.judged === false}>
                {call.verdict}
              </span>
            )}
            <b>{scene.place}</b>
          </span>
          <small>
            {scene.int_ext} {scene.day_night} · {scene.takes} pieces of footage ·{" "}
            {scene.positions} camera position{scene.positions === 1 ? "" : "s"}
          </small>
        </div>
        <div className="bd-head-right">
          <b>{Math.round((data.summary.completeness ?? 0) * 100)}%</b>
          <small>of what this scene needs</small>
        </div>
      </div>
      {scene.synopsis && <p className="bd-synopsis">{scene.synopsis}</p>}

      <div className="bd-takes">
        {takes.map((t) => {
          const isOpen = open === t.take_id;
          const blocking = t.problems.filter((p) => p.severity === "blocking");
          return (
            <div className="bd-take" key={t.take_id} data-usable={t.usable}>
              <button
                className="bd-row"
                onClick={() => setOpen(isOpen ? "" : t.take_id)}
              >
                <span className="bd-caret">{isOpen ? "▾" : "▸"}</span>
                <span className="bd-name">
                  {t.take_id}
                  {t.circled && <em title="the director's preferred take">★</em>}
                </span>
                <span className="bd-len">{length(t.seconds)}</span>
                <span className="bd-what">
                  {t.shot_size_plain}, {t.movement_plain}
                </span>
                <span className="bd-who">
                  {t.characters.length === 0
                    ? "nobody in frame"
                    : t.characters.map((c) => c.name).join(", ")}
                </span>
                {!t.usable && <span className="bd-flag">can't be used</span>}
                {t.usable && blocking.length === 0 && t.problems.length > 0 && (
                  <span className="bd-flag warn">{t.problems.length} note</span>
                )}
              </button>

              {isOpen && (
                <div className="bd-body">
                  {t.playable ? (
                    playing === t.take_id ? (
                      <video
                        className="bd-video"
                        src={`/api/takes/${t.take_id}/video`}
                        controls
                        autoPlay
                      />
                    ) : (
                      <button className="bd-play"
                              onClick={() => setPlaying(t.take_id)}>
                        ▶ Watch this take
                      </button>
                    )
                  ) : (
                    <small className="bd-nofile">The file for this take isn't here</small>
                  )}

                  {t.characters.length > 0 && (
                    <div className="bd-people">
                      {t.characters.map((c) => {
                        const gives = t.gives
                          .filter((g) => g.character_id === c.character_id)
                          .map((g) => g.band_label);
                        const s = standing(c.character_id);
                        return (
                          <div className="bd-person" key={c.character_id}>
                            {c.face_uri ? (
                              <img src={c.face_uri} alt={c.name} />
                            ) : (
                              <span className="bd-noface" />
                            )}
                            <div className="bd-person-body">
                              <b>{c.name}</b>
                              <small className="bd-prom">{c.prominence}</small>

                              <div className="bd-gives">
                                {gives.length > 0 ? (
                                  <>
                                    this take gives them{" "}
                                    {gives.map((g) => (
                                      <span className="tag have" key={g}>{g}</span>
                                    ))}
                                  </>
                                ) : (
                                  <span className="bd-nothing">
                                    {t.usable
                                      ? "this take doesn't cover them"
                                      : "nothing — this take can't be used"}
                                  </span>
                                )}
                              </div>

                              {s && s.missing.length > 0 && (
                                <div className="bd-gives">
                                  still missing{" "}
                                  {s.missing.map((m) => (
                                    <span className="tag missing" key={m}>{m}</span>
                                  ))}
                                </div>
                              )}
                              {s && s.missing.length === 0 && (
                                <div className="bd-gives">
                                  <span className="tag have">fully covered</span>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {t.problems.length > 0 && (
                    <ul className="bd-problems">
                      {t.problems.map((p, i) => (
                        <li key={i} data-severity={p.severity}>
                          <span className="bd-sev">{p.severity}</span>
                          <span className="bd-issue">
                            {p.what}
                            {p.at_seconds >= 0 && (
                              <em> · at {length(p.at_seconds)}</em>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {data.summary.exposure_usd > 0 && (
        <p className="bd-exposure">
          Moving on now leaves {money(data.summary.exposure_usd)} of shots to
          recover later.
        </p>
      )}
    </section>
  );
}
