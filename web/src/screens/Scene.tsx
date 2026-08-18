import { useEffect, useState } from "react";
import { usd } from "../api";

type Req = {
  req_id: string;
  shot_type: string;
  subject: string;
  priority: number;
  recover_cost_usd: number;
  plain: string;
};

type Take = {
  take_id: string;
  setup_id: string;
  shot_size: string;
  shot_size_plain: string;
  movement_plain: string;
  subjects: string[];
  focus_score: number;
  faults: string[];
  duration_s: number;
  circled: boolean;
};

type ShotType = { value: string; label: string };

/** What this scene needs, and what came off the card.
 *
 *  The list starts from the script but the scene is the AD's, not ours — so
 *  everything here can be added to and taken away. Changing it changes the
 *  call. */
export function Scene({ sceneId, onChanged }: {
  sceneId: string;
  onChanged: () => void;
}) {
  const [reqs, setReqs] = useState<Req[]>([]);
  const [types, setTypes] = useState<ShotType[]>([]);
  const [takes, setTakes] = useState<Take[]>([]);
  const [shotType, setShotType] = useState("single");
  const [subject, setSubject] = useState("");
  const [cost, setCost] = useState(20000);
  const [priority, setPriority] = useState(1);

  async function load() {
    const r = await fetch(`/api/scenes/${sceneId}/requirements`).then((x) => x.json());
    setReqs(r.requirements);
    setTypes(r.shot_types);
    setTakes(await fetch(`/api/scenes/${sceneId}/takes`).then((x) => x.json()));
  }

  useEffect(() => { void load(); }, [sceneId]);

  async function add() {
    await fetch(`/api/scenes/${sceneId}/requirements`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        shot_type: shotType,
        subject: subject.trim() || "-",
        priority,
        recover_cost_usd: cost,
      }),
    });
    setSubject("");
    await load();
    onChanged();
  }

  async function remove(reqId: string) {
    await fetch(`/api/scenes/${sceneId}/requirements/${reqId}`, { method: "DELETE" });
    await load();
    onChanged();
  }

  const bySetup = takes.reduce<Record<string, Take[]>>((acc, t) => {
    (acc[t.setup_id] ??= []).push(t);
    return acc;
  }, {});

  return (
    <>
      <h2 style={{ margin: "0 0 4px", fontSize: 22 }}>What this scene needs</h2>
      <p style={{ color: "var(--muted)", marginTop: 0, fontSize: 14, maxWidth: "62ch" }}>
        Worked out from the script as a starting point. Add anything the director
        wants, remove what you're not covering. The call updates when you do.
      </p>

      <div className="card" style={{ padding: "4px 0", marginTop: 18 }}>
        {reqs.map((r) => (
          <div className="req" key={r.req_id} data-state="gap">
            <div className="tick" style={{ color: "var(--faint)" }}>
              {r.priority === 1 ? "!" : "·"}
            </div>
            <div className="what">
              <b>{r.shot_type}{r.subject !== "-" ? ` · ${r.subject}` : ""}</b>
              <small>{r.plain}</small>
            </div>
            <div className="cost">{usd(r.recover_cost_usd)} to redo</div>
            <button
              onClick={() => remove(r.req_id)}
              style={{
                background: "none", border: 0, color: "var(--faint)",
                cursor: "pointer", fontSize: 16, padding: "0 4px",
              }}
              title="Remove"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ fontSize: 12, color: "var(--faint)" }}>
          shot
          <br />
          <select
            value={shotType}
            onChange={(e) => setShotType(e.target.value)}
            style={selectStyle}
          >
            {types.map((t) => (
              <option key={t.value} value={t.value}>{t.value} — {t.label}</option>
            ))}
          </select>
        </label>

        <label style={{ fontSize: 12, color: "var(--faint)" }}>
          of whom or what
          <br />
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="actor_3"
            style={selectStyle}
          />
        </label>

        <label style={{ fontSize: 12, color: "var(--faint)" }}>
          must have?
          <br />
          <select
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
            style={selectStyle}
          >
            <option value={1}>can't cut without it</option>
            <option value={2}>would like it</option>
            <option value={3}>nice to have</option>
          </select>
        </label>

        <label style={{ fontSize: 12, color: "var(--faint)" }}>
          cost to get later
          <br />
          <input
            type="number"
            value={cost}
            step={1000}
            onChange={(e) => setCost(Number(e.target.value))}
            style={{ ...selectStyle, width: 120 }}
          />
        </label>

        <button className="primary" onClick={add}>Add</button>
      </div>

      <h2 style={{ margin: "32px 0 4px", fontSize: 22 }}>What came off the card</h2>
      <p style={{ color: "var(--muted)", marginTop: 0, fontSize: 14 }}>
        {takes.length} takes across {Object.keys(bySetup).length} camera positions,
        as the Script Supervisor logged them.
      </p>

      {Object.entries(bySetup).map(([setupId, group]) => (
        <div className="card" key={setupId} style={{ marginTop: 12 }}>
          <h3>{setupId.split("_").slice(-1)[0]} · {group[0].shot_size_plain} · {group[0].movement_plain}</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {group.map((t) => (
              <div
                key={t.take_id}
                title={t.faults.join(", ") || "clean"}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                  padding: "6px 10px",
                  borderRadius: 7,
                  background: "var(--panel-2)",
                  border: `1px solid ${t.circled ? "var(--go)" : "var(--line)"}`,
                  color: t.faults.length ? "var(--warn)" : "var(--text)",
                }}
              >
                {t.take_id.split("_").slice(-1)[0]}
                <span style={{ color: "var(--faint)" }}> · {t.duration_s.toFixed(1)}s</span>
                {t.faults.length > 0 && <span> ⚠</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

const selectStyle: React.CSSProperties = {
  background: "var(--panel-2)",
  color: "var(--text)",
  border: "1px solid var(--line)",
  borderRadius: 7,
  padding: "7px 9px",
  font: "inherit",
  fontSize: 13,
  marginTop: 4,
};
