import { useEffect, useRef, useState } from "react";
import { ActivityLine } from "./components/ActivityLine";
import { CrewGraph } from "./components/CrewGraph";
import { Choose } from "./screens/Choose";
import { DayBar } from "./components/DayBar";
import type { Scene } from "./api";
import { Today } from "./screens/Today";
import { useRun } from "./useRun";

export default function App() {
  const [scene, setScene] = useState<Scene | null>(null);
  const [hoursIn, setHoursIn] = useState(9);
  const [useAgent, setUseAgent] = useState(true);
  const sceneId = scene?.scene_id ?? "";
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
  //
  // Once. This used to re-fire every time a check finished, which meant that
  // opening any other scene started a check, the check ending re-ran this, and
  // it dragged you back to the scene the last upload landed in. You could not
  // stay on scene 2.
  const followed = useRef("");
  useEffect(() => {
    if (!touched.length || busy) return;
    const key = touched.join(",");
    if (followed.current === key) return;
    followed.current = key;

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

  // Nothing is assigned. Until a shoot day is chosen, the chooser is the
  // whole interface — which also means there is never a doubt about which
  // workspace an upload landed in.
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [workspaceLabel, setWorkspaceLabel] = useState("");
  const [checked, setChecked] = useState(false);
  const [watching, setWatching] = useState(false);

  useEffect(() => {
    void fetch("/api/session")
      .then((r) => r.json())
      .then((s: { workspace: string | null; label?: string }) => {
        setWorkspaceLabel(s.label ?? "");
        setWorkspace(s.workspace);
      })
      .catch(() => setWorkspace(null))
      .finally(() => setChecked(true));
  }, []);

  useEffect(() => {
    if (!workspace) return;
    void fetch("/api/scenes")
      .then((r) => r.json())
      .then((rows: Scene[]) => {
        const shot = rows.find((s) => s.place !== "nothing yet") ?? rows[0];
        if (!shot) return;
        setScene(shot);
        void start(hoursIn, useAgent, shot.scene_id);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  async function leave() {
    await fetch("/api/workspaces/leave", { method: "POST" });
    setWorkspace(null);
    setScene(null);
    followed.current = "";
    reset();
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="wordmark">
          THE GATE
          <span>
            {workspaceLabel ? ` · ${workspaceLabel}` : ""}
            {scene ? ` · scene ${scene.number} · ${scene.place}` : ""}
          </span>
        </div>

        <div className="right">
          <DayBar
            hoursIn={hoursIn}
            onHoursIn={setHoursIn}
            // Let go of the slider and the numbers redo themselves. Without
            // the crew: the odds, the wrap, the penalties and what is worth
            // grabbing are all computed, so moving through the afternoon
            // costs nothing. The button is still how you ask them to explain.
            onCommit={(h) => { if (!busy) void start(h, false); }}
          />

          <label
            className="toggle"
            title="Off: the same call and the same numbers, written by the
                   computation instead of the crew. Instant, and it still works
                   if the models are unreachable."
          >
            <input
              type="checkbox"
              checked={useAgent}
              onChange={(e) => setUseAgent(e.target.checked)}
            />
            Crew explains the call
          </label>

          <button
            className="primary"
            onClick={() => start(hoursIn, useAgent)}
            disabled={busy}
          >
            {busy ? "Checking…" : "Check the gate"}
          </button>

          <button
            className="secondary watch"
            data-live={busy}
            onClick={() => setWatching(true)}
            title="Watch the crew work — which agent is running, what it is using, and how long each one takes"
          >
            {busy && <span className="watch-dot" aria-hidden="true" />}
            The crew
          </button>

          <button
            className="secondary"
            onClick={leave}
            title="Close this shoot day and pick another"
          >
            Change day
          </button>
        </div>
      </header>

      {watching && (
        <CrewGraph events={events} busy={busy}
                   onClose={() => setWatching(false)} />
      )}

      <ActivityLine events={events} busy={busy} />

      <main>
        {!checked && <div className="empty">Looking for your work…</div>}

        {checked && !workspace && (
          <Choose
            onChosen={(id, label) => {
              setWorkspaceLabel(label);
              setWorkspace(id);
            }}
          />
        )}

        {error && (
          <div className="empty" style={{ color: "var(--nogo)" }}>
            {/* The port only means anything at a desk. Deployed, this told
                people to go and check a server that was not the one they
                were talking to. */}
            {error}
          </div>
        )}

        {workspace && <Today
          call={call}
          scene={scene}
          dataKey={dataKey}
          fresh={touched}
          onScene={(s) => {
            // Opening a scene is looking, not asking. Running the whole gate
            // on a click meant every glance cost a model call and reset the
            // panel the AD was in the middle of using — and the call at the
            // top is the day's now, so it does not change with the scene.
            setScene(s);
          }}
          onIngested={(id) => follow(id)}
          // Everything that changes the day comes through here: a shot opted
          // out of a scene, two takes joined, a take moved, two faces merged,
          // the crew resized, the world set. All of them change what the day
          // is short of and what it costs, so all of them redo the numbers.
          //
          // Without the crew: the verdict, the odds, the wrap and the money
          // are computed, so this is a couple of seconds and no model calls.
          // The panels alone used to reload and the call above them stayed as
          // it was, so a shot you had just written off still read as missing.
          onChanged={() => {
            setDataKey((n) => n + 1);
            if (!busy) void start(hoursIn, false);
          }}
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
