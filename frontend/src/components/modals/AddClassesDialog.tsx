import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, type BlockClass, type BlockSection, type Subject } from "../../api/client";
import Modal from "./Modal";

type SubjectResult =
  // `hold`: the subject is on the Block, but the note needs reading, so the
  // dialog does not close by itself.
  | { added: true; note: string; hold?: boolean }
  | { added: false; message: string };

function describeFailure(caught: unknown): string {
  return caught instanceof ApiError
    ? caught.message
    : "Could not reach the server, so this may have been saved. Close and check the Block and Subject offerings before adding it again.";
}

/**
 * Adds several subjects to one Block: a new offering per subject, then the link
 * to this Block. The API has no single call for this, so one subject can fail
 * while others succeed. Results stay per subject, and a retry reuses an offering
 * the server confirmed creating. The page's sync is paused while this is open,
 * so the Block's classes are re-read before each attempt: a subject another
 * session already linked is not given a second group.
 */
export default function AddClassesDialog({ block, termId, subjects, takenCodes, onClose, onChanged }: {
  block: BlockSection;
  termId: string;
  subjects: Subject[];
  takenCodes: string[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, SubjectResult>>({});
  const [saving, setSaving] = useState(false);
  const createdOfferings = useRef(new Map<string, string>());
  // A create whose response was lost: its group may exist, with no id to reuse.
  const unconfirmedCreates = useRef(new Set<string>());
  const submitButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);

  // Disabling the focused submit button drops focus, so it is put back once the
  // buttons are enabled again.
  useEffect(() => {
    if (saving || !restoreFocus.current) return;
    restoreFocus.current = false;
    (submitButton.current?.disabled ? closeButton.current : submitButton.current)?.focus();
  }, [saving]);

  const available = subjects.filter((subject) => !takenCodes.includes(subject.subject_code));
  const needle = query.trim().toLowerCase();
  const matching = available.filter((subject) =>
    `${subject.subject_code} ${subject.subject_title}`.toLowerCase().includes(needle));
  const pending = selected.filter((code) => !results[code] || !results[code].added);
  const retryOnly = pending.length > 0 && pending.every((code) => results[code]);
  const attempted = Object.keys(results);
  // Read from results, not the selection: unticking a subject must not hide
  // the class group its failed attempt left behind.
  const leftBehind = attempted.some((code) => !results[code].added && createdOfferings.current.has(code));

  function toggle(code: string) {
    setSelected((current) => current.includes(code)
      ? current.filter((item) => item !== code)
      : [...current, code]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    const next = { ...results };
    let linked: BlockClass[] | null = null;
    try {
      linked = (await api.listBlockClasses(block.block_id)).items;
    } catch (caught: unknown) {
      // This attempt wrote nothing. What earlier attempts left has its own notice.
      const message = caught instanceof ApiError
        ? caught.message
        : "Could not reach the server. Retry when it is back.";
      for (const code of pending) next[code] = { added: false, message };
    }
    for (const code of linked ? pending : []) {
      const existing = linked?.find((item) => item.subject_code === code);
      const created = createdOfferings.current.get(code);
      if (existing) {
        // Linked through our own group means only the earlier response was lost.
        next[code] = created && existing.offering_id !== created
          ? { added: true, hold: true, note: "Another session added this first, so the new class group made here is unlinked. You can remove it under Subject offerings." }
          : { added: true, note: created ? "Added" : "Already on this Block" };
      } else {
        try {
          let offeringId = created;
          if (!offeringId) {
            try {
              offeringId = (await api.createOffering(termId, code)).offering_id;
            } catch (caught: unknown) {
              if (!(caught instanceof ApiError)) unconfirmedCreates.current.add(code);
              throw caught;
            }
            createdOfferings.current.set(code, offeringId);
          }
          await api.addBlockClass(block.block_id, offeringId);
          next[code] = { added: true, note: "Added" };
        } catch (caught: unknown) {
          // Another session deleted the group, so the next retry makes a new one.
          if (caught instanceof ApiError && caught.code === "REFERENCE_NOT_FOUND") {
            createdOfferings.current.delete(code);
          }
          next[code] = { added: false, message: describeFailure(caught) };
        }
      }
      setResults({ ...next });
    }
    setResults(next);
    onChanged();
    // Every attempt counts, not just what is ticked now: unticking a failure
    // must not let the dialog close over the group it left behind.
    const allResolved = unconfirmedCreates.current.size === 0
      && Object.values(next).every((result) => result.added && !result.hold);
    if (allResolved && selected.every((code) => next[code]?.added)) {
      onClose();
    } else {
      restoreFocus.current = true;
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`Add classes to ${block.name}`}
      subtitle="Each subject gets its own new class group."
      onClose={() => { if (!saving) onClose(); }}
    >
      <form
        onSubmit={(event) => void submit(event)}
        // Enter in the search box or on a checkbox must not commit the selection.
        onKeyDown={(event) => {
          if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault();
        }}
        className="flex flex-col min-h-0"
      >
        <div className="p-5 space-y-3 overflow-y-auto">
          <label className="block">
            <span className="block text-sm font-medium mb-1">Find a subject</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by code or title"
              className="w-full border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest"
            />
          </label>
          {subjects.length === 0 ? (
            <p className="text-sm text-on-surface-variant">
              No subjects in the catalog yet. Add them in the <Link to="/subjects" className="text-primary underline">Subjects catalog</Link>.
            </p>
          ) : available.length === 0 ? (
            <p className="text-sm text-on-surface-variant">This Block already takes every subject in the catalog.</p>
          ) : matching.length === 0 ? (
            <p className="text-sm text-on-surface-variant">No subjects match that search.</p>
          ) : (
            <fieldset className="max-h-64 overflow-y-auto divide-y divide-outline-variant border border-outline-variant rounded-lg">
              <legend className="sr-only">Subjects to add</legend>
              {matching.map((subject) => {
                const result = results[subject.subject_code];
                return (
                  <label key={subject.subject_code} className="flex items-start gap-3 px-3 py-2 min-h-11 md:min-h-6 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.includes(subject.subject_code)}
                      disabled={saving || result?.added === true}
                      onChange={() => toggle(subject.subject_code)}
                    />
                    <span>
                      <strong>{subject.subject_code}</strong> {subject.subject_title}
                      <span className="block text-on-surface-variant">{subject.units} units, {subject.subject_type}</span>
                    </span>
                  </label>
                );
              })}
            </fieldset>
          )}
          {/* A search can hide a ticked subject; it is still submitted. */}
          {selected.length > 0 && (
            <p className="text-sm"><strong>Selected:</strong> {selected.join(", ")}</p>
          )}
          <div role="status" className="space-y-2">
            {attempted.length > 0 && (
              <ul aria-label="Results" className="text-sm space-y-1">
                {attempted.map((code) => {
                  const result = results[code];
                  return (
                    <li key={code} className={result.added ? "text-secondary" : "text-error"}>
                      {code}: {result.added ? result.note : result.message}
                    </li>
                  );
                })}
              </ul>
            )}
            {leftBehind && (
              <p className="text-sm text-on-surface-variant">
                A failed subject left a new class group unlinked. Retry links it. If you close now, you can remove it under Subject offerings.
              </p>
            )}
            {unconfirmedCreates.current.size > 0 && (
              <p className="text-sm text-on-surface-variant">
                The server may have created an extra class group for {[...unconfirmedCreates.current].join(", ")}. Check Subject offerings and remove any group with no Block.
              </p>
            )}
          </div>
        </div>
        <div className="p-5 border-t border-outline-variant flex justify-end gap-3">
          <button ref={closeButton} type="button" onClick={onClose} disabled={saving} className="min-h-11 md:min-h-6 px-4 py-2 rounded border border-outline-variant disabled:opacity-60">
            {attempted.length ? "Close" : "Cancel"}
          </button>
          <button ref={submitButton} disabled={saving || pending.length === 0} className="min-h-11 md:min-h-6 px-4 py-2 rounded bg-primary text-on-primary font-bold disabled:opacity-50">
            {saving
              ? "Adding..."
              : retryOnly
                ? `Retry ${pending.length} failed`
                : pending.length === 0
                  ? "Add classes"
                  : `Add ${pending.length} ${pending.length === 1 ? "class" : "classes"}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}
