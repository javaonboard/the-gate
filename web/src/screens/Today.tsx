import { useState } from "react";
import type { GateCall, Scene } from "../api";
import { pct, usd } from "../api";
import { Breakdown } from "../components/Breakdown";
import { CastMatrix } from "../components/CastMatrix";
import { DayLine } from "../components/DayLine";
import { Intake } from "../components/Intake";
import { SectionTitle } from "../components/SectionTitle";

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

export function Today({ call, scene, dataKey, fresh, onScene,
                       onIngested, onChanged, onCleared }: {
  call: GateCall | null;
  scene: Scene | null;
  dataKey: number;
  fresh: string[];
  onScene: (s: Scene) => void;
  onIngested: (runId: string) => void;
  onChanged: () => void;
  onCleared: () => void;
}) {
  const [bumped, setBumped] = useState(0);
  const [footage, setFootage] = useState(false);
  const reloadKey = bumped + dataKey;
  const setReloadKey = (fn: (n: number) => number) => setBumped(fn);
  const sceneId = scene?.scene_id ?? "";

  // One computation, two questions. The gate covers the whole day, so the
  // verdict and the money at the top are the day's. What is worth grabbing
  // is not: it is what this scene is short of, because that is what the crew
  // is standing in front of.
  const worthIt = (call?.options ?? []).filter((o) => o.worth_it);
  const here = sceneId
    ? worthIt.filter((o) => o.scene_id === sceneId)
    : worthIt;
  const elsewhere = sceneId
    ? worthIt.filter((o) => o.scene_id !== sceneId)
    : [];

  return (
    <>
      {/* the day's call, with the scenes it is made of */}
      <DayLine
        fresh={fresh}
        onFootage={() => setFootage(true)}
        reloadKey={reloadKey}
        call={call}
        openScene={sceneId}
        onRechecked={(id) => {
          setReloadKey((n) => n + 1);
          onIngested(id);
        }}
        onChanged={() => {
          setReloadKey((n) => n + 1);
          onChanged();
        }}
        onScene={(id) => {
          void fetch("/api/scenes")
            .then((r) => r.json())
            .then((rows: Scene[]) => {
              const found = rows.find((s) => s.scene_id === id);
              if (found) onScene(found);
            });
        }}
      />

      {footage && (
        <Intake
          sceneId={sceneId}
          sceneName={scene ? `scene ${scene.number} · ${scene.place}` : ""}
          onClose={() => setFootage(false)}
          onStarted={(id) => {
            setFootage(false);
            setReloadKey((n) => n + 1);
            onIngested(id);
          }}
          onCleared={() => {
            setFootage(false);
            setReloadKey((n) => n + 1);
            onCleared();
          }}
        />
      )}

      {call && worthIt.length > 0 && (
        <section style={{ marginTop: 28 }}>
          <SectionTitle icon="grab">Worth grabbing before we move</SectionTitle>

          {/* Before we move means before we leave this scene. The camera is
              already up, the cast is dressed and on the floor, and that is
              what makes these cheap. A missing shot somewhere else is not
              something you grab before the move — going back for it IS the
              move. So the boxes are this scene, and the rest is a footnote. */}
          <p className="grabs-lead">
            {!scene ? "across the day"
              : here.length ? <>in <b>{scene.place}</b>, while the camera is still up</>
              : <>nothing outstanding in <b>{scene.place}</b></>}
          </p>

          <div className="grabs">
            {groupByPerson(here).map((g) => (
              <div className="grab" key={g.person}>
                <div className="grab-head">
                  <b>{g.person}</b>
                  <span>saves {usd(g.saving)}</span>
                </div>
                <ul>
                  {g.shots.map((o, i) => (
                    <li key={i}>
                      <span className="grab-shot">
                        {o.shot_type}
                        {!scene && o.place && (
                          <em className="grab-where">in {o.place}</em>
                        )}
                      </span>
                      <span className="grab-cost">
                        {o.minutes ? `${o.minutes} min · ` : ""}
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

          {elsewhere.length > 0 && (
            <p className="grabs-rest">
              {elsewhere.length} {here.length ? "more " : ""}worth grabbing
              elsewhere today, saving{" "}
              {usd(elsewhere.reduce((n, o) => n + o.saving_usd, 0))} — but that
              means going back, which is the move you are deciding about.
            </p>
          )}
        </section>
      )}

      {sceneId && (
        <Breakdown
          sceneId={sceneId}
          reloadKey={reloadKey}
          call={call}
          // Joining two takes changes the coverage, so everything that reads
          // from it has to be told, not just this panel.
          onJoined={() => { setReloadKey((n) => n + 1); onChanged(); }}
        />
      )}

      {sceneId && (
        <CastMatrix
          sceneId={sceneId}
          sceneName={scene ? `scene ${scene.number} · ${scene.place}` : ""}
          reloadKey={reloadKey}
          onChanged={() => {
            // changing what a scene needs changes what is missing, and the
            // breakdown above is showing exactly that
            setReloadKey((n) => n + 1);
            onChanged();
          }}
        />
      )}
    </>
  );
}
