import { useEffect, useState } from "react";

/** When the day starts, when it is meant to end, and where we are in it.
 *
 *  Taken from the shoot day on record rather than typed in — a production
 *  already knows its call time. Editable, because plans change, and draggable
 *  through the day so you can see the call change as the clock runs down. */

type Day = {
  shoot_day: string;
  call_time: string;
  wrap_time: string;
  hours: number;
};

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const toInput = (iso: string) => iso.slice(0, 16);

export function DayBar({ hoursIn, onHoursIn }: {
  hoursIn: number;
  onHoursIn: (h: number) => void;
}) {
  const [day, setDay] = useState<Day | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ call_time: "", wrap_time: "" });

  async function load() {
    const d: Day = await fetch("/api/day").then((r) => r.json());
    setDay(d);
    setDraft({ call_time: toInput(d.call_time), wrap_time: toInput(d.wrap_time) });
  }

  useEffect(() => {
    void load();
  }, []);

  async function save() {
    setEditing(false);
    await fetch("/api/day", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        call_time: new Date(draft.call_time).toISOString().slice(0, 19),
        wrap_time: new Date(draft.wrap_time).toISOString().slice(0, 19),
      }),
    });
    await load();
  }

  if (!day) return null;

  const now = new Date(new Date(day.call_time).getTime() + hoursIn * 3600_000);

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
        <button className="primary" onClick={save}>save</button>
        <button className="linky" onClick={() => setEditing(false)}>cancel</button>
      </div>
    );
  }

  return (
    <div className="daybar">
      <span className="clock">
        {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
      <input
        type="range"
        min={0}
        max={day.hours}
        step={0.5}
        value={hoursIn}
        onChange={(e) => onHoursIn(Number(e.target.value))}
        title="Move through the day"
      />
      <span className="daybar-ends" onClick={() => setEditing(true)}
            title="Change the call or the planned wrap">
        {hhmm(day.call_time)} – {hhmm(day.wrap_time)}
      </span>
    </div>
  );
}
