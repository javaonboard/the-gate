import { useEffect, useState } from "react";
import { ActivityLine } from "./components/ActivityLine";
import type { Scene } from "./components/SceneBar";
import { Today } from "./screens/Today";
import { useRun } from "./useRun";

const FIRST_SCENE = "prod_now_sc001";

export default function App() {
  const [scene, setScene] = useState<Scene | null>(null);
  const [hoursIn, setHoursIn] = useState(9);
  const [useAgent, setUseAgent] = useState(true);
  const sceneId = scene?.scene_id ?? FIRST_SCENE;
  const { events, call, busy, error, start, follow } = useRun(sceneId);

  useEffect(() => {
    void start(hoursIn, useAgent);
    // first call only; after that the AD asks
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const shootClock = new Date(2026, 7, 16, 7, 0);
  shootClock.setMinutes(shootClock.getMinutes() + hoursIn * 60);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="wordmark">
          THE GATE
          <span>
            {scene ? ` · scene ${scene.number} · ${scene.place}` : ""}
          </span>
        </div>

        <div className="right">
          <div className="controls">
            <span>
              {shootClock.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <input
              type="range"
              min={7}
              max={12}
              step={0.5}
              value={hoursIn}
              onChange={(e) => setHoursIn(Number(e.target.value))}
              title="How far into the shoot day"
            />
          </div>

          <label
            className="toggle"
            title="Turn the language models off and show only the computed call"
          >
            <input
              type="checkbox"
              checked={useAgent}
              onChange={(e) => setUseAgent(e.target.checked)}
            />
            crew speaks
          </label>

          <button
            className="primary"
            onClick={() => start(hoursIn, useAgent)}
            disabled={busy}
          >
            {busy ? "Checking…" : "Check the gate"}
          </button>
        </div>
      </header>

      <ActivityLine events={events} busy={busy} />

      <main>
        {error && (
          <div className="empty" style={{ color: "var(--nogo)" }}>
            {error} — is the backend running on :8080?
          </div>
        )}

        <Today
          call={call}
          scene={scene}
          onScene={(s) => {
            setScene(s);
            void start(hoursIn, useAgent, s.scene_id);
          }}
          onIngested={(id) => follow(id)}
          onChanged={() => start(hoursIn, useAgent)}
        />
      </main>

    </div>
  );
}
