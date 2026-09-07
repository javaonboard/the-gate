import { useMemo } from "react";
import type { AgentEvent } from "../api";
import { Modal } from "./Modal";

/** The crew, working.
 *
 *  Driven by the same event stream the status rail reads, so nothing here is
 *  choreographed — a node lights up because that agent actually published, and
 *  an edge fills because the work genuinely moved on. If the model is slow, the
 *  node sits there being slow.
 *
 *  Two dimensional on purpose. A three-dimensional graph looks impressive in a
 *  still and is harder to read in motion, and the whole point is that somebody
 *  watching for ninety seconds can see where the answer came from.
 *
 *  What it has to show, because it is the argument the project is making:
 *  which agent judged what, which tool it reached for, what it handed on, and
 *  the fact that the numbers were computed rather than spoken. */

/** What a stage runs on. The badge colour comes from this, so the same thing
 *  always looks the same wherever it appears. */
type Vendor = "gemini" | "clickhouse" | "parallel" | "python" | "ffmpeg";

type Stage = {
  key: string;          // the agent key the events use
  name: string;         // what a crew list would call them
  does: string;         // one line, present tense
  vendor: Vendor;       // what it runs on
  kind: "model" | "computed" | "partner";
  mcp?: boolean;        // reached through a Model Context Protocol server
  together?: boolean;   // runs at the same time as its neighbours
};

const VENDOR_LABEL: Record<Vendor, string> = {
  gemini: "Gemini",
  clickhouse: "ClickHouse",
  parallel: "Parallel",
  python: "Computed",
  ffmpeg: "ffmpeg",
};

/** Footage arrives and is understood. Runs once, per upload. */
const INTAKE: Stage[] = [
  { key: "editor", name: "Editor", does: "watches the film, finds where each shot begins", vendor: "gemini", kind: "model" },
  { key: "vision", name: "Scripty", does: "logs every take — size, movement, who is in it", vendor: "gemini", kind: "model", together: true },
  { key: "qc", name: "QC", does: "finds what would stop a take being used", vendor: "gemini", kind: "model" },
  { key: "script", name: "Breakdown", does: "merges the labels into the places they really are", vendor: "gemini", kind: "model", together: true },
  // Badged ClickHouse for the job ClickHouse actually does here: a vector
  // search over the faces already on the day, narrowing a new one to the few
  // people it could be before the model is asked to choose between them. The
  // old wording said it found faces, which read as though the database were
  // doing the looking.
  { key: "casting", name: "Casting", does: "matches every new face against the cast so far, by vector search", vendor: "clickhouse", kind: "partner" },
  { key: "continuity", name: "Continuity", does: "checks the takes will cut together", vendor: "gemini", kind: "model" },
];

/** The call. Runs every time the AD asks. */
const GATE: Stage[] = [
  { key: "scout", name: "Scout", does: "asks the live web what is happening at the location", vendor: "parallel", kind: "partner", together: true },
  { key: "historian", name: "The Book", does: "how long this crew has taken before", vendor: "clickhouse", kind: "partner", mcp: true, together: true },
  { key: "simulator", name: "The Clock", does: "ten thousand runs of the rest of the day", vendor: "python", kind: "computed" },
  { key: "compliance", name: "Steward", does: "which union rule bites, and what it costs", vendor: "python", kind: "computed" },
  { key: "planner", name: "1st AD", does: "prices every missing shot, both ways", vendor: "python", kind: "computed" },
  { key: "orchestrator", name: "The Gate", does: "reports the call it was given", vendor: "gemini", kind: "model" },
];

type State = "idle" | "working" | "done";

/** How long it took. The one number in here nobody could stage. */
const took = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

export function CrewGraph({ events, busy, onClose }: {
  events: AgentEvent[];
  busy: boolean;
  onClose?: () => void;
}) {
  /** What each agent is doing, from what it actually published. */
  const state = useMemo(() => {
    const out: Record<string, {
      state: State; said: string; tool: string; ms: number;
    }> = {};
    for (const e of events) {
      const now = out[e.agent]
        ?? { state: "idle" as State, said: "", tool: "", ms: 0 };
      if (e.phase === "working" || e.phase === "started") {
        now.state = "working";
        if (e.message) now.said = e.message;
      }
      if (e.phase === "tool_call") {
        now.state = "working";
        // the tool it actually reached for, not the sentence about it
        now.tool = (e.data?.tool as string) || now.tool;
      }
      // A tool coming back is not the agent finishing. It asked something and
      // got an answer, and now it has to do the work with it — the editor
      // reaches for ffmpeg early and then watches the whole film. Counting
      // this as done put the spinner out while the agent was still going, and
      // the status rail, which never believed it, went on saying so.
      if (e.phase === "tool_result") {
        // Keeps a working agent working; never wakes a finished one. The
        // takes are written to the database after the batch that judged them
        // has ended, and each write echoes its verdict — so Scripty, QC and
        // Casting all publish results after saying they had finished. Read as
        // a start, those echoes put three finished agents back to work and
        // left them spinning until the run itself ended.
        if (now.state !== "done") now.state = "working";
        if (e.message) now.said = e.message;
      }
      // Only the agent itself says it has stopped. An error is a stop too —
      // otherwise a failed agent spins until the whole run gives up.
      if (e.phase === "done" || e.phase === "result"
          || e.phase === "complete" || e.phase === "error") {
        now.state = "done";
        if (e.message) now.said = e.message;
        // how long it took, which is the part nobody can fake
        const spent = e.data?.took_ms as number | undefined;
        if (spent) now.ms = spent;
      }
      out[e.agent] = now;
    }
    // A run that has stopped has no one still working.
    if (!busy) {
      for (const k of Object.keys(out)) {
        if (out[k].state === "working") out[k].state = "done";
      }
    }
    return out;
  }, [events, busy]);

  const seen = (row: Stage[]) => row.some((s) => state[s.key]);
  const anything = events.length > 0;

  const inside = (
    <>
      {seen(INTAKE) || !anything ? (
        <Row title="Footage comes in" stages={INTAKE} state={state} />
      ) : null}
      <Row title="The call" stages={GATE} state={state} />

      <div className="crewgraph-key">
        <em className="crewbadge" data-vendor="gemini">Gemini</em> judged it
        <em className="crewbadge" data-vendor="python">Computed</em> no model involved
        <em className="crewbadge" data-vendor="clickhouse">ClickHouse</em>
        <em className="crewbadge" data-vendor="parallel">Parallel</em>
        <em className="crewbadge" data-vendor="mcp">MCP</em> the agent chose the query
        <span className="crewgraph-key-note">
          boxes joined underneath run at the same time
        </span>
      </div>
    </>
  );

  if (!onClose) return <section className="crewgraph">{inside}</section>;

  return (
    <Modal
      wide
      title="The crew, working"
      blurb="Every box lights up because that agent published something, not on
             a timer. The times are measured. If a model is slow, the box sits
             there being slow."
      onClose={onClose}
    >
      {inside}
    </Modal>
  );
}

function Row({ title, stages, state }: {
  title: string;
  stages: Stage[];
  state: Record<string, {
    state: State; said: string; tool: string; ms: number;
  }>;
}) {
  return (
    <div className="crewrow">
      <span className="crewrow-title">{title}</span>
      <div className="crewrow-track">
        {stages.map((s, i) => {
          const here = state[s.key];
          const before = i > 0 ? state[stages[i - 1].key] : undefined;
          return (
            <div className="crewstep" key={s.key} data-together={!!s.together}>
              {i > 0 && (
                <span
                  className="crewedge"
                  data-flowing={before?.state === "done" && !!here}
                  aria-hidden="true"
                />
              )}
              <div
                className="crewnode"
                data-kind={s.kind}
                data-state={here?.state ?? "idle"}
                title={`${s.name} — ${s.does}`}
              >
                <b>
                  <span className="crewname">
                    {here?.state === "working" && (
                      <span className="crewspin" aria-label="working" />
                    )}
                    {s.name}
                  </span>
                  {here?.ms ? <i>{took(here.ms)}</i> : null}
                </b>
                <small>{here?.said || s.does}</small>
                <span className="crewmarks">
                  <em className="crewbadge" data-vendor={s.vendor}>
                    {VENDOR_LABEL[s.vendor]}
                  </em>
                  {s.mcp && (
                    <em
                      className="crewbadge"
                      data-vendor="mcp"
                      title="Reached through a Model Context Protocol server — the agent decides what to ask and queries it as a tool, rather than the query being written into our code"
                    >
                      MCP
                    </em>
                  )}
                  {here?.tool && <code className="crewcall">{here.tool}</code>}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
