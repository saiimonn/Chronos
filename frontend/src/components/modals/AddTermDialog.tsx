import { useId, useRef, useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { ApiError, api, type Term } from "../../api/client";
import { SETUP_CUTOFF_ACADEMIC_YEAR, TERM_NUMBERS, academicYearProblem } from "../../config/terms";
import Modal from "./Modal";

interface AddTermDialogProps {
  /** Every known term, archived ones included, so a duplicate is caught before the request. */
  existingTerms: Term[];
  onClose: () => void;
  onCreated: (term: Term) => void;
}

function duplicateProblem(terms: Term[], academicYear: string, termNumber: number): string {
  const existing = terms.find((term) => term.academic_year === academicYear && term.term_number === termNumber);
  if (!existing) return "";
  return existing.is_archived
    ? `Term ${termNumber} of AY ${academicYear} already exists but is archived. Restore it from Archived terms.`
    : `Term ${termNumber} of AY ${academicYear} already exists.`;
}

export default function AddTermDialog({ existingTerms, onClose, onCreated }: AddTermDialogProps) {
  const [academicYear, setAcademicYear] = useState("");
  const [termNumber, setTermNumber] = useState(String(TERM_NUMBERS[0]));
  const [touched, setTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const [serverError, setServerError] = useState("");
  const yearId = useId();
  const yearHintId = useId();
  const yearErrorId = useId();
  const termId = useId();

  const trimmed = academicYear.trim();
  const yearProblem = academicYearProblem(trimmed);
  const problem = yearProblem || duplicateProblem(existingTerms, trimmed, Number(termNumber));
  const showProblem = touched && problem !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (problem || inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setServerError("");
    try {
      const created = await api.createTerm({ academic_year: trimmed, term_number: Number(termNumber) });
      onCreated(created);
      onClose();
    } catch (caught: unknown) {
      setServerError(caught instanceof ApiError ? caught.message : "The request to the Chronos server failed.");
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <Modal title="Add term" subtitle="New terms start inactive; set one active when you are ready." onClose={() => { if (!inFlight.current) onClose(); }}>
      <form onSubmit={(event) => void submit(event)} noValidate className="flex flex-col min-h-0">
        <div className="p-6 space-y-4 overflow-y-auto">
          <div className="space-y-1.5">
            <label htmlFor={yearId} className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Academic year</label>
            <input
              id={yearId}
              value={academicYear}
              onChange={(event) => { setAcademicYear(event.target.value); setServerError(""); }}
              onBlur={() => { if (academicYear) setTouched(true); }}
              placeholder={SETUP_CUTOFF_ACADEMIC_YEAR}
              maxLength={9}
              inputMode="numeric"
              autoComplete="off"
              required
              aria-invalid={showProblem}
              aria-describedby={showProblem ? `${yearHintId} ${yearErrorId}` : yearHintId}
              className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary aria-[invalid=true]:border-error"
            />
            <p id={yearHintId} className="text-xs text-on-surface-variant">Two consecutive years, from {SETUP_CUTOFF_ACADEMIC_YEAR} on.</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={termId} className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Term</label>
            <select
              id={termId}
              value={termNumber}
              onChange={(event) => { setTermNumber(event.target.value); setServerError(""); }}
              className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
            >
              {TERM_NUMBERS.map((number) => (
                <option key={number} value={number}>Term {number}</option>
              ))}
            </select>
          </div>
          {showProblem && <p id={yearErrorId} role="alert" className="text-sm text-error">{problem}</p>}
          {serverError && (
            <div role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4 text-sm">
              {serverError}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button type="button" onClick={onClose} disabled={pending} className="px-4 py-2 min-h-11 md:min-h-6 disabled:opacity-60">Cancel</button>
          <button
            type="submit"
            disabled={pending || showProblem}
            className="flex items-center gap-2 px-5 py-2 min-h-11 md:min-h-6 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-50"
          >
            <Plus className="w-4 h-4" /> {pending ? "Adding..." : "Add term"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
