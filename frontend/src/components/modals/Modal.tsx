import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

interface ModalProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
  /** Tailwind max-width utility for the panel. */
  width?: string;
  /** Id of the element that describes the dialog, announced after the title. */
  describedBy?: string;
  children: ReactNode;
}

/**
 * Accessible shell shared by every dialog in the application. It names itself to
 * assistive technology, keeps keyboard focus inside the panel while it is open,
 * closes on Escape, and hands focus back to whatever opened it.
 */
export default function Modal({ title, subtitle, onClose, width = "max-w-lg", describedBy, children }: ModalProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    // Land on the first real control so a keyboard user does not have to tab
    // past the panel chrome to start working.
    const focusable = panel.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [];
    const firstField = [...focusable].find((element) => element !== closeButton.current);
    (firstField ?? closeButton.current ?? panel.current)?.focus();

    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
      if (event.key === "Tab") trapTab(event);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  function trapTab(event: globalThis.KeyboardEvent) {
    if (event.key !== "Tab") return;
    const focusable = [...(panel.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [])];
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    // Disabling the focused submit control can move focus back to the document.
    if (!focusable.includes(active as HTMLElement)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center p-4">
      {/* Presentational scrim. Escape and the close button are the keyboard exits. */}
      <div aria-hidden="true" onClick={onClose} className="fixed inset-0 bg-on-secondary-fixed/60" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedBy}
        tabIndex={-1}
        className={`relative w-full ${width} max-h-[90vh] flex flex-col bg-surface-container-lowest rounded-xl shadow-2xl border border-outline-variant overflow-hidden outline-none`}
      >
        <div className="flex items-center justify-between gap-4 p-5 border-b border-outline-variant shrink-0">
          <div className="min-w-0">
            <h2 id={titleId} className="font-headline-md text-xl break-words">{title}</h2>
            {subtitle && <p title={subtitle} className="text-sm text-on-surface-variant truncate">{subtitle}</p>}
          </div>
          <button ref={closeButton} type="button" aria-label="Close dialog" onClick={onClose} className="shrink-0 inline-flex items-center justify-center min-h-11 min-w-11 md:min-h-7 md:min-w-7 rounded hover:bg-surface-container-highest">
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
