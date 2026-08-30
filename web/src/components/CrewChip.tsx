import { useEffect, useState } from "react";
import { Modal } from "./Modal";

/** Who is on the clock, and what they cost.
 *
 *  Every figure this product produces is a headcount times a rate — a meal
 *  penalty is paid to each person, overtime is each person's hours, an invaded
 *  turnaround is double time for everyone it touches. The same fourteen-hour
 *  day costs $6,730 with two people and $384,510 with a studio unit.
 *
 *  So this sits next to the money rather than in a settings page. It is the
 *  multiplier on the number beside it. */

type Tier = {
  key: string;
  label: string;
  blurb: string;
  crew_size: number;
  cast: number;
};

type Crew = {
  tier: string;
  departments: Record<string, number>;
  departments_labelled: { key: string; label: string; count: number }[];
  cast: number;
  minors: number;
  crew_rate: number;
  cast_rate: number;
  crew_size: number;
  on_the_clock: number;
  set_by_hand: boolean;
  tiers: Tier[];
};

export function CrewChip({ onChanged }: { onChanged?: () => void }) {
  const [crew, setCrew] = useState<Crew | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setCrew(await fetch("/api/crew").then((r) => (r.ok ? r.json() : null)));
  }

  useEffect(() => {
    void load();
  }, []);

  async function save(body: object) {
    setBusy(true);
    try {
      await fetch("/api/crew", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await load();
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }

  if (!crew) return null;

  return (
    <div className="crewbar">
      <button
        className="worldchip"
        onClick={() => setOpen(true)}
        title={
          `${crew.crew_size} crew at $${crew.crew_rate}/hr and ${crew.cast} cast ` +
          `at $${crew.cast_rate}/hr.\n\nEvery penalty is a headcount times a rate, ` +
          `so this is what turns hours into money. ` +
          (crew.set_by_hand ? "" : "Nobody has set it — this is the default.")
        }
      >
        <span className="worldchip-key">Crew</span>
        <span className="worldchip-val">
          {crew.crew_size} + {crew.cast} cast
        </span>
        <span className="worldchip-more">{crew.set_by_hand ? "change" : "set"}</span>
      </button>

      {open && (
        <Modal
          title="Who is on the clock?"
          blurb="Every penalty is paid per person, so this is the multiplier on
                 every figure the day produces. The same fourteen-hour day costs
                 a few thousand with two people and a few hundred thousand with
                 a studio unit."
          onClose={() => setOpen(false)}
          footer={
            <button className="primary" onClick={() => setOpen(false)}>
              Done
            </button>
          }
        >
          <div className="crew-tiers">
            {crew.tiers.map((t) => (
              <button
                key={t.key}
                className="crew-tier"
                data-active={crew.tier === t.key}
                disabled={busy}
                onClick={() => save({ tier: t.key })}
              >
                <b>{t.label}</b>
                <small>{t.blurb}</small>
                <em>
                  {t.crew_size} crew · {t.cast} cast
                </em>
              </button>
            ))}
          </div>

          <div className="crew-departments">
            {crew.departments_labelled.map((d) => (
              <label key={d.key}>
                <span>{d.label}</span>
                <input
                  type="number"
                  min={0}
                  value={d.count}
                  onChange={(e) =>
                    save({
                      departments: {
                        ...crew.departments,
                        [d.key]: Math.max(0, Number(e.target.value) || 0),
                      },
                    })
                  }
                />
              </label>
            ))}

            <label>
              <span>Cast</span>
              <input
                type="number"
                min={0}
                value={crew.cast}
                onChange={(e) => save({ cast: Math.max(0, Number(e.target.value) || 0) })}
              />
            </label>

            <label>
              <span title="Under 18. Their hours are capped by law, not by cost.">
                Of those, minors
              </span>
              <input
                type="number"
                min={0}
                max={crew.cast}
                value={crew.minors}
                onChange={(e) => save({ minors: Math.max(0, Number(e.target.value) || 0) })}
              />
            </label>

            <label>
              <span>Crew, per hour</span>
              <input
                type="number"
                min={0}
                value={crew.crew_rate}
                onChange={(e) => save({ crew_rate: Number(e.target.value) || 0 })}
              />
            </label>

            <label>
              <span>Cast, per hour</span>
              <input
                type="number"
                min={0}
                value={crew.cast_rate}
                onChange={(e) => save({ cast_rate: Number(e.target.value) || 0 })}
              />
            </label>
          </div>

          <p className="modal-quiet">
            {crew.on_the_clock} people on the clock. A minor over hours stops
            the day outright; everything else is paid.
          </p>
        </Modal>
      )}
    </div>
  );
}
