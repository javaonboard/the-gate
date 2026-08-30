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

      {sceneId && (
        <Breakdown sceneId={sceneId} reloadKey={reloadKey} call={call} />
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
