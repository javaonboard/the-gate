import { useEffect, useState } from "react";

/** Which day are you working on?
 *
 *  Nothing is assigned. Whatever is picked here owns everything that follows,
 *  so there is never a question about where footage went.
 *
 *  Most people arriving have shot nothing, so the day that is already analysed
 *  comes first and is the only thing they have to understand. Making one of
 *  your own sits underneath, and stays folded away until asked for. */

type Workspace = {
  workspace_id: string;
  label: string;
  kind: string;
  last_used?: string;
  scenes: number;
  takes: number;
};

type Listing = {
  current: string;
  demo: Workspace;
  workspaces: Workspace[];
};

/** How many people are on the clock, which is what turns hours into money. */
const CREWS = [
  { key: "micro", label: "Two people", blurb: "A creator shoot. Everyone does three jobs." },
  { key: "indie", label: "Small indie", blurb: "Departments exist, one person deep." },
  { key: "standard", label: "Standard", blurb: "A drama or a series. Real department heads." },
  { key: "studio", label: "Studio", blurb: "Every department fully staffed." },
];

const when = (iso?: string) =>
  iso
    ? new Date(iso).toLocaleString([], {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      })
    : "";

export function Choose({ onChosen }: { onChosen: (id: string, label: string) => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [making, setMaking] = useState(false);
  const [label, setLabel] = useState("");
  const [crew, setCrew] = useState("standard");
  const [busy, setBusy] = useState(false);

  async function load() {
    setListing(await fetch("/api/workspaces").then((r) => r.json()));
  }

  useEffect(() => {
    void load();
  }, []);

  async function create(startFrom: "empty" | "demo") {
    setBusy(true);
    try {
      const made = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, start_from: startFrom, crew }),
      }).then((r) => r.json());
      onChosen(made.workspace_id, made.label);
    } finally {
      setBusy(false);
    }
  }

  async function open(w: Workspace) {
    await fetch(`/api/workspaces/${w.workspace_id}/open`, { method: "POST" });
    onChosen(w.workspace_id, w.label);
  }

  async function remove(w: Workspace, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm(`Delete "${w.label}" and everything in it?`)) return;
    await fetch(`/api/workspaces/${w.workspace_id}`, { method: "DELETE" });
    await load();
  }

  if (!listing) return <div className="empty">Looking for your work…</div>;

  const { demo, workspaces } = listing;

  return (
    <div className="pick">
      <header className="pick-head">
        <h1>Which day are you on?</h1>
        <p>Everything you drop in belongs to one shoot day.</p>
      </header>

      {/* already shot and already analysed: the way in for anyone new */}
      <button className="pick-demo" onClick={() => open(demo)}>
        <span className="pick-demo-body">
          <b>{demo.label}</b>
          <small>
            {demo.scenes} scene{demo.scenes === 1 ? "" : "s"} · {demo.takes} takes
            · already analysed
          </small>
        </span>
        <span className="pick-go">Open</span>
      </button>

      {workspaces.length > 0 && (
        <section className="pick-yours">
          <h2>Your days</h2>
          {workspaces.map((w) => (
            <div className="pick-row" key={w.workspace_id}>
              <button className="pick-row-main" onClick={() => open(w)}>
                <b>{w.label}</b>
                <small>
                  {w.scenes} scene{w.scenes === 1 ? "" : "s"} · {w.takes} takes
                  {w.last_used && ` · ${when(w.last_used)}`}
                </small>
              </button>
              <button
                className="pick-bin"
                title="Delete this day"
                onClick={(e) => remove(w, e)}
              >
                ×
              </button>
            </div>
          ))}
        </section>
      )}

      <section className="pick-new">
        {!making ? (
          <button className="secondary" onClick={() => setMaking(true)}>
            Start a new day
          </button>
        ) : (
          <>
            <h2>A new day</h2>

            <label className="pick-field">
              <span>Call it something</span>
              <input
                autoFocus
                value={label}
                placeholder="Tuesday, canal street"
                onChange={(e) => setLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void create("empty");
                  if (e.key === "Escape") setMaking(false);
                }}
              />
            </label>

            <div className="pick-field">
              <span>What kind of shoot</span>
              <div className="pick-crew">
                {CREWS.map((c) => (
                  <button
                    key={c.key}
                    data-active={crew === c.key}
                    onClick={() => setCrew(c.key)}
                    title={c.blurb}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            <p className="pick-why">
              Sets how many people are on the clock, which is what turns hours
              into money. Changeable later.
            </p>

            <div className="pick-actions">
              <button className="primary" disabled={busy}
                      onClick={() => create("empty")}>
                Start empty
              </button>
              <button className="secondary" disabled={busy}
                      onClick={() => create("demo")}>
                Copy {demo.label}
              </button>
              <button className="linky" onClick={() => setMaking(false)}>
                cancel
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
