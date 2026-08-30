import { useEffect, useState } from "react";
import { Modal } from "./Modal";

/** What world this is set in.
 *
 *  Nothing can be called an anachronism until somebody says what the period is.
 *  A paper cup is only wrong because the scene is medieval — so this is the
 *  setting the whole QC pass is judged against. */

const PRESETS = [
  { label: "as shot", period: "near-future science fiction, around 2040",
    setting: "Amsterdam — canal streets, an old church, a workshop",
    notes: "Robots and holographic displays belong. Present-day branding, disposable cups and modern cars do not." },
  { label: "medieval", period: "1300s medieval Europe",
    setting: "a village street before industrialisation",
    notes: "No glass windows, printed text, plastic, zips, rubber soles or painted road markings." },
  { label: "1950s", period: "1950s",
    setting: "a small town high street",
    notes: "No mobile phones, no modern branding, no plastic bottles, no LED lighting." },
  { label: "present day", period: "present day",
    setting: "a modern city",
    notes: "" },
];

export function WorldPanel({ sceneId, onRechecked }: {
  sceneId: string;
  onRechecked: (runId: string) => void;
}) {
  const [world, setWorld] = useState({ period: "", setting: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    void fetch("/api/world").then((r) => r.json()).then(setWorld);
  }, []);

  async function save(next: typeof world, andRecheck: boolean) {
    setSaving(true);
    setWorld(next);
    try {
      await fetch("/api/world", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (andRecheck && sceneId) {
        const r = await fetch(`/api/scenes/${sceneId}/recheck`, { method: "POST" });
        const { run_id } = await r.json();
        onRechecked(run_id);
      }
    } finally {
      setSaving(false);
    }
  }

  /** "near-future science fiction, around 2040" is not a label on a button. */
  const short = (world.period || "").split(/[,—-]/)[0].trim() || "not set";

  const why =
    "What this film is set in, so anything that could not exist there gets "
    + "flagged as wrong — a paper cup in a medieval scene, a zip on a Roman "
    + "tunic. Read from the footage; change it if it guessed wrong.";

  return (
    <div className="worldbar">
      <button className="worldchip" onClick={() => setOpen((o) => !o)}
              title={
                world.setting
                  ? `${world.period} — ${world.setting}.

${why}`
                  : why
              }>
        <span className="worldchip-key">World</span>
        <span className="worldchip-val">{short}</span>
        <span className="worldchip-more">{open ? "close" : "change"}</span>
      </button>

      {open && (
        <Modal
          title="What is this film set in?"
          blurb="Everything QC flags as wrong is judged against this — a paper
                 cup in a medieval scene, a zip on a Roman tunic. It was read
                 from the footage; change it if it guessed wrong."
          onClose={() => setOpen(false)}
          footer={
            <button className="primary" disabled={saving}
                    onClick={() => { void save(world, true); setOpen(false); }}>
              {saving ? "Checking…" : "Save and check the footage again"}
            </button>
          }
        >
        <div className="world">
          <div className="world-presets">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                data-active={world.period === p.period}
                onClick={() => save({ period: p.period, setting: p.setting, notes: p.notes }, false)}
              >
                {p.label}
              </button>
            ))}
          </div>

          <label>
            when
            <input
              value={world.period}
              placeholder="1300s medieval Europe"
              onChange={(e) => setWorld({ ...world, period: e.target.value })}
            />
          </label>

          <label>
            where
            <input
              value={world.setting}
              placeholder="a village street"
              onChange={(e) => setWorld({ ...world, setting: e.target.value })}
            />
          </label>

          <label className="wide">
            what would look wrong
            <input
              value={world.notes}
              placeholder="no plastic, no printed text, no road markings"
              onChange={(e) => setWorld({ ...world, notes: e.target.value })}
            />
          </label>

        </div>
        </Modal>
      )}
    </div>
  );
}
