import { useState } from "react";
import type { GateCall } from "../api";
import { clock, pct, usd } from "../api";
import { CastMatrix } from "../components/CastMatrix";
import { DropZone } from "../components/DropZone";
import { SceneBar, type Scene } from "../components/SceneBar";

function oddsColour(p: number) {
  if (p >= 0.7) return "var(--go)";
  if (p >= 0.4) return "var(--warn)";
  return "var(--nogo)";
}

export function Today({ call, scene, onScene, onIngested, onChanged }: {
  call: GateCall | null;
  scene: Scene | null;
  onScene: (s: Scene) => void;
  onIngested: (runId: string) => void;
  onChanged: () => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
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

      {call && <p className="spoken">{call.spoken || call.summary}</p>}

      {/* footage in */}
      <DropZone
        sceneId={sceneId}
        setupId=""
        sceneName={scene ? `scene ${scene.number} · ${scene.place}` : ""}
        onIngested={(id) => {
          setReloadKey((n) => n + 1);
          onIngested(id);
        }}
      />

      {call && call.options.filter((o) => o.worth_it).length > 0 && (
        <section style={{ marginTop: 26 }}>
          <h3 className="section-title">Worth grabbing before we move</h3>
          {call.options
            .filter((o) => o.worth_it)
            .map((o, i) => (
              <div className="option" key={i} data-worth={o.worth_it}>
                <div className="head">
                  <b>
                    {o.shot_type} {o.subject !== "-" ? o.subject : ""}
                  </b>
                  <span
                    style={{ color: "var(--go)", fontFamily: "var(--mono)" }}
                  >
                    saves {usd(o.saving_usd)}
                  </span>
                </div>
                <div className="prices">
                  <div>
                    <span>shoot now</span>
                    {usd(o.shoot_now_usd)}
                  </div>
                  <div>
                    <span>pick up later</span>
                    {usd(o.recover_later_usd)}
                  </div>
                  <div>
                    <span>odds after</span>
                    {pct(o.p_make_day_after)}
                  </div>
                </div>
                <div className="verdict-line">{o.verdict}</div>
              </div>
            ))}
        </section>
      )}

      {/* pick a scene */}
      <h3 className="section-title" style={{ marginTop: 26 }}>
        Scenes today
      </h3>
      <SceneBar selected={sceneId} onSelect={onScene} reloadKey={reloadKey} />

      {/* and the breakdown of the one that's open */}
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
