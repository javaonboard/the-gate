import { useEffect, useState } from "react";
import type { AgentEvent } from "../api";

/** What the crew is doing, right now.
 *
 *  Only live work is shown. A job appears when it starts, updates while it
 *  runs, and clears a couple of seconds after it finishes — the way a call
 *  sheet gets ticked off rather than a log that grows forever. */

type Job = {
  agent: string;
  name: string;
  role: string;
  message: string;
  state: "working" | "done" | "error";
  since: number;
  tookMs?: number;
};

const LINGER_MS = 2600;

export function StatusRail({ events, busy }: {
  events: AgentEvent[];
  busy: boolean;
}) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [, force] = useState(0);

  useEffect(() => {
    if (!events.length) {
      setJobs({});
      return;
    }
    const e = events[events.length - 1];
    if (e.phase === "result") return;

    setJobs((prev) => {
      const existing = prev[e.agent];
      const next: Job = {
        agent: e.agent,
        name: e.agent_name,
        role: e.agent_role,
        message: e.message,
        state:
          e.phase === "done" ? "done" : e.phase === "error" ? "error" : "working",
        since: e.phase === "done" || e.phase === "error" ? Date.now() : existing?.since ?? Date.now(),
        tookMs:
          typeof e.data?.took_ms === "number"
            ? (e.data.took_ms as number)
            : existing?.tookMs,
      };
      return { ...prev, [e.agent]: next };
    });
  }, [events]);

  // clear finished jobs after they have been seen
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 400);
    return () => clearInterval(t);
  }, []);

  const now = Date.now();
  const visible = Object.values(jobs)
    .filter((j) => j.state === "working" || now - j.since < LINGER_MS)
    .sort((a, b) => b.since - a.since);

  return (
    <aside className="rail">
      <h2>Agent crew</h2>

      {visible.length === 0 && (
        <div className="empty">{busy ? "Starting…" : "Standing by."}</div>
      )}

      {visible.map((j) => {
        const fading = j.state !== "working" && now - j.since > LINGER_MS - 900;
        return (
          <div
            className="job"
            key={j.agent}
            data-state={j.state}
            style={{ opacity: fading ? 0.25 : 1 }}
          >
            <div className="job-dot">
              {j.state === "working" ? (
                <span className="spinner" />
              ) : j.state === "error" ? (
                "✕"
              ) : (
                "✓"
              )}
            </div>
            <div>
              <div className="who">
                <b>{j.name}</b>
                <small>{j.role}</small>
                {j.state !== "working" && j.tookMs != null && (
                  <span className="took">
                    {(j.tookMs / 1000).toFixed(1)}s
                  </span>
                )}
              </div>
              <div className="msg">{j.message}</div>
            </div>
          </div>
        );
      })}
    </aside>
  );
}
