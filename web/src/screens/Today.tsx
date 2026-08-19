import { useState } from "react";
import type { GateCall } from "../api";
import { clock, pct, usd } from "../api";
import { CastMatrix } from "../components/CastMatrix";
import { DropZone } from "../components/DropZone";
import { SetupStrip } from "../components/SetupStrip";

function oddsColour(p: number) {
  if (p >= 0.7) return "var(--go)";
  if (p >= 0.4) return "var(--warn)";
  return "var(--nogo)";
}

export function Today({ call, sceneId, onIngested, onChanged }: {
  call: GateCall | null;
  sceneId: string;
  onIngested: (runId: string) => void;
  onChanged: () => void;
}) {
  const [setupId, setSetupId] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <>
      <SetupStrip
        sceneId={sceneId}
        selected={setupId}
        onSelect={setSetupId}
        reloadKey={reloadKey}
      />
      <DropZone
        sceneId={sceneId}
        setupId={setupId}
        onIngested={(id) => {
          setReloadKey((n) => n + 1);
          onIngested(id);
        }}
      />

      {!call ? (
        <div className="empty">Working out where we are…</div>
      ) : (
        <>
          <div className="verdict" style={{ marginTop: 24 }}>
            <h1 data-go={call.go}>{call.verdict}</h1>
            <div className="where">
              {call.scene_id} · {clock(call.now)}
            </div>
          </div>

          <p className="spoken">{call.spoken || call.summary}</p>

          <div className="row">
            <div className="card">
              <h3>Chance of finishing today</h3>
              <div
                className="stat"
                style={{ color: oddsColour(call.day.p_make_the_day) }}
              >
                {pct(call.day.p_make_the_day)}
              </div>
              <div className="meter">
                <div
                  style={{
                    width: `${call.day.p_make_the_day * 100}%`,
                    background: oddsColour(call.day.p_make_the_day),
                  }}
                />
              </div>
              <div className="stat-note">
                {call.day.trials.toLocaleString()} runs ·{" "}
                {call.day.setups_remaining} camera positions left
              </div>
            </div>

            <div className="card">
              <h3>Shots we have</h3>
              <div className="stat">{pct(call.coverage.completeness)}</div>
              <div className="stat-note">
                {call.coverage.takes} takes logged
                {call.coverage.exposure_usd > 0 && (
                  <> · {usd(call.coverage.exposure_usd)} at risk if we move on</>
                )}
              </div>
            </div>

            <div className="card">
              <h3>Latest we can finish</h3>
              <div className="stat small">{clock(call.day.hard_stop)}</div>
              <div className="stat-note">
                likely {clock(call.day.median_wrap)} · slow finish{" "}
                {clock(call.day.p90_wrap)}
                <br />
                after that the crew's rest is broken
              </div>
            </div>
          </div>

          {call.options.filter((o) => o.worth_it).length > 0 && (
            <section style={{ marginTop: 26 }}>
              <h3 className="section-title">Worth grabbing before we move</h3>
              {call.options
                .filter((o) => o.worth_it)
                .map((o, i) => (
                  <div className="option" key={i} data-worth={o.worth_it}>
                    <div className="head">
                      <b>
                        {o.shot_type} {o.subject !== "-" ? o.subject : ""}
                      </b>
                      <span
                        style={{
                          color: "var(--go)",
                          fontFamily: "var(--mono)",
                        }}
                      >
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
        </>
      )}

      <CastMatrix sceneId={sceneId} onChanged={onChanged} />
    </>
  );
}
