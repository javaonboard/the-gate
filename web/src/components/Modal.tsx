import { useEffect, useRef, type ReactNode } from "react";

/** A dialog that stays put.
 *
 *  Both of these started as panels that opened in place — and in place meant
 *  inside a flex row that stretched, or inside a section that reloaded and
 *  took the panel with it while someone was using it. A dialog is not affected
 *  by what the page does underneath it, which is the whole point. */

export function Modal({ title, blurb, onClose, children, footer, wide }: {
  title: string;
  blurb?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", escape);
    box.current?.focus();
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal"
        data-wide={!!wide}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={box}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="modal-x" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {blurb && <p className="modal-blurb">{blurb}</p>}

        <div className="modal-body">{children}</div>

        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
