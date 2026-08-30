import { useEffect, useState } from "react";
import { Modal } from "./Modal";

/** Who is in these takes.
 *
 *  Faces are found by looking, and looking fails — an actor lit from behind, a
 *  chase where nobody turns to camera, a character whose face is inside a
 *  hood. The take is still of them and their coverage still counts.
 *
 *  A script supervisor writes this down because they were standing there. So
 *  can you. */

type Take = {
  take_id: string;
  shot_size_plain: string;
  seconds: number;
  usable: boolean;
  characters: { character_id: string; name: string }[];
};

type Person = {
  character_id: string;
  name: string;
  face_uri: string;
  appearances: number;
};

const length = (s: number) =>
  s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;

export function WhoIsIn({ sceneId, sceneName, onClose, onSaved }: {
  sceneId: string;
  sceneName: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [takes, setTakes] = useState<Take[] | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [adding, setAdding] = useState<string>("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const [breakdown, known] = await Promise.all([
      fetch(`/api/scenes/${sceneId}/breakdown`).then((r) => r.json()),
      fetch("/api/people").then((r) => r.json()),
    ]);
    setTakes(breakdown.takes ?? []);
    setPeople(known ?? []);
  }

  useEffect(() => {
    void load();
  }, [sceneId]);

  async function put(takeId: string, body: object) {
    setBusy(true);
    try {
      await fetch(`/api/takes/${takeId}/who`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setAdding("");
      setNewName("");
      await load();
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  async function remove(takeId: string, characterId: string) {
    setBusy(true);
    try {
      await fetch(`/api/takes/${takeId}/who/${characterId}`, {
        method: "DELETE",
      });
      await load();
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Who is in ${sceneName}?`}
      blurb="Nobody's face was clear enough to place. Say who is in each take and
             their coverage — wide, close, over-the-shoulder — is checked the
             same as anyone else's."
      onClose={onClose}
      footer={
        <button className="primary" disabled={busy} onClick={onClose}>
          Done
        </button>
      }
    >
      {!takes ? (
        <p className="modal-quiet">Looking…</p>
      ) : (
        <ul className="who-list">
          {takes.map((t) => (
            <li key={t.take_id}>
              <div className="who-take">
                <b>{t.take_id}</b>
                <small>
                  {length(t.seconds)} · {t.shot_size_plain}
                  {!t.usable && " · can't be used"}
                </small>
              </div>

              <div className="who-people">
                {t.characters.map((c) => (
                  <span className="who-chip" key={c.character_id}>
                    {c.name}
                    <button
                      onClick={() => remove(t.take_id, c.character_id)}
                      title="Not in this take after all"
                      aria-label={`Remove ${c.name}`}
                    >
                      ✕
                    </button>
                  </span>
                ))}

                {adding === t.take_id ? (
                  <span className="who-picker">
                    {people.map((p) => (
                      <button
                        key={p.character_id}
                        disabled={busy}
                        onClick={() => put(t.take_id, { character_id: p.character_id })}
                      >
                        {p.name}
                      </button>
                    ))}
                    <input
                      value={newName}
                      placeholder="or a new name"
                      onChange={(e) => setNewName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && newName.trim())
                          void put(t.take_id, { name: newName });
                      }}
                    />
                    <button className="linky" onClick={() => setAdding("")}>
                      cancel
                    </button>
                  </span>
                ) : (
                  <button
                    className="who-add"
                    onClick={() => setAdding(t.take_id)}
                  >
                    ＋ someone
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
