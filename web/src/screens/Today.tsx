import type { GateCall } from "../api";
import { clock, pct, usd } from "../api";

const PLAIN: Record<string, string> = {
  master: "the wide shot of the whole scene",
  single: "close-up of one actor",
  ots: "over-the-shoulder shot",
  insert: "close-up of an object",
  reaction: "a reaction shot",
  plate: "clean shot for visual effects",
  establishing: "wide shot that sets the place",
};

function oddsColour(p: number) {
  if (p >= 0.7) return "var(--go)";
  if (p >= 0.4) return "var(--warn)";
  return "var(--nogo)";
}

export function Today({ call }: { call: GateCall | null }) {
  if (!call) return <div className="empty">Waiting for the first call…</div>;

  const { coverage, day, options } = call;
  const worth = options.filter((o) => o.worth_it);

  return (
    <>
      <div className="verdict">
        <h1 data-go={call.go}>{call.verdict}</h1>
        <div className="where">
          {call.scene_id} · {clock(call.now)}
        </div>
      </div>

      <p className="spoken">{call.spoken || call.summary}</p>

      <div className="row">
        <div className="card">
          <h3>Chance of finishing today</h3>
          <div className="stat" style={{ color: oddsColour(day.p_make_the_day) }}>
            {pct(day.p_make_the_day)}
          </div>
          <div className="meter">
            <div
              style={{
                width: `${day.p_make_the_day * 100}%`,
                background: oddsColour(day.p_make_the_day),
              }}
            />
          </div>
          <div className="stat-note">
            {day.trials.toLocaleString()} runs · {day.setups_remaining} camera
            positions left
          </div>
        </div>

        <div className="card">
          <h3>Shots we have</h3>
          <div className="stat">{pct(coverage.completeness)}</div>
          <div className="stat-note">
            {coverage.takes} takes logged
            {coverage.exposure_usd > 0 && (
              <> · {usd(coverage.exposure_usd)} at risk if we move on</>
            )}
          </div>
        </div>

        <div className="card">
          <h3>Latest we can finish</h3>
          <div className="stat small">{clock(day.hard_stop)}</div>
          <div className="stat-note">
            likely {clock(day.median_wrap)} · slow finish{" "}
            {clock(day.p90_wrap)}
            <br />
            after that the crew's rest is broken
          </div>
        </div>
      </div>

      {worth.length > 0 && (
        <section style={{ marginTop: 26 }}>
          <h3
            style={{
              fontSize: 11,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: "var(--faint)",
            }}
          >
            Worth grabbing before we move
          </h3>
          {worth.map((o, i) => (
            <div className="option" key={i} data-worth={o.worth_it}>
              <div className="head">
                <b>
                  {o.shot_type} {o.subject !== "-" ? o.subject : ""}
                </b>
                <span style={{ color: "var(--go)", fontFamily: "var(--mono)" }}>
                  saves {usd(o.saving_usd)}
                </span>
              </div>
              <div className="prices">
                <div>
                  <span>shoot now</span>
                  {usd(o.shoot_now_usd)}
                </div>
                <div>
                  <span>pick up later</span>
                  {usd(o.recover_later_usd)}
                </div>
                <div>
                  <span>odds after</span>
                  {pct(o.p_make_day_after)}
                </div>
              </div>
              <div className="verdict-line">{o.verdict}</div>
            </div>
          ))}
        </section>
      )}

      <section className="reqs">
        <h3
          style={{
            fontSize: 11,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: "var(--faint)",
            marginBottom: 8,
          }}
        >
          What this scene needs
        </h3>
        <div className="card" style={{ padding: "4px 0" }}>
          {coverage.requirements.map((r) => {
            const state = r.satisfied ? "ok" : r.priority === 1 ? "miss" : "gap";
            return (
              <div className="req" key={r.req_id} data-state={state}>
                <div className="tick">
                  {r.satisfied ? "✓" : r.priority === 1 ? "✕" : "○"}
                </div>
                <div className="what">
                  <b>
                    {r.shot_type}
                    {r.subject !== "-" ? ` · ${r.subject}` : ""}
                  </b>
                  <small>{PLAIN[r.shot_type] ?? r.shot_type_plain}</small>
                </div>
                <div className="clip">
                  {r.satisfied ? r.satisfied_by[0] : ""}
                </div>
                <div className="cost">
                  {r.satisfied ? "" : `${usd(r.recover_cost_usd)} to redo`}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </>
  );
}
