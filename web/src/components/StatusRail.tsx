import { useEffect, useRef, useState } from "react";
import type { AgentEvent } from "../api";

const DOT: Record<AgentEvent["phase"], string> = {
  started: "·",
  working: "▸",
  tool_call: "→",
  tool_result: "←",
  done: "✓",
  error: "✕",
};

/** Live crew activity over Server-Sent Events.
 *
 *  The browser opens one connection and the backend pushes down it as each
 *  agent works. Not a chat — a status board, the way a production office
 *  would run one. */
export function StatusRail({ runId }: { runId: string | null }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);

    const source = new EventSource(`/api/runs/${runId}/stream`);
    source.addEventListener("agent", (e) => {
      const event = JSON.parse((e as MessageEvent).data) as AgentEvent;
      setEvents((prev) =>
        prev.some((p) => p.seq === event.seq) ? prev : [...prev, event]
      );
    });
    source.onerror = () => source.close();

    return () => source.close();
  }, [runId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  return (
    <aside className="rail">
      <h2>Crew</h2>

      {events.length === 0 && (
        <div className="empty">Nothing running.</div>
      )}

      {events.map((e) => (
        <div className="event" key={e.seq} data-phase={e.phase}>
          <div className="dot">{DOT[e.phase] ?? "·"}</div>
          <div>
            <div className="who">
              <b>{e.agent_name}</b>
              <small>{e.agent_role}</small>
              {typeof e.data?.took_ms === "number" && (
                <span className="took">
                  {Math.round((e.data.took_ms as number) / 100) / 10}s
                </span>
              )}
            </div>
            <div className="msg">{e.message}</div>
          </div>
        </div>
      ))}

      <div ref={bottom} />
    </aside>
  );
}
