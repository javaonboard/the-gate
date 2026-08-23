import { useState } from "react";
import type { GateCall } from "../api";
import { clock, pct, usd } from "../api";
import { CastMatrix } from "../components/CastMatrix";
import { Intake } from "../components/Intake";
import { Problems } from "../components/Problems";
import { SceneBar, type Scene } from "../components/SceneBar";
import { SectionTitle } from "../components/SectionTitle";
import { WorldPanel } from "../components/WorldPanel";

/** Several missing shots of one person read as one job, not three. */
function groupByPerson(options: GateCall["options"]) {
  const by = new Map<string, { person: string; saving: number; shots: GateCall["options"] }>();
  for (const o of options) {
    const person = o.subject && o.subject !== "-" ? o.subject : "the scene";
    const found = by.get(person) ?? { person, saving: 0, shots: [] };
    found.saving += o.saving_usd;
    found.shots.push(o);
    by.set(person, found);
  }
  return [...by.values()].sort((a, b) => b.saving - a.saving);
}

function oddsColour(p: number) {
  if (p >= 0.7) return "var(--go)";
  if (p >= 0.4) return "var(--warn)";
  return "var(--nogo)";
}

export function Today({ call, scene, dataKey, fresh, onScene, onIngested, onChanged }: {
  call: GateCall | null;
  scene: Scene | null;
  dataKey: number;
  fresh: string[];
  onScene: (s: Scene) => void;
  onIngested: (runId: string) => void;
  onChanged: () => void;
}) {
  const [bumped, setBumped] = useState(0);
  const reloadKey = bumped + dataKey;
  const setReloadKey = (fn: (n: number) => number) => setBumped(fn);
  const sceneId = scene?.scene_id ?? "";

  return (
    <>
      {/* the call, compact, straight under the banner */}
      <div className="topline">
        {call ? (
          <>
            <span className="badge" data-go={call.go}>
              {call.verdict}
            </span>

            <span className="topstat">
              <b style={{ color: oddsColour(call.day.p_make_the_day) }}>
                {pct(call.day.p_make_the_day)}
              </b>
              <small>chance of finishing today</small>
            </span>

            <span className="topstat">
              <b>{pct(call.coverage.completeness)}</b>
              <small>shots we have</small>
            </span>

            <span className="topstat">
              <b>{clock(call.day.hard_stop)}</b>
              <small>latest we can finish</small>
            </span>

            {call.coverage.exposure_usd > 0 && (
              <span className="topstat">
                <b style={{ color: "var(--warn)" }}>
                  {usd(call.coverage.exposure_usd)}
                </b>
                <small>at risk if we move on</small>
              </span>
            )}
          </>
        ) : (
          <span className="topstat">
            <small>working out where we are…</small>
          </span>
        )}
      </div>

      {call && (
        <details className="why">
          <summary>Why</summary>
          <p>{call.spoken || call.summary}</p>
        </details>
      )}

      {/* footage in, or thrown away */}
      <SectionTitle icon="footage">Footage</SectionTitle>
      <Intake
        sceneId={sceneId}
        sceneName={scene ? `scene ${scene.number} · ${scene.place}` : ""}
        onStarted={(id) => {
          setReloadKey((n) => n + 1);
          onIngested(id);
        }}
        onCleared={() => {
          setReloadKey((n) => n + 1);
          onChanged();
        }}
      />

      {call && call.options.filter((o) => o.worth_it).length > 0 && (
        <section style={{ marginTop: 28 }}>
          <SectionTitle icon="grab">Worth grabbing before we move</SectionTitle>
          <div className="grabs">
            {groupByPerson(call.options.filter((o) => o.worth_it)).map((g) => (
              <div className="grab" key={g.person}>
                <div className="grab-head">
                  <b>{g.person}</b>
                  <span>saves {usd(g.saving)}</span>
                </div>
                <ul>
                  {g.shots.map((o, i) => (
                    <li key={i}>
                      <span className="grab-shot">{o.shot_type}</span>
                      <span className="grab-cost">
                        {usd(o.shoot_now_usd)} now · {usd(o.recover_later_usd)} later
                      </span>
                    </li>
                  ))}
                </ul>
                <small>
                  odds drop to {pct(Math.min(...g.shots.map((s) => s.p_make_day_after)))} if
                  you shoot {g.shots.length > 1 ? "them all" : "it"}
                </small>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* pick a scene */}
      <div style={{ marginTop: 28 }}>
        <SectionTitle icon="scenes">Scenes today</SectionTitle>
      </div>
      <SceneBar selected={sceneId} onSelect={onScene} reloadKey={reloadKey}
                fresh={fresh} />

      {/* and the breakdown of the one that's open */}
      {sceneId && (
        <WorldPanel
          sceneId={sceneId}
          onRechecked={(id) => {
            setReloadKey((n) => n + 1);
            onIngested(id);
          }}
        />
      )}

      {sceneId && <Problems sceneId={sceneId} reloadKey={reloadKey} />}

      {sceneId && (
        <CastMatrix
          sceneId={sceneId}
          sceneName={scene ? `scene ${scene.number} · ${scene.place}` : ""}
          reloadKey={reloadKey}
          onChanged={onChanged}
        />
      )}
    </>
  );
}
