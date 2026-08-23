import { useEffect, useRef, useState } from "react";
import { ActivityLine } from "./components/ActivityLine";
import { DayBar } from "./components/DayBar";
import type { Scene } from "./components/SceneBar";
import { Today } from "./screens/Today";
import { useRun } from "./useRun";

const FIRST_SCENE = "prod_now_sc001";

export default function App() {
  const [scene, setScene] = useState<Scene | null>(null);
  const [hoursIn, setHoursIn] = useState(9);
  const [useAgent, setUseAgent] = useState(true);
  const sceneId = scene?.scene_id ?? FIRST_SCENE;
  const { events, call, busy, error, touched, start, follow, reset } =
    useRun(sceneId);
  const [dataKey, setDataKey] = useState(0);
  const wasBusy = useRef(false);

  // A run that finishes in the background has usually changed the scenes, the
  // cast and the problems. Nothing on screen knows that unless it is told.
  useEffect(() => {
    if (wasBusy.current && !busy) setDataKey((n) => n + 1);
    wasBusy.current = busy;
  }, [busy]);

  // Footage that has just been taken in is the thing you want to look at, so
  // open the scene it landed in rather than leaving the old one selected.
  useEffect(() => {
    if (!touched.length || busy) return;
    const landed = touched[0];
    if (scene?.scene_id === landed) return;
    void fetch("/api/scenes")
      .then((r) => r.json())
      .then((rows: Scene[]) => {
        const found = rows.find((s) => s.scene_id === landed);
        if (found) {
          setScene(found);
          void start(hoursIn, useAgent, found.scene_id);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touched, busy]);

  const [ready, setReady] = useState(false);

  // Settle the workspace first. Firing everything at once gave each request
  // its own workspace, and the last cookie set won.
  useEffect(() => {
    void fetch("/api/session")
      .then(() => setReady(true))
      .catch(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    void start(hoursIn, useAgent);
    // first call only; after that the AD asks
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

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
          <DayBar hoursIn={hoursIn} onHoursIn={setHoursIn} />

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
        {!ready && <div className="empty">Starting up…</div>}

        {error && (
          <div className="empty" style={{ color: "var(--nogo)" }}>
            {error} — is the backend running on :8080?
          </div>
        )}

        {ready && <Today
          call={call}
          scene={scene}
          dataKey={dataKey}
          fresh={touched}
          onScene={(s) => {
            setScene(s);
            void start(hoursIn, useAgent, s.scene_id);
          }}
          onIngested={(id) => follow(id)}
          onChanged={() => start(hoursIn, useAgent)}
          onCleared={() => {
            // The data is gone; anything still selected refers to rows that
            // no longer exist, and re-rendering it looks like the reset failed.
            setScene(null);
            reset();
            setDataKey((n) => n + 1);
          }}
        />}
      </main>

    </div>
  );
}
