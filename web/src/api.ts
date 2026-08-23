/** Types and calls against the backend. */

export type Requirement = {
  req_id: string;
  shot_type: string;
  shot_type_plain: string;
  subject: string;
  priority: number;
  satisfied: boolean;
  satisfied_by: string[];
  recover_cost_usd: number;
};

export type Option = {
  shot_type: string;
  subject: string;
  shoot_now_usd: number;
  recover_later_usd: number;
  saving_usd: number;
  p_make_day_after: number;
  worth_it: boolean;
  verdict: string;
};

export type GateCall = {
  run_id: string;
  scene_id: string;
  now: string;
  verdict: "GO" | "NO-GO";
  go: boolean;
  summary: string;
  spoken?: string;
  coverage: {
    completeness: number;
    judged?: boolean;
    takes: number;
    exposure_usd: number;
    requirements: Requirement[];
  };
  day: {
    p_make_the_day: number;
    hard_stop: string;
    median_wrap: string;
    p90_wrap: string;
    expected_penalty_usd: number;
    setups_remaining: number;
    trials: number;
  };
  options: Option[];
};

export type AgentEvent = {
  run_id: string;
  agent: string;
  agent_name: string;
  agent_role: string;
  phase:
    | "started"
    | "working"
    | "tool_call"
    | "tool_result"
    | "done"
    | "result"     // the finished call, carried in data
    | "complete"   // the whole run is over
    | "error";
  message: string;
  data: Record<string, unknown>;
  seq: number;
  ts: string;
  elapsed_ms: number | null;
};

export type Dp = {
  id: string;
  name: string;
  role: string;
  setups: number;
  median_minutes: number;
  p90_minutes: number;
};

export type WorldEvent = {
  ts: string;
  source: string;
  kind: string;
  severity: number;
  summary: string;
  citation_url: string;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  gate: (scene: string, hoursIn: number, useAgent: boolean) =>
    get<GateCall>(
      `/api/gate/${scene}?hours_in=${hoursIn}&use_agent=${useAgent}`
    ),
  dps: () => get<Dp[]>("/api/library/dps"),
  dp: (id: string) => get<unknown>(`/api/library/dps/${id}`),
  world: () => get<WorldEvent[]>("/api/world"),
  glossary: () => get<Record<string, unknown>>("/api/glossary"),
  crew: () => get<{ key: string; name: string; role: string; does: string }[]>(
    "/api/crew"
  ),
};

/** Money, the way a production office writes it. */
export const usd = (n: number) =>
  n >= 1000 ? `$${Math.round(n).toLocaleString()}` : `$${Math.round(n)}`;

export const pct = (n: number) => `${Math.round(n * 100)}%`;

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
