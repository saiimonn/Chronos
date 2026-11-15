import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import type { ScheduleValidation } from "../../api/client";
import { formatCompleteness } from "../../utils/formatCompleteness";

const CONFLICT_LIMIT = 20;

// Controls withheld by the verdict point here for their reason.
export const ELIGIBILITY_STATUS_ID = "schedule-eligibility-status";

interface ScheduleEligibilityProps {
  validation: ScheduleValidation | null;
  finalized: boolean;
  // Only a master draft can be finalized; any other schedule gets neutral wording.
  finalizable?: boolean;
}

// Only shown, never decided: `export_allowed` is the server's verdict, and the
// reasons listed are the server's own counts and conflict messages.
export default function ScheduleEligibility({ validation, finalized, finalizable = true }: ScheduleEligibilityProps) {
  const action = finalized ? "Export" : "Finalization";
  const blocked = validation !== null && !validation.export_allowed;

  const tone = validation === null
    ? "border-outline-variant bg-surface-container-low"
    : validation.export_allowed
      ? "border-emerald-500/30 bg-emerald-500/5"
      : "border-error/30 bg-error-container/20";

  // One live region for every verdict, so a change on the poll updates its
  // text instead of swapping the node (which screen readers often miss). Only
  // the summary sits in it; a long conflict list would be re-announced in full.
  return (
    <section aria-label="Schedule eligibility" className={`border rounded-lg px-4 py-3 text-sm ${tone}`}>
      <p id={ELIGIBILITY_STATUS_ID} role="status" className="flex items-start gap-2">
        {validation === null ? (
          <HelpCircle className="w-4 h-4 mt-0.5 shrink-0 text-on-surface-variant" aria-hidden="true" />
        ) : validation.export_allowed ? (
          <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" aria-hidden="true" />
        ) : (
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-error" aria-hidden="true" />
        )}
        <span>
          {validation === null
            ? `Server validation is unavailable, so ${action.toLowerCase()} eligibility is unknown. Refresh to try again.`
            : validation.export_allowed
              ? <><strong>{finalized ? "Ready to export." : finalizable ? "Ready to finalize." : "Finalization eligible."}</strong> The server reports all {validation.required_class_count} required classes assigned with zero conflict errors.</>
              : <strong>{action} blocked by server validation.</strong>}
        </span>
      </p>
      {blocked && <BlockerReasons validation={validation} />}
    </section>
  );
}

function BlockerReasons({ validation }: { validation: ScheduleValidation }) {
  const required = validation.required_class_count;
  const unassigned = validation.unassigned_class_ids.length;
  const noClasses = required === 0;
  // From integers, not the server's float (29/100 arrives as 28.999…).
  const percent = noClasses ? "0%" : formatCompleteness(((required - unassigned) * 100) / required);
  const shownConflicts = validation.conflicts.slice(0, CONFLICT_LIMIT);
  const hiddenConflicts = validation.conflicts.length - shownConflicts.length;

  return (
    <ul className="mt-2 ml-6 list-disc space-y-1">
      {noClasses && <li>No required classes in this term.</li>}
      {unassigned > 0 && (
        <li>
          {unassigned} of {required} required class{required === 1 ? "" : "es"} unassigned ({percent} complete).
        </li>
      )}
      {validation.hard_conflict_count > 0 && (
        <li>
          {validation.hard_conflict_count} conflict error{validation.hard_conflict_count === 1 ? "" : "s"}:
          <ul className="ml-4 list-[circle] break-words">
            {shownConflicts.map((conflict, index) => (
              <li key={`${conflict.code}-${index}`}>{conflict.message}</li>
            ))}
            {hiddenConflicts > 0 && <li>and {hiddenConflicts} more.</li>}
          </ul>
        </li>
      )}
      {!noClasses && unassigned === 0 && validation.hard_conflict_count === 0 && (
        <li>The server withheld eligibility without a listed reason.</li>
      )}
    </ul>
  );
}
