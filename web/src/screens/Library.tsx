import { useEffect, useState } from "react";
import { api, type Dp } from "../api";

type Condition = {
  interior_exterior: string;
  time_of_day: string;
  scene_type: string;
  extras_bucket: number;
  setups: number;
  median_minutes: number;
  p90_minutes: number;
};

type DpDetail = {
  id: string;
  name: string;
  role: string;
  conditions: Condition[];
  productions: { title: string; kind: string; setups: number }[];
};

const WHERE: Record<string, string> = { INT: "inside", EXT: "outside" };
const WHEN: Record<string, string> = {
  DAY: "daytime", NIGHT: "at night", DUSK: "at dusk", DAWN: "at dawn",
};
const CROWD = ["no extras", "a few extras", "a dozen extras", "a crowd", "a big crowd"];

/** Everyone the studio has worked with, and how they actually shoot.
 *
 *  This is the four years of history made visible — the thing that lets the
 *  system say anything useful on a crew's first day. */
export function Library() {
  const [dps, setDps] = useState<Dp[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<DpDetail | null>(null);
  const [filter, setFilter] = useState<"all" | "EXT" | "INT">("all");

  useEffect(() => {
    void api.dps().then((rows) => {
      setDps(rows);
      if (rows.length) setSelected(rows[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selected) return;
    void fetch(`/api/library/dps/${selected}`)
      .then((r) => r.json())
      .then(setDetail);
  }, [selected]);

  const conditions = (detail?.conditions ?? [])
    .filter((c) => filter === "all" || c.interior_exterior === filter)
    .slice(0, 24);

  const slowest = Math.max(1, ...conditions.map((c) => c.p90_minutes));

  return (
    <>
      <h2 style={{ margin: "0 0 4px", fontSize: 22 }}>Who we've worked with</h2>
      <p style={{ color: "var(--muted)", marginTop: 0, fontSize: 14, maxWidth: "62ch" }}>
        Every camera position from eighteen productions over four years. This is
        what the estimates are built on — not a guess about film crews in general,
        but what these people actually did.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16, marginTop: 20 }}>
        <div>
          {dps.map((d) => (
            <button
              key={d.id}
              onClick={() => setSelected(d.id)}
              style={{
                display: "block", width: "100%", textAlign: "left",
                background: selected === d.id ? "var(--panel-2)" : "var(--panel)",
                border: `1px solid ${selected === d.id ? "var(--accent)" : "var(--line)"}`,
                borderRadius: 10, padding: "12px 14px", marginBottom: 8,
                color: "var(--text)", font: "inherit", cursor: "pointer",
              }}
            >
              <div style={{ fontSize: 15, fontWeight: 600 }}>{d.name}</div>
              <div style={{ fontSize: 12, color: "var(--faint)" }}>{d.role}</div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
                {d.setups.toLocaleString()} setups · {d.median_minutes} min typical
              </div>
            </button>
          ))}
        </div>

        <div>
          {detail && (
            <>
              <div className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 600 }}>{detail.name}</div>
                    <div style={{ fontSize: 13, color: "var(--faint)" }}>{detail.role}</div>
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    {(["all", "INT", "EXT"] as const).map((f) => (
                      <button
                        key={f}
                        onClick={() => setFilter(f)}
                        style={{
                          background: filter === f ? "var(--panel-2)" : "none",
                          border: "1px solid var(--line)", borderRadius: 6,
                          color: filter === f ? "var(--text)" : "var(--muted)",
                          font: "inherit", fontSize: 12, padding: "5px 10px",
                          cursor: "pointer",
                        }}
                      >
                        {f === "all" ? "everywhere" : WHERE[f]}
                      </button>
                    ))}
                  </div>
                </div>

                <div style={{ marginTop: 18 }}>
                  {conditions.map((c, i) => (
                    <div key={i} style={{ marginBottom: 12 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                        <span>
                          {WHERE[c.interior_exterior]} {WHEN[c.time_of_day]},{" "}
                          {c.scene_type}
                          <span style={{ color: "var(--faint)" }}>
                            {" "}· {CROWD[c.extras_bucket] ?? "extras"}
                          </span>
                        </span>
                        <span style={{ fontFamily: "var(--mono)", color: "var(--muted)" }}>
                          {c.median_minutes} min · {c.p90_minutes} on a bad day
                        </span>
                      </div>
                      <div className="meter" style={{ marginTop: 5 }}>
                        <div
                          style={{
                            width: `${(c.median_minutes / slowest) * 100}%`,
                            background: "var(--accent)",
                          }}
                        />
                      </div>
                      <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>
                        seen {c.setups.toLocaleString()} times
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card" style={{ marginTop: 14 }}>
                <h3>Films and seasons</h3>
                {detail.productions.map((p) => (
                  <div
                    key={p.title}
                    style={{
                      display: "flex", justifyContent: "space-between",
                      padding: "7px 0", borderBottom: "1px solid var(--line)",
                      fontSize: 14,
                    }}
                  >
                    <span>
                      {p.title}{" "}
                      <span style={{ color: "var(--faint)", fontSize: 12 }}>{p.kind}</span>
                    </span>
                    <span style={{ fontFamily: "var(--mono)", color: "var(--muted)", fontSize: 13 }}>
                      {p.setups.toLocaleString()} setups
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
