import { useEffect, useState } from "react";
import type { AgentEvent } from "../api";

/** One line saying what the system is doing.
 *
 *  Not a log and not a chat — the same thing a browser shows while a page
 *  loads. It appears while work is happening and clears when it stops. */
export function ActivityLine({ events, busy }: {
  events: AgentEvent[];
  busy: boolean;
}) {
  const [done, setDone] = useState<string | null>(null);

  const latest = [...events]
    .reverse()
    .find((e) => e.phase === "working" || e.phase === "tool_call");

  useEffect(() => {
    if (busy) {
      setDone(null);
      return;
    }
    if (!events.length) return;
    const took = events[events.length - 1]?.elapsed_ms;
    setDone(took != null ? `checked in ${(took / 1000).toFixed(1)}s` : "done");
    const t = setTimeout(() => setDone(null), 4000);
    return () => clearTimeout(t);
  }, [busy, events]);

  if (!busy && !done) return null;

  return (
    <div className="activity" data-busy={busy}>
      {busy ? <span className="spinner" /> : <span className="tick">✓</span>}
      <span>
        {busy && latest ? (
          <>
            <b>{latest.agent_name}</b> {lower(latest.message)}
          </>
        ) : busy ? (
          "starting…"
        ) : (
          done
        )}
      </span>
    </div>
  );
}

/** Messages are written as sentences; mid-line they read better lowercase. */
function lower(message: string) {
  return message.charAt(0).toLowerCase() + message.slice(1);
}
