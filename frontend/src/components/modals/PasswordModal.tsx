import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import Modal from "./Modal";

// The browser's minLength counts UTF-16 units, the server counts characters,
// so a short emoji password can pass here and still be refused.
const PASSWORD_RULES = "Passwords need at least 8 characters; an emoji counts as one.";
// maxLength would truncate by UTF-16 units too, cutting a valid emoji password
// in half, so the server's character limit is checked on submit instead.
const MAX_PASSWORD_LENGTH = 1024;
const TOO_LONG = `Passwords can be at most ${MAX_PASSWORD_LENGTH} characters.`;

interface PasswordModalProps {
  title: string;
  /** Also asks for the current password (a user changing their own). */
  askCurrent?: boolean;
  submit: (currentPassword: string, newPassword: string) => Promise<void>;
  onClose: () => void;
}

const field = "w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary";

export default function PasswordModal({ title, askCurrent = false, submit, onClose }: PasswordModalProps) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ([...current].length > MAX_PASSWORD_LENGTH || [...next].length > MAX_PASSWORD_LENGTH) {
      setError(TOO_LONG);
      return;
    }
    setSaving(true);
    setError("");
    try {
      await submit(current, next);
      setDone(true);
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.code === "VALIDATION_ERROR"
          ? PASSWORD_RULES
          : caught instanceof ApiError ? caught.message : "Could not update the password.",
      );
    } finally {
      setSaving(false);
    }
  }

  // The server may still commit a request in flight, so the dialog can't be
  // dismissed until it settles.
  const close = () => {
    if (!saving) onClose();
  };
  return (
    <Modal title={title} onClose={close}>
      {done ? (
        <div className="p-6 space-y-5">
          <p role="status">Password updated.</p>
          <div className="flex justify-end">
            <button type="button" onClick={onClose} className="px-5 py-2 rounded bg-surface-tint text-white font-bold">Close</button>
          </div>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col min-h-0">
          <div className="p-6 grid gap-5 overflow-y-auto">
            {askCurrent && (
              <label className="block space-y-1.5">
                <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Current password</span>
                <input type="password" value={current} onChange={(event) => setCurrent(event.target.value)} required autoComplete="current-password" className={field} />
              </label>
            )}
            <label className="block space-y-1.5">
              <span className="font-label-md text-[11px] uppercase text-on-surface-variant">New password</span>
              <input type="password" value={next} onChange={(event) => setNext(event.target.value)} required minLength={8} autoComplete="new-password" className={field} />
            </label>
            {error && <p role="alert" className="text-sm text-error">{error}</p>}
          </div>
          <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
            <button type="button" onClick={close} aria-disabled={saving} className="px-4 py-2 aria-disabled:opacity-60">Cancel</button>
            <button disabled={saving} className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">{saving ? "Saving..." : "Save password"}</button>
          </div>
        </form>
      )}
    </Modal>
  );
}
