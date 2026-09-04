import { useEffect, useState } from "react";
import { pct } from "../api";
import type { GateCall } from "../api";
import { SectionTitle } from "./SectionTitle";
import { FaceCard } from "./FaceCard";

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
  saw: string[];
  screen_direction: string;
  eyeline: string;
  focus: number;
  exposure: number;
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

export function Breakdown({ sceneId, reloadKey, call, onJoined }: {
  sceneId: string;
  reloadKey: number;
  call?: GateCall | null;
  onJoined?: () => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [open, setOpen] = useState<string>("");
  const [playing, setPlaying] = useState<string>("");
  // Whose face is being looked at. A character under a take is the moment the
  // question "is that really them?" comes up, so it opens from there too.
  const [looking, setLooking] = useState<string>("");
  // Takes ticked as being really one take. Where a take begins is a judgment
  // and the agent gets it wrong both ways on raw footage; whoever is watching
  // is never in any doubt, so they say so and it stays said.
  const [joining, setJoining] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [elsewhere, setElsewhere] = useState<{ scene_id: string; place: string }[]>([]);

  // Where else a take could belong. Grouping by what a place looks like puts
  // the odd take in the wrong scene, and two grey interiors look alike.
  useEffect(() => {
    void fetch("/api/scenes")
      .then((r) => r.json())
      .then((rows) => setElsewhere(
        (Array.isArray(rows) ? rows : rows.scenes ?? [])
          .filter((s: { scene_id: string }) => s.scene_id !== sceneId)
          .map((s: { scene_id: string; place: string }) =>
            ({ scene_id: s.scene_id, place: s.place }))));
  }, [sceneId, reloadKey]);

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
  const who = characters.find((c) => c.character_id === looking);

  async function move(to: string) {
    setBusy(true);
    try {
      const done = await fetch("/api/takes/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ take_ids: joining, scene_id: to }),
      });
      if (!done.ok) {
        const why = await done.json().catch(() => ({}));
        alert(why.detail ?? "Could not move those takes");
        return;
      }
      setJoining([]);
      onJoined?.();
    } finally {
      setBusy(false);
    }
  }

  async function join() {
    setBusy(true);
    try {
      // Order matters: the earliest take keeps its number and the clips are
      // put back together in the order they were shot.
      const inOrder = takes
        .map((t) => t.take_id)
        .filter((id) => joining.includes(id));
      const done = await fetch("/api/takes/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ take_ids: inOrder }),
      });
      if (!done.ok) {
        const why = await done.json().catch(() => ({}));
        alert(why.detail ?? "Could not join those takes");
        return;
      }
      setJoining([]);
      onJoined?.();
    } finally {
      setBusy(false);
    }
  }

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

      {who && (
        <FaceCard
          name={who.name}
          faceUri={who.face_uri}
          appearances={who.appearances}
          cells={Object.values(who.cells)}
          bandLabel={band_label}
          onClose={() => setLooking("")}
        />
      )}

      {joining.length > 0 && (
        <div className="bd-joining">
          <span>
            {joining.length === 1
              ? "Tick another take that is part of the same one"
              : `${joining.length} takes, really one`}
          </span>
          {joining.length > 1 && (
            <button className="primary small" disabled={busy} onClick={join}>
              {busy ? "Working…" : "Join them"}
            </button>
          )}
          {elsewhere.length > 0 && (
            <select
              className="bd-move"
              value=""
              disabled={busy}
              onChange={(e) => e.target.value && move(e.target.value)}
            >
              <option value="">move to…</option>
              {elsewhere.map((s) => (
                <option key={s.scene_id} value={s.scene_id}>{s.place}</option>
              ))}
            </select>
          )}
          <button className="linky" onClick={() => setJoining([])}>cancel</button>
        </div>
      )}

      <div className="bd-takes">
        {takes.map((t) => {
          const isOpen = open === t.take_id;
          const blocking = t.problems.filter((p) => p.severity === "blocking");
          return (
            <div className="bd-take" key={t.take_id} data-usable={t.usable}
                 data-joining={joining.includes(t.take_id)}>
              <button
                className="bd-row"
                onClick={() => setOpen(isOpen ? "" : t.take_id)}
              >
                <span
                  className="bd-tick"
                  role="checkbox"
                  aria-checked={joining.includes(t.take_id)}
                  title="Tick two or more that are really one take"
                  onClick={(e) => {
                    e.stopPropagation();
                    setJoining((was) => was.includes(t.take_id)
                      ? was.filter((x) => x !== t.take_id)
                      : [...was, t.take_id]);
                  }}
                >
                  {joining.includes(t.take_id) ? "☑" : "☐"}
                </span>
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

                  {/* Everything else on this take is a verdict. This is what
                      the model saw to reach it, in its own words, sitting
                      next to the take so the two can be read against each
                      other. It is the only place the AD can catch it being
                      wrong. */}
                  {(t.saw.length > 0 || t.eyeline) && (
                    <div className="bd-saw">
                      <h5>What it saw</h5>
                      {t.saw.length > 0 && (
                        <ul>
                          {t.saw.map((who, i) => <li key={i}>{who}</li>)}
                        </ul>
                      )}
                      <dl>
                        {t.eyeline && (
                          <><dt>looking at</dt><dd>{t.eyeline}</dd></>
                        )}
                        {t.screen_direction && (
                          <><dt>facing</dt><dd>{t.screen_direction}</dd></>
                        )}
                        {t.focus > 0 && (
                          <><dt>focus</dt><dd>{pct(t.focus)}</dd></>
                        )}
                        {t.exposure > 0 && (
                          <><dt>exposure</dt><dd>{pct(t.exposure)}</dd></>
                        )}
                      </dl>
                    </div>
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
                              <img
                                src={c.face_uri}
                                alt={c.name}
                                title="Click to see them bigger, and every take they are in"
                                onClick={() => setLooking(c.character_id)}
                              />
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
