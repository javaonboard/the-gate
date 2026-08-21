import type { ReactNode } from "react";

/** A heading you can tell apart at a glance.
 *
 *  Small caps alone made every section look the same, so each gets a mark and
 *  weight of its own. */

const ICONS: Record<string, ReactNode> = {
  footage: (
    <path d="M2 5.5h13v13H2zM15 10l5.2-3.1a.7.7 0 0 1 1.05.6v9a.7.7 0 0 1-1.05.6L15 14z"
          stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" fill="none" />
  ),
  scenes: (
    <path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor"
          strokeWidth="1.7" strokeLinecap="round" />
  ),
  people: (
    <>
      <circle cx="9" cy="8.5" r="3.2" stroke="currentColor" strokeWidth="1.7" fill="none" />
      <path d="M3.5 19.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5M16 6.2a3 3 0 0 1 0 5.6M17.5 19.5c0-2-.7-3.6-1.8-4.6"
            stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" fill="none" />
    </>
  ),
  problems: (
    <>
      <path d="M12 4.2 21 19.5H3z" stroke="currentColor" strokeWidth="1.7"
            strokeLinejoin="round" fill="none" />
      <path d="M12 10v4M12 16.6v.1" stroke="currentColor" strokeWidth="1.9"
            strokeLinecap="round" />
    </>
  ),
  world: (
    <>
      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.7" fill="none" />
      <path d="M3.5 12h17M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5S14.2 18.2 12 20.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5z"
            stroke="currentColor" strokeWidth="1.5" fill="none" />
    </>
  ),
  grab: (
    <path d="M12 3.5v12M7.5 11.5 12 16l4.5-4.5M4.5 19.5h15"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
          strokeLinejoin="round" fill="none" />
  ),
};

export function SectionTitle({ icon, children, aside }: {
  icon: keyof typeof ICONS | string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="sect">
      <svg viewBox="0 0 24 24" aria-hidden>{ICONS[icon] ?? ICONS.scenes}</svg>
      <h3>{children}</h3>
      {aside && <span className="sect-aside">{aside}</span>}
    </div>
  );
}
