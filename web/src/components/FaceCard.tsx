import { Modal } from "./Modal";

/** One character, big enough to argue with.
 *
 *  Everywhere else a face is a thumbnail used to tell rows apart. This is the
 *  one place it is evidence: the crop the system matched on, the words it
 *  wrote about them, and every take it decided they were in. A wrong take
 *  shows up here and nowhere else, because nowhere else puts the three next
 *  to each other. */

type Cell = {
  band: string;
  takes: string[];
};

export function FaceCard({ name, faceUri, appearances, cells, bandLabel,
                          onMerge, onClose }: {
  name: string;
  faceUri: string;
  appearances: number;
  cells: Cell[];
  bandLabel: Record<string, string>;
  onMerge?: () => void;
  onClose: () => void;
}) {
  const got = cells.filter((c) => c.takes.length > 0);
  const seen = [...new Set(got.flatMap((c) => c.takes))];

  return (
    <Modal
      title={name}
      blurb={`In ${appearances} take${appearances === 1 ? "" : "s"}. This is the picture the system matched them by — if a take below is not them, it matched wrong.`}
      onClose={onClose}
      footer={
        onMerge && (
          <button className="secondary" onClick={onMerge}>
            This is someone already listed
          </button>
        )
      }
    >
      <div className="facecard">
        <img className="facecard-shot" src={faceUri} alt={name} />

        <div className="facecard-takes">
          {got.length === 0 ? (
            <p className="facecard-none">
              Nothing of them yet — they appear, but no take covers them.
            </p>
          ) : (
            got.map((c) => (
              <div className="facecard-band" key={c.band}>
                <h5>{bandLabel[c.band] ?? c.band}</h5>
                <ul>
                  {c.takes.map((t) => <li key={t}>{t}</li>)}
                </ul>
              </div>
            ))
          )}
          {seen.length > 0 && (
            <p className="facecard-count">
              {seen.length} take{seen.length === 1 ? "" : "s"} give them
              something. Open one in the scene below to watch it.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
