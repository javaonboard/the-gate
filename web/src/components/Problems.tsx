import { useEffect, useState } from "react";
import { SectionTitle } from "./SectionTitle";

/** What QC found.
 *
 *  The reason this exists: a coffee cup on a medieval table costs nothing to
 *  fix while the camera is still up, and cannot be fixed once the set is
 *  struck. A blocking problem takes the take out of coverage entirely. */

type Problem = {
  take_id: string;
  category: string;
  severity: "blocking" | "warning" | "note";
  what: string;
  where: string;
  at_seconds: number;
};

const CATEGORY: Record<string, string> = {
  // technical
  crew_or_equipment: "crew in shot",
  boom_shadow: "mic shadow",
  reflection: "camera reflected",
  focus: "focus",
  exposure: "exposure",
  artefact: "artefact",
  framing: "framing",
  // the world
  anachronism: "didn't exist yet",
  wrong_place: "doesn't belong here",
  modern_branding: "modern branding",
  // won't cut
  continuity: "won't cut",
  screen_direction: "facing the wrong way",
  prop_position: "prop moved",
  wardrobe: "wardrobe changed",
  physical_state: "state changed",
  hair_makeup: "hair or makeup",
  light: "light moved",
  performance: "performance",
};

export function Problems({ sceneId, reloadKey }: {
  sceneId: string;
  reloadKey?: number;
}) {
  const [rows, setRows] = useState<Problem[]>([]);

  useEffect(() => {
    if (!sceneId) return;
    void fetch(`/api/scenes/${sceneId}/problems`)
      .then((r) => r.json())
      .then(setRows);
  }, [sceneId, reloadKey]);

  const shown = rows.filter((r) => r.severity !== "note");
  if (!shown.length) return null;

  const blocking = shown.filter((r) => r.severity === "blocking").length;

  return (
    <section style={{ marginTop: 26 }}>
      <SectionTitle
        icon="problems"
        aside={
          blocking > 0
            ? `${blocking} take${blocking > 1 ? "s" : ""} can't be used`
            : `${shown.length} worth a look`
        }
      >
        Problems in the footage
      </SectionTitle>

      <div className="problems">
        {shown.map((p, i) => (
          <div className="problem" key={i} data-severity={p.severity}>
            <span className="sev">
              {p.severity === "blocking" ? "can't use" : "check"}
            </span>
            <span className="body">
              <b>{p.what}</b>
              <small>
                {CATEGORY[p.category] ?? p.category}
                {p.where && ` · ${p.where}`}
                {p.at_seconds >= 0 && ` · at ${p.at_seconds.toFixed(1)}s`}
              </small>
            </span>
            <span className="which">{p.take_id.split("_").slice(-1)[0]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
