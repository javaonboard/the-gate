import { useEffect, useState } from "react";
import { StatusRail } from "./components/StatusRail";
import { Library } from "./screens/Library";
import { Lineage } from "./screens/Lineage";
import { Today } from "./screens/Today";
import { useRun } from "./useRun";

const SCENE = "prod_now_sc001";
const SCREENS = ["Today", "Library", "Lineage"] as const;
type Screen = (typeof SCREENS)[number];

export default function App() {
  const [screen, setScreen] = useState<Screen>("Today");
  const [hoursIn, setHoursIn] = useState(9);
  const [useAgent, setUseAgent] = useState(true);
  const { events, call, busy, error, start, follow } = useRun(SCENE);

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
          THE GATE <span>· canal street</span>
        </div>

        <nav>
          {SCREENS.map((s) => (
            <button
              key={s}
              data-active={screen === s}
              onClick={() => setScreen(s)}
            >
              {s}
            </button>
          ))}
        </nav>

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

      <main>
        {error && (
          <div className="empty" style={{ color: "var(--nogo)" }}>
            {error} — is the backend running on :8080?
          </div>
        )}

        {screen === "Today" && (
          <Today
            call={call}
            sceneId={SCENE}
            onIngested={(id) => follow(id)}
            onChanged={() => start(hoursIn, useAgent)}
          />
        )}
        {screen === "Library" && <Library />}
        {screen === "Lineage" && <Lineage events={events} />}
      </main>

      <StatusRail events={events} busy={busy} />
    </div>
  );
}
