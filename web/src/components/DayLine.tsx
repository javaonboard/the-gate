import { useEffect, useState } from "react";
import { clock, pct, usd } from "../api";
import type { GateCall } from "../api";
import { CrewChip } from "./CrewChip";
import { SceneGoal } from "./SceneGoal";
import { WorldPanel } from "./WorldPanel";

/** The call, for the day.
 *
 *  Two verdicts on one screen — one for the open scene at the top, one for the
 *  day underneath — read as a contradiction, because they answer different
 *  questions and nothing said so. The day is the headline: it is what the AD
 *  is carrying. The scene you happen to have open is detail.
 *
 *  Coverage is the day's; the clock is the day's too. They are the two axes,
 *  and they only mean anything together — being short only matters against the
 *  time left, and the time left only matters against what is still missing. */

type Scene = {
  scene_id: string;
  number: string;
  place: string;
  where: string;
  takes: number;
  positions: number;
  people: number;
  judged: boolean;
  completeness: number;
  required: number;
  have: number;
  exposure_usd: number;
};

type Today = {
  scenes: Scene[];
  day: {
    verdict: string;
    scenes: number;
    unjudged: number;
    required: number;
    have: number;
    completeness: number;
    judged: boolean;
    exposure_usd: number;
  };
};

export function DayLine({ reloadKey, call, openScene, onScene, onChanged,
                         onRechecked, onFootage, fresh = [] }: {
  reloadKey: number;
  call: GateCall | null;
  openScene?: string;
  onScene?: (sceneId: string) => void;
  onChanged?: () => void;
  onRechecked?: (runId: string) => void;
  onFootage?: () => void;
  fresh?: string[];
}) {
  const [today, setToday] = useState<Today | null>(null);
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [goalFor, setGoalFor] = useState<Scene | null>(null);
  const [adding, setAdding] = useState(false);
  const [place, setPlace] = useState("");

  async function load() {
    setToday(await fetch("/api/today").then((r) => (r.ok ? r.json() : null)));
  }

  useEffect(() => {
    void load();
  }, [reloadKey]);

  function stopPicking() {
    setPicking(false);
    setPicked([]);
  }

  function tick(sceneId: string) {
    setPicked((was) =>
      was.includes(sceneId)
        ? was.filter((s) => s !== sceneId)
        : [...was, sceneId]
    );
  }

  async function addScene() {
    const where = place.trim();
    if (!where) return;
    await fetch("/api/scenes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place: where }),
    });
    setPlace("");
    setAdding(false);
    await load();
    onChanged?.();
  }

  /** One room shot from both ends comes back as two scenes. Say otherwise. */
  async function mergePicked() {
    if (picked.length < 2) return;
    setBusy(true);
    try {
      const [into, ...rest] = picked;
      for (const other of rest) {
        await fetch(`/api/scenes/${into}/is/${other}`, { method: "POST" });
      }
      stopPicking();
      await load();
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }

  if (!today) {
    return (
      <div className="topline">
        <span className="topstat"><small>working out where we are…</small></span>
      </div>
    );
  }

  // A day with nothing in it still needs a way in. The footage button and the
  // crew live in this component, so returning early left a new day as a blank
  // page with no way to start — the one screen where the way in matters most.
  if (today.scenes.length === 0) {
    return (
      <section className="daycall">
        <div className="daycall-top">
          <span className="topstat">
            <b>Nothing shot yet</b>
            <small>drop a day's footage in and it sorts itself out</small>
          </span>

          <CrewChip onChanged={() => onChanged?.()} />

          <WorldPanel
            sceneId=""
            onRechecked={(id) => onRechecked?.(id)}
          />
        </div>

        <div className="daycall-foot">
          {onFootage && (
            <button className="primary" onClick={onFootage}>
              Bring footage in
            </button>
          )}
          {adding ? (
            <>
              <input
                className="dayscene-newplace"
                autoFocus
                value={place}
                placeholder="or name a scene — where is it?"
                onChange={(e) => setPlace(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setAdding(false);
                  if (e.key === "Enter" && place.trim()) void addScene();
                }}
              />
              <button className="secondary small" onClick={addScene}
                      disabled={!place.trim()}>
                Add
              </button>
              <button className="secondary small" onClick={() => setAdding(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="secondary small" onClick={() => setAdding(true)}>
              Start a scene by hand
            </button>
          )}
        </div>
      </section>
    );
  }

  const { day, scenes } = today;
  const go = day.verdict === "GO";
  const unset = day.verdict === "NOT CHECKED";

  return (
    <section className="daycall">
      <div className="daycall-top">
        <span className="badge" data-go={go} data-unjudged={unset}>
          {day.verdict}
        </span>

        <span className="topstat">
          <b>{day.judged ? pct(day.completeness) : "—"}</b>
          <small>
            of the day{day.judged && ` · ${day.have} of ${day.required} shots`}
          </small>
        </span>

        {day.exposure_usd > 0 && (
          <span className="topstat">
            <b style={{ color: "var(--warn)" }}>{usd(day.exposure_usd)}</b>
            <small>at risk if we move on</small>
          </span>
        )}

        {call && (
          <>
            <span className="topstat">
              <b style={{ color: oddsColour(call.day.p_make_the_day) }}>
                {pct(call.day.p_make_the_day)}
              </b>
              <small>chance of finishing today</small>
            </span>
            <span className="topstat">
              <b>{clock(call.day.hard_stop)}</b>
              <small>latest we can finish</small>
            </span>
          </>
        )}

        <CrewChip onChanged={() => onChanged?.()} />

        <WorldPanel
          sceneId={openScene ?? ""}
          onRechecked={(id) => onRechecked?.(id)}
        />
      </div>

      {/* the reasoning belongs with the call, not below the list of scenes */}
      {call && (call.spoken || call.summary) && (
        <details className="why">
          <summary>Why</summary>
          <p>{call.spoken || call.summary}</p>
        </details>
      )}

      <div className="daycall-scenes">
        {scenes.map((s) => {
          const chosen = picked.includes(s.scene_id);
          return (
            <div
              key={s.scene_id}
              className="dayscene"
              data-judged={s.judged}
              data-open={s.scene_id === openScene}
              data-chosen={chosen}
            >
              {picking && (
                <input
                  type="checkbox"
                  className="dayscene-tick"
                  checked={chosen}
                  onChange={() => tick(s.scene_id)}
                  aria-label={`Merge ${s.place}`}
                />
              )}

              <span className="dayscene-no">{s.number}</span>

              <button
                className="dayscene-main"
                onClick={() =>
                  picking ? tick(s.scene_id) : onScene?.(s.scene_id)
                }
                title={
                  s.judged
                    ? `${s.have} of ${s.required} shots${
                        s.exposure_usd
                          ? `, ${usd(s.exposure_usd)} to come back for`
                          : ""
                      }`
                    : "Nobody has said what this scene needs, so there was nothing to check it against. Open it and say what the director wanted."
                }
              >
                <span className="dayscene-bar">
                  <span
                    className="dayscene-fill"
                    style={{ width: `${Math.round(s.completeness * 100)}%` }}
                  />
                </span>
                <span className="dayscene-body">
                  <b>
                    {s.place}
                    {fresh.includes(s.scene_id) && (
                      <em className="just-in">just in</em>
                    )}
                  </b>
                  <small>
                    {s.where} ·{" "}
                    {s.takes
                      ? `${s.takes} take${s.takes === 1 ? "" : "s"}`
                      : "nothing shot"}
                    {s.positions > 1 && ` · ${s.positions} positions`}
                    {s.people > 0 && ` · ${s.people} on camera`}
                  </small>
                </span>
                <span className="dayscene-pct">
                  {s.judged ? pct(s.completeness) : "no goal set"}
                </span>
              </button>

              {!picking && (
                <button
                  className="dayscene-goal"
                  data-quiet={s.judged}
                  onClick={() => setGoalFor(s)}
                  title={
                    s.judged
                      ? "Change what this scene is checked against"
                      : "Say what the director wanted out of this scene"
                  }
                >
                  {s.judged ? "goal" : "Set the goal"}
                </button>
              )}
            </div>
          );
        })}
        {adding ? (
          <div className="dayscene adding">
            <input
              autoFocus
              value={place}
              placeholder="another place or time — where is it?"
              onChange={(e) => setPlace(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setAdding(false);
                if (e.key === "Enter" && place.trim()) void addScene();
              }}
            />
            <button className="primary small" onClick={addScene}
                    disabled={!place.trim()}>
              Add
            </button>
            <button className="secondary small" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        ) : (
          !picking && (
            <button className="dayscene-add" onClick={() => setAdding(true)}>
              ＋ another scene
            </button>
          )
        )}
      </div>

      <div className="daycall-foot">
        {picking ? (
          <>
            <span>
              {picked.length < 2
                ? "Tick the scenes that are really one place."
                : `${picked.length} scenes — they will become one.`}
            </span>
            <button
              className="primary small"
              disabled={picked.length < 2 || busy}
              onClick={mergePicked}
            >
              {busy ? "Merging…" : "Merge"}
            </button>
            <button className="secondary small" onClick={stopPicking}>
              Cancel
            </button>
          </>
        ) : (
          <>
            {onFootage && (
              <button className="secondary small" onClick={onFootage}>
                Bring footage in
              </button>
            )}
            {scenes.length > 1 && (
            <button
              className="secondary small"
              onClick={() => setPicking(true)}
              title="A room shot from one side and then from the other comes back as two scenes. It is one."
            >
              Merge scenes
            </button>
            )}
          </>
        )}
      </div>

      {goalFor && (
        <SceneGoal
          sceneId={goalFor.scene_id}
          sceneName={goalFor.place}
          onClose={() => setGoalFor(null)}
          onSaved={() => {
            void load();
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}

function oddsColour(p: number) {
  if (p >= 0.7) return "var(--go)";
  if (p >= 0.4) return "var(--warn)";
  return "var(--nogo)";
}
