import { useId, useRef, useState, type ReactNode } from "react";
import { describeDeleteError } from "../../api/client";
import Modal from "./Modal";

interface ConfirmDeleteDialogProps {
  title: string;
  /** Names the record being removed. */
  subtitle: string;
  /** What the delete does, in the words the server's behaviour makes true. */
  children: ReactNode;
  actionLabel: string;
  pendingLabel: string;
  fallbackError: string;
  /** Runs the delete. A rejection keeps the dialog open and shows the refusal. */
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

/**
 * Confirmation step shared by the catalog deletes. It stays open on a refusal
 * (with the server's blocking records spelled out), blocks a second submit
 * while the request is in flight, and closes only when the delete succeeded.
 */
export default function ConfirmDeleteDialog({
  title, subtitle, children, actionLabel, pendingLabel, fallbackError, onConfirm, onClose,
}: ConfirmDeleteDialogProps) {
  const [pending, setPending] = useState(false);
  // State lags a render behind a fast double click; the ref does not.
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const descriptionId = useId();

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (caught: unknown) {
      setError(describeDeleteError(caught, fallbackError));
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <Modal title={title} subtitle={subtitle} describedBy={descriptionId} onClose={() => { if (!inFlight.current) onClose(); }}>
      <div className="p-6 space-y-4 overflow-y-auto">
        <div id={descriptionId} className="space-y-2">{children}</div>
        {error && (
          <div role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">
            <p>{error}</p>
          </div>
        )}
      </div>
      <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
        <button type="button" onClick={onClose} disabled={pending} className="px-4 py-2 min-h-11 md:min-h-6 disabled:opacity-60">Cancel</button>
        <button type="button" onClick={() => void confirm()} disabled={pending} className="px-5 py-2 min-h-11 md:min-h-6 rounded bg-error text-on-error font-bold disabled:opacity-60">
          {pending ? pendingLabel : actionLabel}
        </button>
      </div>
    </Modal>
  );
}
