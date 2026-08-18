import { useCallback, useEffect, useState } from "react";
import { api, type GateCall } from "./api";
import { StatusRail } from "./components/StatusRail";
import { Library } from "./screens/Library";
import { Lineage } from "./screens/Lineage";
import { Scene } from "./screens/Scene";
import { Today } from "./screens/Today";

const SCENE = "prod_now_sc001";
const SCREENS = ["Today", "Scene", "Library", "Lineage"] as const;
type Screen = (typeof SCREENS)[number];

export default function App() {
  const [screen, setScreen] = useState<Screen>("Today");
  const [call, setCall] = useState<GateCall | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [hoursIn, setHoursIn] = useState(9);
  const [useAgent, setUseAgent] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.gate(SCENE, hoursIn, useAgent);
      setCall(result);
      setRunId(result.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [hoursIn, useAgent]);

  useEffect(() => {
    void check();
    // first call only; after that the AD asks
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const shootDayClock = new Date(2026, 7, 16, 7, 0);
  shootDayClock.setHours(shootDayClock.getHours() + Math.floor(hoursIn));
  shootDayClock.setMinutes((hoursIn % 1) * 60);

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
              {shootDayClock.toLocaleTimeString([], {
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

          <label className="toggle" title="Turn off the language models and show only the computed call">
            <input
              type="checkbox"
              checked={useAgent}
              onChange={(e) => setUseAgent(e.target.checked)}
            />
            crew speaks
          </label>

          <button className="primary" onClick={check} disabled={busy}>
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

        {screen === "Today" && <Today call={call} />}
        {screen === "Scene" && <Scene sceneId={SCENE} onChanged={check} />}
        {screen === "Library" && <Library />}
        {screen === "Lineage" && <Lineage runId={runId} />}
      </main>

      <StatusRail runId={runId} />
    </div>
  );
}
