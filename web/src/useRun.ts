import { useCallback, useRef, useState } from "react";
import type { AgentEvent, GateCall } from "./api";

/** Starts a check and follows it live.
 *
 *  The run id comes back before any work begins, so the stream is open while
 *  the crew works rather than replaying a finished job. The call itself
 *  arrives as the last event. */
export function useRun(scene: string) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [call, setCall] = useState<GateCall | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [touched, setTouched] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source = useRef<EventSource | null>(null);

  const start = useCallback(
    async (hoursIn: number, useAgent: boolean, sceneOverride?: string) => {
      source.current?.close();
      setEvents([]);
      setError(null);
      setBusy(true);

      try {
        const res = await fetch(
          `/api/runs?scene_id=${sceneOverride ?? scene}` +
            `&hours_in=${hoursIn}&use_agent=${useAgent}`,
          { method: "POST" }
        );
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const { run_id } = (await res.json()) as { run_id: string };
        setRunId(run_id);

        const es = new EventSource(`/api/runs/${run_id}/stream`);
        source.current = es;

        es.addEventListener("agent", (e) => {
          const event = JSON.parse((e as MessageEvent).data) as AgentEvent;
          setEvents((prev) => [...prev, event]);

          if (event.phase === "result") {
            setCall({ ...(event.data as unknown as GateCall), run_id });
            setBusy(false);
          }
          if (event.phase === "error") {
            setError(event.message);
            setBusy(false);
          }
          if (event.phase === "done" && event.agent === "orchestrator") {
            const scenes = (event.data?.scenes as string[]) ?? [];
            if (scenes.length) setTouched(scenes);
          }
          if (event.phase === "complete") {
            setBusy(false);
            es.close();
          }
        });

        es.onerror = () => {
          es.close();
          setBusy(false);
        };
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    },
    [scene]
  );

  /** Follow a run someone else started — an upload, or a webhook. */
  const follow = useCallback((id: string) => {
    source.current?.close();
    setEvents([]);
    setError(null);
    setBusy(true);
    setRunId(id);

    const es = new EventSource(`/api/runs/${id}/stream`);
    source.current = es;

    es.addEventListener("agent", (e) => {
      const event = JSON.parse((e as MessageEvent).data) as AgentEvent;
      setEvents((prev) => [...prev, event]);
      if (event.phase === "result") {
        setCall({ ...(event.data as unknown as GateCall), run_id: id });
      }
      if (event.phase === "done" && event.agent === "orchestrator") {
        const scenes = (event.data?.scenes as string[]) ?? [];
        if (scenes.length) setTouched(scenes);
      }
      if (event.phase === "complete") {
        setBusy(false);
        es.close();
      }
      if (event.phase === "error") {
        setError(event.message);
        setBusy(false);
      }
    });

    es.onerror = () => {
      es.close();
      setBusy(false);
    };
  }, []);

  return { events, call, runId, busy, error, touched, start, follow };
}
