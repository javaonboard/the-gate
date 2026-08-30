import { useEffect, useState } from "react";
import { Modal } from "./Modal";

/** What the director wanted out of this scene.
 *
 *  The system can find a close-up with nobody in it. It cannot know that the
 *  close-up *was* the point — that the scene is a hand struggling with a door
 *  handle and needs nothing else. Only somebody who was there knows that, and
 *  until they say so the scene cannot honestly be called covered or short.
 *
 *  A checklist, not a wall of prose: three things, ticked or not. */

type Shot = {
  shot: string;
  label: string;
  help: string;
  required: boolean;
  state: string;
  takes: string[];
  recover_cost_usd: number;
};

export function SceneGoal({ sceneId, sceneName, onClose, onSaved }: {
  sceneId: string;
  sceneName: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [shots, setShots] = useState<Shot[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void fetch(`/api/scenes/${sceneId}/matrix`)
      .then((r) => r.json())
      .then((d) => setShots(d.scene_shots ?? []));
  }, [sceneId]);

  async function set(shot: string, required: boolean) {
    setBusy(true);
    setShots((was) =>
      was?.map((s) => (s.shot === shot ? { ...s, required } : s)) ?? was
    );
    try {
      await fetch(`/api/scenes/${sceneId}/needs/${shot}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ required }),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`What does ${sceneName} need?`}
      blurb="Tick what the director wanted. Anything ticked is checked against
             the footage; anything not ticked is nobody's business."
      onClose={onClose}
      footer={
        <button
          className="primary"
          disabled={busy}
          onClick={() => {
            onSaved();
            onClose();
          }}
        >
          Done
        </button>
      }
    >
      {!shots ? (
        <p className="modal-quiet">Looking…</p>
      ) : (
        <ul className="goal-list">
          {shots.map((s) => (
            <li key={s.shot}>
              <label>
                <input
                  type="checkbox"
                  checked={s.required}
                  onChange={(e) => set(s.shot, e.target.checked)}
                />
                <span className="goal-text">
                  <b>{s.label}</b>
                  <small>{s.help}</small>
                </span>
                <span className="goal-state" data-state={s.state}>
                  {s.state === "have"
                    ? `got it · ${s.takes.length} take${s.takes.length === 1 ? "" : "s"}`
                    : s.required
                      ? "not shot"
                      : ""}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}

      <p className="modal-quiet">
        Coverage of each person — wide, close, over-the-shoulder — is set on the
        grid behind this.
      </p>
    </Modal>
  );
}
