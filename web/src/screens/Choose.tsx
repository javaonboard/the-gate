import { useEffect, useState } from "react";

/** Which shoot day are you working on?
 *
 *  Nothing is assigned. Everything from here belongs to whichever workspace is
 *  picked, and the banner says which one — so there is never a question about
 *  where your footage went. */

type Workspace = {
  workspace_id: string;
  label: string;
  kind: string;
  created_at?: string;
  last_used?: string;
  scenes: number;
  takes: number;
};

type Listing = {
  current: string;
  demo: Workspace;
  workspaces: Workspace[];
};

/** The multiplier on every figure the day produces, asked once, up front. */
const CREWS = [
  { key: "micro", label: "Two people", blurb: "A creator shoot — everyone does three jobs." },
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
  const [label, setLabel] = useState("");
  const [crew, setCrew] = useState("standard");
  const [busy, setBusy] = useState(false);
  const [going, setGoing] = useState<string[]>([]);

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
    if (going.includes(w.workspace_id)) return;
    if (!confirm(`Delete "${w.label}" and everything in it?`)) return;

    // Out of the list at once. Waiting for the round trip left the day sitting
    // there looking undeleted, and it could be deleted again while it did.
    setGoing((was) => [...was, w.workspace_id]);
    try {
      await fetch(`/api/workspaces/${w.workspace_id}`, { method: "DELETE" });
      await load();
    } finally {
      setGoing((was) => was.filter((id) => id !== w.workspace_id));
    }
  }

  if (!listing) return <div className="empty">Looking for your work…</div>;

  return (
    <div className="choose">
      <h1>Which day are you on?</h1>
      <p className="choose-lead">
        Everything you upload belongs to one shoot day. Pick one to carry on
        with, or start a new one.
      </p>

      <div className="choose-new">
        <input
          value={label}
          placeholder="name it — “Tuesday, canal street”"
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void create("empty");
          }}
        />
        <button className="primary" disabled={busy} onClick={() => create("empty")}>
          Start empty
        </button>
        <button className="ghost" disabled={busy} onClick={() => create("demo")}>
          Start from a copy of the demo
        </button>

        <div className="choose-crew">
          <span>What kind of shoot is it?</span>
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
          <small>
            Sets how many people are on the clock, which is what turns hours
            into money. You can change it later.
          </small>
        </div>
      </div>

      {listing.workspaces.length > 0 && (
        <>
          <h2>Carry on with</h2>
          <div className="choose-list">
            {listing.workspaces
              .filter((w) => !going.includes(w.workspace_id))
              .map((w) => (
              <button key={w.workspace_id} className="choose-card"
                      onClick={() => open(w)}>
                <span className="choose-body">
                  <b>{w.label}</b>
                  <small>
                    {w.scenes} scene{w.scenes === 1 ? "" : "s"} ·{" "}
                    {w.takes} take{w.takes === 1 ? "" : "s"}
                    {w.last_used && ` · last opened ${when(w.last_used)}`}
                  </small>
                </span>
                <span className="choose-bin" title="Delete this day"
                      onClick={(e) => remove(w, e)}>
                  ×
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      <h2>Or just look</h2>
      <button className="choose-card demo" onClick={() => open(listing.demo)}>
        <span className="choose-body">
          <b>{listing.demo.label}</b>
          <small>
            {listing.demo.scenes} scene{listing.demo.scenes === 1 ? "" : "s"} ·{" "}
            {listing.demo.takes} takes · read-only
          </small>
        </span>
      </button>
    </div>
  );
}
