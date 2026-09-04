import { useEffect, useRef, useState } from "react";

/** When the day starts, when it is meant to end, and what time it is now.
 *
 *  All three are typed in and none of them move on their own. The clock used
 *  to place itself on every load, which meant saving a new call time also
 *  jumped the hour you were standing at, and the two controls read as though
 *  they were fighting each other.
 *
 *  Everything the call says about time hangs off these: how much day is left,
 *  the latest wrap that breaks no rule, what an overrun costs. */

type Day = {
  shoot_day: string;
  call_time: string;
  wrap_time: string;
  hours: number;
};

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** ISO to what a datetime-local input wants, and back.
 *
 *  Both directions stay in local time and never touch a timezone. Going
 *  through Date.toISOString() converted the typed time to UTC before storing
 *  it, so every save moved the call by the offset — and saving twice moved it
 *  twice.
 */
const toInput = (iso: string) => iso.slice(0, 16);
const fromInput = (local: string) => (local.length === 16 ? `${local}:00` : local);

const clockOf = (call: string, hoursIn: number) =>
  new Date(new Date(call).getTime() + hoursIn * 3600_000);

const asInput = (d: Date) => {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
       + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export function DayBar({ hoursIn, onHoursIn, onCommit }: {
  hoursIn: number;
  onHoursIn: (h: number) => void;
  // Fired when a time is committed, not while it is being changed. Every
  // number that depends on the clock is computed rather than written, so it
  // can be redone without waiting for anyone to explain it.
  onCommit?: (h: number) => void;
}) {
  const [day, setDay] = useState<Day | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ call_time: "", wrap_time: "", now: "" });
  const [problem, setProblem] = useState("");
  const placed = useRef(false);

  async function load(placeTheClock: boolean) {
    const d: Day = await fetch("/api/day").then((r) => r.json());
    setDay(d);

    // Only ever on the very first load, and only if nobody has said otherwise.
    // Re-placing it after a save is what made the clock feel possessed.
    if (placeTheClock && !placed.current) {
      placed.current = true;
      const since = (Date.now() - new Date(d.call_time).getTime()) / 3600_000;
      const onTheFloor = since > 0.5 && since < d.hours - 0.5;
      onHoursIn(Math.round((onTheFloor ? since : d.hours * (2 / 3)) * 2) / 2);
    }
    return d;
  }

  useEffect(() => {
    void load(true);
  }, []);

  function open() {
    if (!day) return;
    setProblem("");
    setDraft({
      call_time: toInput(day.call_time),
      wrap_time: toInput(day.wrap_time),
      now: asInput(clockOf(day.call_time, hoursIn)),
    });
    setEditing(true);
  }

  async function save() {
    const call = new Date(fromInput(draft.call_time));
    const wrap = new Date(fromInput(draft.wrap_time));
    const now = new Date(fromInput(draft.now));

    if (!(wrap > call)) {
      setProblem("The wrap has to be after the call.");
      return;
    }

    await fetch("/api/day", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        call_time: fromInput(draft.call_time),
        wrap_time: fromInput(draft.wrap_time),
      }),
    });
    const fresh = await load(false);

    // The clock is kept where it was put, in real time, not as a fraction of
    // a day that just changed length.
    const hours = (now.getTime() - new Date(fresh.call_time).getTime()) / 3600_000;
    const held = Math.max(0, Math.min(fresh.hours, Math.round(hours * 2) / 2));
    onHoursIn(held);
    setEditing(false);
    onCommit?.(held);
  }

  if (!day) return null;

  const now = clockOf(day.call_time, hoursIn);

  if (editing) {
    return (
      <div className="daybar editing">
        <label>
          call
          <input
            type="datetime-local"
            value={draft.call_time}
            onChange={(e) => setDraft({ ...draft, call_time: e.target.value })}
          />
        </label>
        <label>
          planned wrap
          <input
            type="datetime-local"
            value={draft.wrap_time}
            onChange={(e) => setDraft({ ...draft, wrap_time: e.target.value })}
          />
        </label>
        <label>
          time now
          <input
            type="datetime-local"
            value={draft.now}
            onChange={(e) => setDraft({ ...draft, now: e.target.value })}
          />
        </label>
        {problem && <span className="daybar-problem">{problem}</span>}
        <button className="primary" onClick={save}>save</button>
        <button className="linky" onClick={() => setEditing(false)}>cancel</button>
      </div>
    );
  }

  return (
    <div className="daybar">
      <span className="clock" onClick={open}
            title="What time it is on set. Drag to move through the day, or click to type it.">
        {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
      <input
        type="range"
        min={0}
        max={Math.max(day.hours, 0.5)}
        step={0.5}
        value={hoursIn}
        onChange={(e) => onHoursIn(Number(e.target.value))}
        onPointerUp={() => onCommit?.(hoursIn)}
        onKeyUp={() => onCommit?.(hoursIn)}
        title="What time it is on set. Drag to ask the same question at another hour."
      />
      <span className="daybar-ends" onClick={open}
            title={"When the crew was called and when you planned to wrap. "
                 + "Everything the call says about time is worked out from "
                 + "these. Click to change them."}>
        called {hhmm(day.call_time)} · wrap {hhmm(day.wrap_time)}
      </span>
    </div>
  );
}
