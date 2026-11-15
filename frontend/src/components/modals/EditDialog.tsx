import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import Modal from "./Modal";

export type EditField =
  | { name: string; label: string; kind: "text"; value: string; maxLength?: number }
  | { name: string; label: string; kind: "number"; value: number; min?: number }
  | { name: string; label: string; kind: "time"; value: string }
  | { name: string; label: string; kind: "select"; value: string; options: { label: string; value: string }[] }
  | { name: string; label: string; kind: "checkbox"; value: boolean };

export type EditValues = Record<string, string | number | boolean>;

interface EditDialogProps {
  title: string;
  fields: EditField[];
  submitLabel?: string;
  onClose: () => void;
  onSubmit: (values: EditValues) => Promise<unknown>;
}

// An <input type="time"> only accepts HH:MM at the default step, while the API
// answers with seconds. Trimming here keeps the seeded value visible.
const asTimeInput = (value: string) => value.slice(0, 5);

function seed(fields: EditField[]): Record<string, string | boolean> {
  return Object.fromEntries(fields.map((field) => [
    field.name,
    field.kind === "checkbox" ? field.value
      : field.kind === "time" ? asTimeInput(field.value)
      : String(field.value),
  ]));
}

/**
 * One dialog for editing an existing record. Every value is collected before
 * anything is sent, so abandoning the dialog discards the whole edit rather than
 * leaving a half-applied change, and a rejected save keeps the entered values on
 * screen next to the reason.
 */
export default function EditDialog({ title, fields, submitLabel = "Save changes", onClose, onSubmit }: EditDialogProps) {
  const [values, setValues] = useState(() => seed(fields));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function update(name: string, value: string | boolean) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload: EditValues = Object.fromEntries(fields.map((field) => {
        const entered = values[field.name];
        if (field.kind === "checkbox") return [field.name, Boolean(entered)];
        if (field.kind === "number") return [field.name, Number(entered)];
        return [field.name, String(entered).trim()];
      }));
      await onSubmit(payload);
      onClose();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "The request to the Chronos server failed.");
      setSaving(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 space-y-5 overflow-y-auto">
          {fields.map((field) => field.kind === "checkbox" ? (
            <label key={field.name} className="flex items-center gap-2 min-h-11 md:min-h-6 text-sm">
              <input
                type="checkbox"
                checked={Boolean(values[field.name])}
                onChange={(event) => update(field.name, event.target.checked)}
              />
              {field.label}
            </label>
          ) : (
            <label key={field.name} className="block space-y-1.5">
              <span className="font-label-md text-[11px] uppercase text-on-surface-variant">{field.label}</span>
              {field.kind === "select" ? (
                <select
                  required
                  value={String(values[field.name])}
                  onChange={(event) => update(field.name, event.target.value)}
                  className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
                >
                  {field.options.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              ) : (
                <input
                  type={field.kind === "text" ? "text" : field.kind}
                  required
                  value={String(values[field.name])}
                  min={field.kind === "number" ? field.min ?? 0 : undefined}
                  maxLength={field.kind === "text" ? field.maxLength : undefined}
                  onChange={(event) => update(field.name, event.target.value)}
                  className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
                />
              )}
            </label>
          ))}
          {error && <p role="alert" className="text-sm text-error">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} className="min-h-11 md:min-h-6 px-4 py-2">Cancel</button>
          <button disabled={saving} className="min-h-11 md:min-h-6 px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60">
            {saving ? "Saving..." : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
