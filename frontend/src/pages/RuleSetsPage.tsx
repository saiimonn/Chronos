import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  History,
  Layers,
  Loader2,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  ApiError,
  PATTERN_TYPES,
  PATTERN_TYPE_LABEL,
  api,
  isEmptyRuleOutcome,
  isTerminalJobState,
  type ARMJobStatusResponse,
  type ArmStreamThresholds,
  type ArmThresholds,
  type HistoricalTermOption,
  type MiningSummary,
  type PatternType,
  type RuleSetDetail,
  type RuleSetFacultyCoverage,
  type RuleSetFacultyCoverageItem,
  type RuleSetRule,
  type RuleSetRulePage,
  type RuleSetStatus,
  ruleSetVersionName,
  type RuleSetStreamRules,
  type RuleSetSummary,
  type RuleSetThreshold,
  type RuleSortOrder,
} from "../api/client";
import LoadError from "../components/LoadError";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import Modal from "../components/modals/Modal";
import {
  FROZEN_THRESHOLDS,
  THRESHOLD_FIELD_LABEL,
  thresholdDeviations,
} from "../config/frozenThresholds";
import { meetsAcceptable, ruleMetricGuide, type RuleMetric } from "../config/ruleMetrics";
import { usePeriodicSync } from "../hooks/usePeriodicSync";

// A mining run is minutes of work, not seconds, so the job is polled slower than
// the five-second term SLA the rest of the application syncs at.
const JOB_POLL_INTERVAL_MS = 2000;

// `RuleSet.notes` is VARCHAR(500) and the submission schema bounds the request at
// the same length, so the field cannot accept text the draft could not be saved
// with.
const NOTES_MAX_LENGTH = 500;

function defaultThresholds(): ArmThresholds {
  return structuredClone(FROZEN_THRESHOLDS);
}

const STATUS_LABEL: Record<RuleSetStatus, string> = {
  draft: "Draft",
  active: "Active",
  superseded: "Superseded",
};

const STATUS_STYLE: Record<RuleSetStatus, string> = {
  draft: "bg-tertiary-fixed text-on-tertiary-fixed-variant border-tertiary-fixed-dim",
  active: "bg-primary-fixed text-on-primary-fixed-variant border-primary-fixed-dim",
  superseded: "bg-surface-container-highest text-on-surface-variant border-outline-variant",
};

function formatTimestamp(value: string | null): string {
  if (!value) return "Not activated";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function describeTerm(term: { academic_year: string; term_number: number }): string {
  return `AY ${term.academic_year} · Term ${term.term_number}`;
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

function StatusBadge({ status }: { status: RuleSetStatus }) {
  return (
    <span className={`inline-block px-2.5 py-1 rounded-full border text-xs font-label-md uppercase tracking-wider ${STATUS_STYLE[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

/**
 * The banner that reports one mining run from submission to outcome. Every
 * terminal state gets its own sentence: a run that retained no rules is a
 * legitimate result of the thresholds chosen, not a failure of the system, and
 * an administrator who cannot tell the two apart will re-run the same job.
 */
function JobBanner({ job, draftVersion }: { job: ARMJobStatusResponse; draftVersion: string | null }) {
  const running = !isTerminalJobState(job.status);

  let tone = "border-outline-variant bg-surface-container-low text-on-surface-variant";
  let heading = "Mining run queued";
  let detail = "Waiting for the local worker to claim the job.";

  if (job.status === "running") {
    heading = "Mining run in progress";
    detail = "Association rules are being extracted from the selected history.";
  } else if (job.status === "succeeded") {
    tone = "border-primary/30 bg-primary-fixed/40 text-on-surface";
    heading = "Mining run succeeded";
    detail = draftVersion
      ? `Draft version ${draftVersion} is ready for review. It stays a draft until you validate and activate it.`
      : "A draft rule set was created. It stays a draft until you validate and activate it.";
  } else if (job.status === "timed_out") {
    tone = "border-error/30 bg-error-container/40 text-on-error-container";
    heading = "Mining run timed out";
    detail = "The run exceeded its configured time limit. Narrow the selected history or raise the thresholds and try again. The active rule set is unchanged.";
  } else if (isEmptyRuleOutcome(job)) {
    tone = "border-tertiary-fixed-dim bg-tertiary-fixed/50 text-on-tertiary-fixed-variant";
    heading = "Mining run retained no rules";
    detail = "No pattern in the selected history met the thresholds, so no draft version was created. The active rule set is unchanged.";
  } else if (job.status === "failed") {
    tone = "border-error/30 bg-error-container/40 text-on-error-container";
    heading = "Mining run failed";
    detail = `${job.error?.message ?? "The worker reported no reason."} The active rule set is unchanged.`;
  }

  return (
    <section aria-live="polite" className={`border rounded-xl p-4 flex items-start gap-3 ${tone}`}>
      {running ? (
        <Loader2 className="w-5 h-5 shrink-0 mt-0.5 animate-spin" aria-hidden="true" />
      ) : job.status === "succeeded" ? (
        <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
      ) : (
        <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
      )}
      <div className="min-w-0">
        <p className="font-medium">{heading}</p>
        <p className="text-sm">{detail}</p>
      </div>
    </section>
  );
}

// Thresholds are stored as NUMERIC(5, 4), so the browser's own validation has
// to accept four decimal places: at a coarser step it would reject a support of
// 0.0050 or a lift of 1.0050 that the API and the column both take.
const THRESHOLD_STEP = "0.0001";

function ThresholdFieldset({
  pattern,
  values,
  onChange,
}: {
  pattern: PatternType;
  values: ArmStreamThresholds;
  onChange: (next: ArmStreamThresholds) => void;
}) {
  // The legend names the stream, so the four field labels inside can stay short
  // and still be announced unambiguously.
  return (
    <fieldset className="border border-outline-variant rounded-lg p-4">
      <legend className="px-1 font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
        {PATTERN_TYPE_LABEL[pattern]}
      </legend>
      <div className="grid grid-cols-2 gap-3">
        <label className="block space-y-1">
          <span className="text-[11px] uppercase text-on-surface-variant">Min support</span>
          <input
            type="number"
            step={THRESHOLD_STEP}
            min={THRESHOLD_STEP}
            max="1"
            required
            value={values.min_support}
            onChange={(event) => onChange({ ...values, min_support: event.target.value })}
            className="w-full border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest font-mono-data outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-[11px] uppercase text-on-surface-variant">Min confidence</span>
          <input
            type="number"
            step={THRESHOLD_STEP}
            min={THRESHOLD_STEP}
            max="1"
            required
            value={values.min_confidence}
            onChange={(event) => onChange({ ...values, min_confidence: event.target.value })}
            className="w-full border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest font-mono-data outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-[11px] uppercase text-on-surface-variant">Min lift</span>
          <input
            type="number"
            step={THRESHOLD_STEP}
            min="0"
            required
            value={values.min_lift}
            onChange={(event) => onChange({ ...values, min_lift: event.target.value })}
            className="w-full border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest font-mono-data outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-[11px] uppercase text-on-surface-variant">Min term recurrence</span>
          <input
            type="number"
            step="1"
            min="1"
            required
            value={values.min_term_recurrence}
            onChange={(event) =>
              onChange({ ...values, min_term_recurrence: Number(event.target.value) })
            }
            className="w-full border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest font-mono-data outline-none focus:border-primary"
          />
        </label>
      </div>
    </fieldset>
  );
}

function HistoryLinks() {
  return (
    <div className="flex flex-wrap gap-3">
      <Link to="/historical-upload" className="px-4 py-2 rounded bg-surface-tint text-white font-bold">
        Go to Historical Upload
      </Link>
      <Link to="/history" className="px-4 py-2 rounded border border-outline-variant font-bold">
        Go to Completed History
      </Link>
    </div>
  );
}

function MineDraftDialog({
  onClose,
  onSubmitted,
}: {
  onClose: () => void;
  onSubmitted: (jobId: string) => void;
}) {
  const [terms, setTerms] = useState<HistoricalTermOption[]>([]);
  const [loading, setLoading] = useState(true);
  // True once any read has succeeded, so a later failed poll cannot undo what
  // the server has already said about the history.
  const [loaded, setLoaded] = useState(false);
  const emptyState = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const wasEmpty = useRef(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [thresholds, setThresholds] = useState<ArmThresholds>(defaultThresholds);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [termsError, setTermsError] = useState("");
  const [submissionError, setSubmissionError] = useState("");

  usePeriodicSync(
    async ({ isStale }) => {
      try {
        const response = await api.listHistoricalTerms();
        if (isStale()) return;
        setTerms(response.items);
        setLoaded(true);
        setTermsError("");
      } catch (caught: unknown) {
        if (isStale()) return;
        setTermsError(messageOf(caught, "Could not load historical terms."));
      } finally {
        if (!isStale()) setLoading(false);
      }
    },
    { enabled: true, intervalMs: JOB_POLL_INTERVAL_MS * 30 },
  );

  function toggle(snapshotId: string) {
    setSelected((current) =>
      current.includes(snapshotId)
        ? current.filter((id) => id !== snapshotId)
        : [...current, snapshotId],
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected.length === 0) {
      setSubmissionError("Select at least one historical term to mine.");
      return;
    }
    setSubmitting(true);
    setSubmissionError("");
    try {
      const accepted = await api.submitArmJob({
        selected_snapshot_ids: selected,
        thresholds,
        notes: notes.trim() === "" ? null : notes.trim(),
      });
      onSubmitted(accepted.job_id);
    } catch (caught: unknown) {
      setSubmissionError(messageOf(caught, "Could not start the mining run."));
    } finally {
      setSubmitting(false);
    }
  }

  // Once the submission is in flight the run is already being created on the
  // server. Dismissing here would read as calling it off while the page went on
  // to track a run the administrator believed they had cancelled, so every exit
  // is closed until the request answers.
  const deviations = thresholdDeviations(thresholds);
  const dismiss = submitting ? () => {} : onClose;
  // Nothing is known to be missing while the first load is pending or has
  // failed, so the empty state needs a successful read that returned no terms.
  const noHistory = loaded && terms.length === 0;
  const noUsableTerm = loaded && !terms.some((term) => term.eligible_streams.length > 0);
  const canStart = selected.length > 0;
  const startHint = canStart
    ? ""
    : loading
      ? "Loading historical terms..."
      : !loaded
        ? "Historical terms could not be loaded, so mining cannot start yet."
        : noUsableTerm
          ? "No captured term can feed a stream yet, so there is nothing to mine."
          : "Select at least one historical term to enable mining.";

  // The form unmounts when the empty state takes over, which would drop focus
  // to the page behind the dialog, so focus moves to the state that replaced it.
  // The reverse (terms arriving by a later poll) removes the focused empty state,
  // so focus moves into the form, but only if it was dropped to the page and not
  // moved elsewhere in the dialog by the administrator.
  useEffect(() => {
    if (noHistory) emptyState.current?.focus();
    else if (wasEmpty.current && document.activeElement === document.body) {
      formRef.current
        ?.querySelector<HTMLElement>("input:not([disabled]), button:not([disabled])")
        ?.focus();
    }
    wasEmpty.current = noHistory;
  }, [noHistory]);

  return (
    <Modal
      title="Mine a new candidate rule set"
      subtitle="The run produces a draft version. Nothing is activated automatically."
      onClose={dismiss}
      width="max-w-4xl"
    >
      {noHistory ? (
        <>
        <div
          ref={emptyState}
          tabIndex={-1}
          role="status"
          className="p-6 space-y-4 outline-none min-h-0 overflow-y-auto"
        >
          <p className="text-on-surface-variant">
            No historical terms have been captured yet, so there is nothing to mine. Import a
            reviewed historical bundle, or capture a finalized completed term after it is approved.
          </p>
          <HistoryLinks />
        </div>
        {termsError && <p role="alert" className="px-6 pb-6 text-sm text-error">{termsError}</p>}
        </>
      ) : (
      <form ref={formRef} onSubmit={submit} className="flex flex-col min-h-0">
        <div className="p-6 space-y-6 overflow-y-auto">
          <section className="space-y-3">
            <h3 className="font-headline-md text-lg">Historical terms</h3>
            <p className="text-sm text-on-surface-variant">
              Each stream may only mine terms from its own earliest academic year onwards, so a
              selected term contributes to the streams listed beside it.
            </p>
            {loading ? (
              <p className="text-on-surface-variant">Loading historical terms...</p>
            ) : (
              <ul className="space-y-2">
                {terms.map((term) => {
                  // A term no stream can draw on contributes nothing to a run, so
                  // it is shown as unusable rather than offered and then refused.
                  const unusable = term.eligible_streams.length === 0;
                  return (
                    <li key={term.snapshot_id}>
                      <label
                        className={`flex items-start gap-3 border border-outline-variant rounded-lg p-3 ${
                          unusable
                            ? "opacity-60 cursor-not-allowed"
                            : "hover:border-primary cursor-pointer"
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-1"
                          disabled={unusable}
                          checked={selected.includes(term.snapshot_id)}
                          onChange={() => toggle(term.snapshot_id)}
                        />
                        <span className="min-w-0">
                          <span className="block font-medium">{describeTerm(term)}</span>
                          <span className="block text-sm text-on-surface-variant">
                            {term.record_count} record{term.record_count === 1 ? "" : "s"}
                            {" · "}
                            {unusable
                              ? "eligible for no stream — cannot be mined"
                              : `eligible for ${term.eligible_streams
                                  .map((stream) => PATTERN_TYPE_LABEL[stream])
                                  .join(", ")}`}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="space-y-3">
            <h3 className="font-headline-md text-lg">Thresholds</h3>
            <p className="text-sm text-on-surface-variant">
              Applied per stream during mining and recorded on the version that comes out, where
              they can never be edited again.
            </p>
            <div className="grid md:grid-cols-2 gap-4">
              {PATTERN_TYPES.map((pattern) => (
                <ThresholdFieldset
                  key={pattern}
                  pattern={pattern}
                  values={thresholds[pattern]}
                  onChange={(next) => setThresholds((current) => ({ ...current, [pattern]: next }))}
                />
              ))}
            </div>
            {deviations.length > 0 && (
              <div
                role="status"
                className="border border-tertiary-fixed-dim bg-tertiary-fixed text-on-tertiary-fixed-variant rounded-lg p-4 space-y-2"
              >
                <p className="font-medium">
                  These thresholds differ from the values frozen for the thesis (CHR-99).
                </p>
                <ul className="list-disc pl-5 text-sm">
                  {deviations.map((d) => (
                    <li key={`${d.pattern}-${d.field}`}>
                      {PATTERN_TYPE_LABEL[d.pattern]} {THRESHOLD_FIELD_LABEL[d.field]}{" "}
                      {d.entered === "" ? "(blank)" : d.entered} differs from the frozen thesis
                      value {d.frozen}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  onClick={() => setThresholds(defaultThresholds())}
                  className="px-3 py-1.5 rounded border border-outline-variant bg-surface-container-lowest"
                >
                  Reset to frozen thresholds
                </button>
              </div>
            )}
          </section>

          <label className="block space-y-1.5">
            <span className="font-label-md text-[11px] uppercase text-on-surface-variant">Notes</span>
            <textarea
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              maxLength={NOTES_MAX_LENGTH}
              rows={3}
              className="w-full border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest outline-none focus:border-primary"
            />
          </label>

          {submissionError && (
            <p role="alert" className="text-sm text-error">{submissionError}</p>
          )}
        </div>
        {termsError && (
          <p role="alert" className="px-6 pb-3 text-sm text-error shrink-0">{termsError}</p>
        )}
        <div className="flex flex-wrap justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
          <button
            type="button"
            onClick={dismiss}
            disabled={submitting}
            className="px-4 py-2 disabled:opacity-60"
          >
            Cancel
          </button>
          <div className="min-w-0 flex-1 basis-60 self-center space-y-2 text-sm text-on-surface-variant">
            <p id="start-mining-hint" role="status">
              {startHint}
            </p>
            {loaded && noUsableTerm && <HistoryLinks />}
          </div>
          <button
            disabled={submitting || loading || !canStart}
            aria-describedby={startHint ? "start-mining-hint" : undefined}
            className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60"
          >
            {submitting ? "Starting..." : "Start mining run"}
          </button>
        </div>
      </form>
      )}
    </Modal>
  );
}

interface PendingTransition {
  ruleSet: RuleSetSummary;
  action: "activate" | "rollback";
}

/** The outcome of one inventory read.
 *
 * Reads are sequenced, so a caller has to be able to tell "these are the current
 * versions" from "something newer already answered this" and from "the read
 * failed". Items are carried only in the first case.
 */
type InventoryRead =
  | { outcome: "loaded"; items: RuleSetSummary[] }
  | { outcome: "stale" }
  | { outcome: "failed" };

function ConfirmTransitionDialog({
  pending,
  activeVersion,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  pending: PendingTransition;
  activeVersion: string | null;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const activating = pending.action === "activate";
  // While the request is in flight the dialog is the only thing on screen that
  // may be interacted with. Dismissing it would put the inventory back within
  // reach, and a version inspected there would then be replaced by the response
  // to a transition the administrator had already walked away from.
  const dismiss = busy ? () => {} : onCancel;
  const supersededNote =
    activeVersion === null
      ? "No version is active right now, so nothing is superseded."
      : `Version ${activeVersion} becomes superseded and stops guiding the genetic algorithm.`;

  return (
    <Modal
      title={activating ? `Validate and activate version ${ruleSetVersionName(pending.ruleSet)}?` : `Roll back to version ${ruleSetVersionName(pending.ruleSet)}?`}
      onClose={dismiss}
    >
      <div className="p-6 space-y-3">
        <p>
          {activating
            ? `Version ${ruleSetVersionName(pending.ruleSet)} is checked for structural integrity and, if it passes, becomes the single active rule set.`
            : `Version ${ruleSetVersionName(pending.ruleSet)} is re-checked for structural integrity and, if it passes, becomes the active rule set again.`}
        </p>
        <p className="text-on-surface-variant">{supersededNote}</p>
        <p className="text-on-surface-variant">
          Every version is kept. The rules inside them are never changed by this action.
        </p>
        {error && <p role="alert" className="text-sm text-error">{error}</p>}
      </div>
      <div className="flex justify-end gap-3 p-5 border-t border-outline-variant bg-surface-bright shrink-0">
        <button
          type="button"
          onClick={dismiss}
          disabled={busy}
          className="px-4 py-2 disabled:opacity-60"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="px-5 py-2 rounded bg-surface-tint text-white font-bold disabled:opacity-60"
        >
          {busy ? "Working..." : activating ? `Activate version ${ruleSetVersionName(pending.ruleSet)}` : `Roll back to version ${ruleSetVersionName(pending.ruleSet)}`}
        </button>
      </div>
    </Modal>
  );
}

// Worker codes such as `unknown_faculty` or `no_transactions` read as prose.
function humanize(code: string): string {
  const words = code.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Offline imports record `artifact sha256=<hex>` in their notes so the handoff
// can verify the package. The hash stays in the database for that check; the
// review screen leaves it out because it means nothing to the administrator.
function displayNotes(notes: string | null): string {
  return (notes ?? "")
    .replace(/[;,]?\s*artifact sha256=[0-9a-f]{64}/gi, "")
    .trim();
}

const SECTION_HEADING = "font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant";

/**
 * Where a version came from: who asked for it, which run mined it, from which
 * captured snapshots, and what that run counted, excluded and kept. Imported
 * packages and versions mined before job tracking look identical in the data,
 * so a version with no recorded run says so without claiming which it was.
 */
function ProvenanceSection({ detail }: { detail: RuleSetDetail }) {
  const requester = detail.requested_by_username
    ?? (detail.requested_by_user_id ? `Unknown account (${detail.requested_by_user_id.slice(0, 8)})` : "—");
  const snapshots = detail.source_snapshots ?? [];
  const sourceJob = detail.source_job_id ?? detail.job_id;

  return (
    <section aria-label="Provenance" className="space-y-3">
      <h3 className={SECTION_HEADING}>Provenance</h3>
      {!sourceJob ? (
        <p className="text-sm text-on-surface-variant">
          No mining run is recorded for this version (imported package, or mined before job tracking).
        </p>
      ) : !detail.source_job_id && (
        <p className="text-sm text-on-surface-variant">
          Mined before provenance tracking: the requester, source snapshots and mining summary were not recorded.
        </p>
      )}
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
        <div>
          <dt className="text-on-surface-variant">Requested by</dt>
          <dd className="text-on-surface">{requester}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-on-surface-variant">Source job</dt>
          <dd className="font-mono-data break-all">{sourceJob ?? "—"}</dd>
        </div>
      </dl>
      <div className="space-y-2">
        <h4 className="text-sm text-on-surface-variant">Source snapshots</h4>
        {snapshots.length === 0 ? (
          <p className="text-sm text-on-surface-variant">—</p>
        ) : (
          <ul aria-label="Source snapshots" className="flex flex-wrap gap-2 max-h-40 overflow-y-auto">
            {snapshots.map((snapshot) => (
              <li key={snapshot.snapshot_id} className="px-2.5 py-1 rounded-full border border-outline-variant bg-surface-container-low text-sm">
                {describeTerm(snapshot)}
              </li>
            ))}
          </ul>
        )}
      </div>
      <MiningSummaryTable summary={detail.mining_summary} />
    </section>
  );
}

function MiningSummaryTable({ summary }: { summary: MiningSummary | null }) {
  if (!summary) {
    return <p className="text-sm text-on-surface-variant">No mining summary was recorded for this version.</p>;
  }
  const order = (stream: string) => PATTERN_TYPES.indexOf(stream as PatternType);
  const streams = [...(summary.streams ?? [])].sort((a, b) => order(a.stream) - order(b.stream));

  return (
    <div className="space-y-2">
      <p className="text-sm">
        {summary.input_record_count} input records · {summary.mined_rule_count} rules mined ·{" "}
        {summary.persisted_rule_count} rules persisted
      </p>
      <div className="overflow-x-auto border border-outline-variant rounded-xl">
        <table className="w-full text-sm">
          <caption className="sr-only">
            What the mining run counted, excluded and persisted for each pattern stream.
          </caption>
          <thead className="bg-surface-container-low text-on-surface-variant">
            <tr>
              <th scope="col" className="text-left px-4 py-2.5 font-label-md">Stream</th>
              <th scope="col" className="text-right px-4 py-2.5 font-label-md">Terms</th>
              <th scope="col" className="text-right px-4 py-2.5 font-label-md">Transactions</th>
              <th scope="col" className="text-right px-4 py-2.5 font-label-md">Mined</th>
              <th scope="col" className="text-right px-4 py-2.5 font-label-md">Persisted</th>
              <th scope="col" className="text-left px-4 py-2.5 font-label-md">Excluded</th>
            </tr>
          </thead>
          <tbody>
            {streams.map((item) => {
              const exclusions = Object.entries(item.excluded_rule_counts ?? {}).filter(([, count]) => count > 0);
              return (
                <tr key={item.stream} className="border-t border-outline-variant align-top">
                  <th scope="row" className="text-left px-4 py-2.5 font-medium">
                    {PATTERN_TYPE_LABEL[item.stream] ?? item.stream}
                    {item.skipped_reason && (
                      <span className="block text-xs font-normal text-on-surface-variant">
                        Skipped: {humanize(item.skipped_reason)}
                      </span>
                    )}
                  </th>
                  <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{item.training_term_count}</td>
                  <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{item.transaction_count}</td>
                  <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{item.mined_rule_count}</td>
                  <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{item.persisted_rule_count}</td>
                  <td className="text-left px-4 py-2.5">
                    {exclusions.length === 0
                      ? "None"
                      : exclusions.map(([reason, count]) => `${humanize(reason)}: ${count}`).join(", ")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RuleSetDetailPanel({ detail }: { detail: RuleSetDetail }) {
  const streamCounts = new Map(detail.streams.map((stream) => [stream.pattern_type, stream.rule_count]));
  const thresholds = new Map(detail.thresholds.map((threshold) => [threshold.pattern_type, threshold]));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-headline-md text-2xl">Version {ruleSetVersionName(detail)}</h2>
          <p className="text-sm text-on-surface-variant">Generated {formatTimestamp(detail.generated_at)}</p>
          <p className="text-sm text-on-surface-variant">Activated {formatTimestamp(detail.activated_at)}</p>
        </div>
        <StatusBadge status={detail.status} />
      </div>

      <section className="space-y-2">
        <h3 className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Notes</h3>
        <p className="text-on-surface">{displayNotes(detail.notes) || "No notes were recorded for this version."}</p>
      </section>

      <section className="space-y-2">
        <h3 className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Included terms</h3>
        {detail.included_terms.length === 0 ? (
          <p className="text-on-surface-variant">No historical terms are recorded against this version.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {detail.included_terms.map((term) => (
              <li key={term.term_id} className="px-2.5 py-1 rounded-full border border-outline-variant bg-surface-container-low text-sm">
                {describeTerm(term)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
          Stream summaries and raw thresholds
        </h3>
        <div className="overflow-x-auto border border-outline-variant rounded-xl">
          <table className="w-full text-sm">
            <caption className="sr-only">
              Retained rule count and the raw mining thresholds recorded for each pattern stream.
            </caption>
            <thead className="bg-surface-container-low text-on-surface-variant">
              <tr>
                <th scope="col" className="text-left px-4 py-2.5 font-label-md">Stream</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Rules</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Min support</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Min confidence</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Min lift</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Min recurrence</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Earliest AY</th>
              </tr>
            </thead>
            <tbody>
              {PATTERN_TYPES.map((pattern) => {
                const threshold = thresholds.get(pattern);
                return (
                  <tr key={pattern} className="border-t border-outline-variant">
                    <th scope="row" className="text-left px-4 py-2.5 font-medium">{PATTERN_TYPE_LABEL[pattern]}</th>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{streamCounts.get(pattern) ?? 0}</td>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{threshold?.min_support ?? "—"}</td>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{threshold?.min_confidence ?? "—"}</td>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{threshold?.min_lift ?? "—"}</td>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{threshold?.min_term_recurrence ?? "—"}</td>
                    <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{threshold?.earliest_academic_year ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-sm text-on-surface-variant">
          A version is immutable: its extracted rules and thresholds are read-only once mined.
        </p>
      </section>

      <ProvenanceSection detail={detail} />
    </div>
  );
}

const RULES_PAGE_SIZE = 25;

// A rule is a historical tendency, not a condition the GA must obey, so its two
// sides are named for what they hold rather than read as "if ... then".
const RULE_COLUMN_LABEL: Record<PatternType, [string, string]> = {
  teacher_subject: ["Teacher", "Subject usually taught"],
  teacher_day: ["Teacher", "Days usually taught"],
  teacher_time: ["Teacher", "Time slot usually taught"],
  teacher_subject_room: ["Teacher and subject", "Room usually used"],
};

// Which metric each coverage column shows, its label, and the flag that says
// whether it met this version's threshold.
const COVERAGE_METRICS = [
  { key: "support", meets: "meets_support", label: "Support" },
  { key: "confidence", meets: "meets_confidence", label: "Confidence" },
  { key: "lift", meets: "meets_lift", label: "Lift" },
  { key: "term_recurrence", meets: "meets_term_recurrence", label: "Terms" },
] as const;

function describePattern(item: RuleSetFacultyCoverageItem): string {
  if (item.room_code) return item.subject_code ? `${item.subject_code} in ${item.room_code}` : item.room_code;
  return item.subject_code ?? item.meeting_days ?? item.time_slot ?? "—";
}

function CoverageMetricCell({ value, meets }: { value: string | number; meets: boolean }) {
  return (
    <td
      className={`text-right px-4 py-2.5 whitespace-nowrap font-mono-data tabular-nums ${
        meets ? "text-emerald-700 dark:text-emerald-300" : "text-error"
      }`}
    >
      <span>{value}</span>
      <span className="sr-only">{meets ? " (meets threshold)" : " (below threshold)"}</span>
    </td>
  );
}

const GOOD_TONE = "text-emerald-700 dark:text-emerald-300";

/**
 * Two pictures of one stream's coverage. Left: which pattern each teacher's
 * strongest candidate points at (solid green link = became a rule, dashed red
 * = fell short). Right: confidence against lift per teacher, with this
 * version's thresholds drawn in, so it is visible how far each one missed.
 * Hover any line or dot for its teacher and values.
 */
function CoverageCharts({
  items,
  threshold,
  stream,
}: {
  items: RuleSetFacultyCoverageItem[];
  threshold: RuleSetThreshold | undefined;
  stream: PatternType;
}) {
  // One subject at a time keeps the link diagram readable; streams without a
  // subject (days, time slots) show every teacher.
  const subjects = [...new Set(items.map((item) => item.subject_code).filter((code): code is string => !!code))].sort();
  const [chosenSubject, setChosenSubject] = useState("");
  const subject = subjects.includes(chosenSubject) ? chosenSubject : (subjects[0] ?? "");
  const linked = subject ? items.filter((item) => item.subject_code === subject) : items;
  // Sorting by pattern keeps links near-parallel, so the diagram stays legible.
  const rows = [...linked].sort(
    (a, b) => describePattern(a).localeCompare(describePattern(b)) || a.faculty_name.localeCompare(b.faculty_name),
  );
  const patterns = [...new Set(rows.map(describePattern))];
  const ROW = 22;
  const height = Math.max(rows.length, 1) * ROW + 8;
  const patternGap = height / Math.max(patterns.length, 1);
  const patternY = (label: string) => patternGap * (patterns.indexOf(label) + 0.5);
  const LEFT = 220;
  const RIGHT = 460;

  // Scatter: confidence 0..1 on x, lift on y from 0 to just above the largest value.
  const W = 320;
  const H = 240;
  const PAD = 36;
  const minConfidence = Number(threshold?.min_confidence ?? 0);
  const minLift = Number(threshold?.min_lift ?? 0);
  const topLift = Math.max(minLift, ...items.map((item) => Number(item.lift)), 1);
  const maxLift = topLift * 1.1;
  const x = (value: number) => PAD + value * (W - PAD - 8);
  const y = (value: number) => H - PAD - (value / maxLift) * (H - PAD - 8);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <figure className="border border-outline-variant rounded-xl p-3 space-y-2 min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <figcaption className="text-sm font-label-md">
            Teacher → {RULE_COLUMN_LABEL[stream][1].toLowerCase()}
          </figcaption>
          {subjects.length > 0 && (
            <label className="flex items-center gap-2 text-sm">
              <span>Subject</span>
              <select
                value={subject}
                onChange={(event) => setChosenSubject(event.target.value)}
                className="border border-outline-variant rounded px-2 py-1 bg-surface-container-lowest"
              >
                {subjects.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <div className="max-h-[28rem] overflow-auto">
          <svg
            role="img"
            aria-label={`Links from each teacher to their strongest ${PATTERN_TYPE_LABEL[stream]} pattern; solid green links became rules, dashed red did not.`}
            viewBox={`0 0 680 ${height}`}
            className="w-full"
            style={{ minWidth: 520 }}
          >
            {rows.map((item, index) => {
              const ty = ROW * index + ROW / 2 + 4;
              const py = patternY(describePattern(item));
              const tone = item.has_rule ? GOOD_TONE : "text-error";
              const inactive = item.is_active === false;
              return (
                <g key={item.source_faculty_key} aria-hidden="true">
                  <title>
                    {`${item.faculty_name} → ${describePattern(item)}: ${item.has_rule ? "has rule" : "no rule"}${
                      inactive ? " (inactive)" : ""
                    }`}
                  </title>
                  <path
                    d={`M${LEFT + 6},${ty} C${(LEFT + RIGHT) / 2},${ty} ${(LEFT + RIGHT) / 2},${py} ${RIGHT - 6},${py}`}
                    className={`${tone} stroke-current`}
                    fill="none"
                    strokeWidth={2}
                    strokeDasharray={item.has_rule ? undefined : "4 3"}
                    opacity={0.8}
                  />
                  <circle cx={LEFT + 6} cy={ty} r={3} className={`${tone} fill-current`} />
                  <text
                    x={LEFT}
                    y={ty + 4}
                    textAnchor="end"
                    fontSize={12}
                    className={`fill-current ${inactive ? "text-error font-semibold" : "text-on-surface"}`}
                  >
                    {item.faculty_name.length > 30 ? `${item.faculty_name.slice(0, 29)}…` : item.faculty_name}
                  </text>
                </g>
              );
            })}
            {patterns.map((label) => (
              <g key={label} aria-hidden="true">
                <circle cx={RIGHT - 6} cy={patternY(label)} r={4} className="fill-current text-on-surface-variant" />
                <text x={RIGHT + 4} y={patternY(label) + 4} fontSize={12} className="fill-current text-on-surface">
                  {label.length > 28 ? `${label.slice(0, 27)}…` : label}
                </text>
              </g>
            ))}
          </svg>
        </div>
        <p className="text-xs text-on-surface-variant">
          <span className={GOOD_TONE}>━ has rule</span> · <span className="text-error">┅ no rule</span> ·{" "}
          <span className="text-error font-semibold">red name</span> = inactive teacher
        </p>
      </figure>

      <figure className="border border-outline-variant rounded-xl p-3 space-y-2 min-w-0">
        <figcaption className="text-sm font-label-md">Confidence vs lift</figcaption>
        <svg
          role="img"
          aria-label="Each teacher's confidence against lift, with the version's minimum confidence and lift drawn as dashed lines."
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
        >
          <line x1={PAD} y1={H - PAD} x2={W - 8} y2={H - PAD} className="stroke-current text-outline-variant" />
          <line x1={PAD} y1={8} x2={PAD} y2={H - PAD} className="stroke-current text-outline-variant" />
          {threshold && (
            <g className="stroke-current text-on-surface-variant" strokeDasharray="3 3" aria-hidden="true">
              <line x1={x(minConfidence)} y1={8} x2={x(minConfidence)} y2={H - PAD} />
              <line x1={PAD} y1={y(minLift)} x2={W - 8} y2={y(minLift)} />
            </g>
          )}
          <g fontSize={10} className="fill-current text-on-surface-variant" aria-hidden="true">
            <text x={PAD} y={H - PAD + 14} textAnchor="middle">0</text>
            <text x={W - 8} y={H - PAD + 14} textAnchor="end">1</text>
            <text x={(PAD + W) / 2} y={H - 6} textAnchor="middle">confidence</text>
            <text x={PAD - 4} y={y(0) + 3} textAnchor="end">0</text>
            <text x={PAD - 4} y={y(topLift) + 3} textAnchor="end">{topLift.toFixed(1)}</text>
            <text x={10} y={(H - PAD) / 2} textAnchor="middle" transform={`rotate(-90 10 ${(H - PAD) / 2})`}>
              lift
            </text>
          </g>
          {items.map((item) => (
            <circle
              key={item.source_faculty_key}
              cx={x(Number(item.confidence))}
              cy={y(Number(item.lift))}
              r={5}
              strokeWidth={2}
              className={`stroke-current ${item.has_rule ? `${GOOD_TONE} fill-current` : "text-error"}`}
              fill={item.has_rule ? undefined : "none"}
              fillOpacity={0.7}
            >
              <title>{`${item.faculty_name}: confidence ${item.confidence}, lift ${item.lift}`}</title>
            </circle>
          ))}
        </svg>
        <p className="text-xs text-on-surface-variant">
          <span className={GOOD_TONE}>● has rule</span> · <span className="text-error">○ no rule</span> · dashed lines =
          minimum confidence and lift. A rule also needs support and terms to pass.
        </p>
      </figure>
    </div>
  );
}

/**
 * Every teacher in a stream's history, listed the same way: their best
 * candidate pattern and its four metrics, each green when it meets this
 * version's threshold and red when it does not. A teacher has a rule only when
 * all four are green.
 */
function FacultyCoveragePanel({ ruleSetId, thresholds }: { ruleSetId: string; thresholds: RuleSetThreshold[] }) {
  const [stream, setStream] = useState<PatternType>("teacher_subject");
  const [loaded, setLoaded] = useState<{ stream: PatternType; coverage: RuleSetFacultyCoverage } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setError("");
    api
      .getRuleSetFacultyCoverage(ruleSetId, stream)
      .then((response) => {
        if (current) setLoaded({ stream, coverage: response });
      })
      .catch((caught: unknown) => {
        if (current) setError(messageOf(caught, "Could not load teacher coverage for this stream."));
      });
    return () => {
      current = false;
    };
  }, [ruleSetId, stream, attempt]);

  const coverage = loaded?.stream === stream ? loaded.coverage : null;
  const threshold = thresholds.find((entry) => entry.pattern_type === stream);

  return (
    <section aria-label="Teacher coverage" className="bg-surface-container-lowest border border-outline-variant rounded-xl shadow-sm">
      <details className="group">
      <summary className="cursor-pointer list-none p-6 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-headline-md text-xl">
          <span aria-hidden="true" className="inline-block mr-2 transition-transform group-open:rotate-90">▸</span>
          Teacher coverage
        </h2>
        {coverage ? (
          <p className="text-sm">
            {PATTERN_TYPE_LABEL[stream]}: {coverage.teacher_count} teachers ·{" "}
            <span className={`font-medium ${GOOD_TONE}`}>{coverage.with_rule_count} with a rule</span>
            {" · "}
            <span className="font-medium text-error">{coverage.without_rule_count} without</span>
            {" · "}
            {coverage.version_with_rule_count} teachers have a rule in at least one stream
          </p>
        ) : (
          <p className="text-sm text-on-surface-variant">{error ? "Coverage did not load" : "Loading…"}</p>
        )}
      </summary>
      <div className="px-6 pb-6 space-y-4">
      <div className="space-y-1">
        <p className="text-sm text-on-surface-variant">
          Every teacher in this stream&apos;s history, with their strongest pattern. Each metric is green when it meets
          this version&apos;s threshold and red when it does not; a teacher gets a rule only when all four are green.
          Teachers without a rule are still scheduled, just without historical guidance.
        </p>
      </div>

      <div role="group" aria-label="Coverage stream" className="flex flex-wrap gap-2">
        {PATTERN_TYPES.map((pattern) => (
          <button
            key={pattern}
            type="button"
            aria-pressed={pattern === stream}
            onClick={() => setStream(pattern)}
            className={`px-3 py-1.5 rounded-full border text-sm ${
              pattern === stream
                ? "bg-primary text-on-primary border-primary"
                : "bg-surface-container-low border-outline-variant text-on-surface"
            }`}
          >
            {PATTERN_TYPE_LABEL[pattern]}
          </button>
        ))}
      </div>

      {threshold && (
        <p className="text-xs text-on-surface-variant font-mono-data">
          Thresholds: support ≥ {threshold.min_support} · confidence ≥ {threshold.min_confidence} · lift ≥{" "}
          {threshold.min_lift} · terms ≥ {threshold.min_term_recurrence}
        </p>
      )}

      {error ? (
        <div className="flex flex-wrap items-center gap-3">
          <p role="alert" className="text-sm text-error">{error}</p>
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
            className="px-3 py-1.5 rounded-lg border border-outline-variant text-sm"
          >
            Retry
          </button>
        </div>
      ) : !coverage ? (
        <p className="text-on-surface-variant">Loading teacher coverage...</p>
      ) : (
        <>
          {!coverage.metrics_recorded && (
            <p className="text-sm text-on-surface-variant">
              This version was created before per-teacher metrics were recorded, so only teachers with a rule are listed.
            </p>
          )}
          {coverage.items.length === 0 ? (
            <p className="text-on-surface-variant">No teachers have history in this stream.</p>
          ) : (
            <>
            <CoverageCharts items={coverage.items} threshold={threshold} stream={stream} />
            <div className="overflow-x-auto border border-outline-variant rounded-xl max-h-[32rem] overflow-y-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  {PATTERN_TYPE_LABEL[stream]}: each teacher&apos;s strongest pattern and whether each metric meets its threshold.
                </caption>
                <thead className="bg-surface-container-low text-on-surface-variant sticky top-0">
                  <tr>
                    <th scope="col" className="text-left px-4 py-2.5 font-label-md">Teacher</th>
                    <th scope="col" className="text-left px-4 py-2.5 font-label-md">{RULE_COLUMN_LABEL[stream][1]}</th>
                    <th scope="col" className="text-right px-4 py-2.5 font-label-md">Classes</th>
                    {COVERAGE_METRICS.map((metric) => (
                      <th key={metric.key} scope="col" className="text-right px-4 py-2.5 font-label-md">
                        {metric.label}
                      </th>
                    ))}
                    <th scope="col" className="text-left px-4 py-2.5 font-label-md">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.items.map((item) => (
                    <tr
                      key={item.source_faculty_key}
                      data-has-rule={item.has_rule}
                      className={`border-t border-outline-variant ${item.is_active === false ? "bg-error/10" : ""}`}
                    >
                      <th
                        scope="row"
                        className={`text-left px-4 py-2.5 font-medium ${item.is_active === false ? "text-error" : ""}`}
                      >
                        {item.faculty_name}
                        {!item.on_roster ? (
                          <span className="block text-xs font-normal text-on-surface-variant">Not linked to the faculty roster</span>
                        ) : item.is_active === false ? (
                          <span className="ml-2 align-middle px-1.5 py-0.5 rounded-full bg-error text-on-error text-[10px] font-label-md uppercase tracking-wider">
                            Inactive
                          </span>
                        ) : null}
                      </th>
                      <td className="px-4 py-2.5">{describePattern(item)}</td>
                      <td className="text-right px-4 py-2.5 font-mono-data tabular-nums">{item.offering_count ?? "—"}</td>
                      {COVERAGE_METRICS.map((metric) => (
                        <CoverageMetricCell key={metric.key} value={item[metric.key]} meets={item[metric.meets]} />
                      ))}
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        {item.has_rule ? (
                          <span className="font-medium text-emerald-700 dark:text-emerald-300">Has rule</span>
                        ) : (
                          <span className="font-medium text-error">No rule</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            </>
          )}
        </>
      )}
      </div>
      </details>
    </section>
  );
}

function RuleMetricGuidePanel({ stream, threshold }: { stream: PatternType; threshold: RuleSetThreshold | undefined }) {
  return (
    <details className="border border-outline-variant rounded-xl bg-surface-container-low" open>
      <summary className="cursor-pointer px-4 py-2.5 text-sm font-label-md">
        How to read these metrics ({PATTERN_TYPE_LABEL[stream]})
      </summary>
      <div className="overflow-x-auto border-t border-outline-variant">
        <table className="w-full text-sm">
          <caption className="sr-only">
            Acceptable values and interpretation of each rule metric for the {PATTERN_TYPE_LABEL[stream]} stream.
          </caption>
          <thead className="text-on-surface-variant">
            <tr>
              <th scope="col" className="text-left px-4 py-2 font-label-md">Metric</th>
              <th scope="col" className="text-left px-4 py-2 font-label-md">Range</th>
              <th scope="col" className="text-left px-4 py-2 font-label-md">Acceptable (retained if)</th>
              <th scope="col" className="text-left px-4 py-2 font-label-md">Interpretation</th>
            </tr>
          </thead>
          <tbody>
            {ruleMetricGuide(threshold).map((entry) => (
              <tr key={entry.metric} className="border-t border-outline-variant align-top">
                <th scope="row" className="text-left px-4 py-2 font-label-md whitespace-nowrap">{entry.label}</th>
                <td className="px-4 py-2 font-mono-data tabular-nums whitespace-nowrap">{entry.range}</td>
                <td className="px-4 py-2 font-mono-data tabular-nums">{entry.acceptable}</td>
                <td className="px-4 py-2 text-on-surface-variant">{entry.meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-2 border-t border-outline-variant text-xs text-on-surface-variant">
        Every listed rule already meets this version&apos;s thresholds. Rules are soft guidance: the GA never trades a hard
        constraint for one.
      </p>
    </details>
  );
}

function MetricCell({
  rule,
  metric,
  value,
  threshold,
}: {
  rule: RuleSetRule;
  metric: RuleMetric;
  value: string | number;
  threshold: RuleSetThreshold | undefined;
}) {
  const ok = meetsAcceptable(metric, rule, threshold);
  const tone = ok === undefined ? "" : ok ? "text-emerald-700 dark:text-emerald-300" : "text-error";
  return (
    <td className={`text-right px-4 py-2.5 align-top whitespace-nowrap font-mono-data tabular-nums ${tone}`}>
      <span>{value}</span>
      {ok !== undefined && <span className="sr-only">{ok ? " (acceptable)" : " (below acceptable)"}</span>}
    </td>
  );
}

function RuleSetRulesPanel({
  ruleSetId,
  thresholds,
  streams,
}: {
  ruleSetId: string;
  thresholds: RuleSetThreshold[];
  streams: RuleSetStreamRules[];
}) {
  const [stream, setStream] = useState<PatternType>("teacher_subject");
  const [order, setOrder] = useState<RuleSortOrder>("desc");
  const [offset, setOffset] = useState(0);
  // The page is kept with the query that produced it, so rows are only shown
  // under the stream, sort and range they actually belong to.
  const [loaded, setLoaded] = useState<{ key: string; page: RuleSetRulePage } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const queryKey = `${stream}|${order}|${offset}`;

  useEffect(() => {
    // Stream, sort and page change faster than a read can return; only the
    // latest request may reach the table.
    let current = true;
    setError("");
    api
      .listRuleSetRules(ruleSetId, { patternType: stream, order, limit: RULES_PAGE_SIZE, offset })
      .then((response) => {
        if (current) setLoaded({ key: `${stream}|${order}|${offset}`, page: response });
      })
      .catch((caught: unknown) => {
        if (current) setError(messageOf(caught, "Could not load the rules for this stream."));
      });
    return () => {
      current = false;
    };
  }, [ruleSetId, stream, order, offset, attempt]);

  function selectStream(next: PatternType) {
    setStream(next);
    setOffset(0);
  }

  function toggleOrder() {
    setOrder((previous) => (previous === "desc" ? "asc" : "desc"));
    setOffset(0);
  }

  const page = loaded?.key === queryKey ? loaded.page : null;
  const loading = page === null && !error;
  const total = page?.total ?? 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + RULES_PAGE_SIZE, total);
  const threshold = thresholds.find((entry) => entry.pattern_type === stream);

  return (
    <section aria-label="Extracted rules" className="bg-surface-container-lowest border border-outline-variant rounded-xl shadow-sm">
      <details className="group">
      <summary className="cursor-pointer list-none p-6 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-headline-md text-xl">
          <span aria-hidden="true" className="inline-block mr-2 transition-transform group-open:rotate-90">▸</span>
          Extracted rules
        </h2>
        <p className="text-sm">
          <span className="font-medium">{streams.reduce((sum, entry) => sum + entry.rule_count, 0)} rules mined</span>
          {PATTERN_TYPES.map((pattern) => (
            <span key={pattern} className="text-on-surface-variant">
              {" · "}
              {PATTERN_TYPE_LABEL[pattern]} {streams.find((entry) => entry.pattern_type === pattern)?.rule_count ?? 0}
            </span>
          ))}
        </p>
      </summary>
      <div className="px-6 pb-6 space-y-4">
      <p className="text-sm text-on-surface-variant">
        The candidate rules this version would hand the GA, read-only.
      </p>

      <div role="group" aria-label="Pattern stream" className="flex flex-wrap gap-2">
        {PATTERN_TYPES.map((pattern) => (
          <button
            key={pattern}
            type="button"
            aria-pressed={pattern === stream}
            onClick={() => selectStream(pattern)}
            className={`px-3 py-1.5 rounded-full border text-sm ${
              pattern === stream
                ? "bg-primary text-on-primary border-primary"
                : "bg-surface-container-low border-outline-variant text-on-surface"
            }`}
          >
            {PATTERN_TYPE_LABEL[pattern]}
          </button>
        ))}
      </div>

      {error && (
        <div className="flex flex-wrap items-center gap-3">
          <p role="alert" className="text-sm text-error">{error}</p>
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
            className="px-3 py-1.5 rounded-lg border border-outline-variant text-sm"
          >
            Retry
          </button>
        </div>
      )}

      <RuleMetricGuidePanel stream={stream} threshold={threshold} />

      {loading ? (
        <p className="text-on-surface-variant">Loading rules...</p>
      ) : page && page.items.length === 0 ? (
        <p className="text-on-surface-variant">This stream retained no rules in this version.</p>
      ) : page ? (
        <div className="overflow-x-auto border border-outline-variant rounded-xl">
          <table className="w-full text-sm">
            <caption className="sr-only">
              {PATTERN_TYPE_LABEL[stream]} rules with their mining metrics.
            </caption>
            <thead className="bg-surface-container-low text-on-surface-variant">
              <tr>
                <th scope="col" className="text-left px-4 py-2.5 font-label-md">{RULE_COLUMN_LABEL[stream][0]}</th>
                <th scope="col" className="text-left px-4 py-2.5 font-label-md">{RULE_COLUMN_LABEL[stream][1]}</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Support</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Confidence</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Lift</th>
                <th scope="col" className="text-right px-4 py-2.5 font-label-md">Terms</th>
                <th
                  scope="col"
                  aria-sort={order === "desc" ? "descending" : "ascending"}
                  className="text-right px-4 py-2.5 font-label-md"
                >
                  <button type="button" onClick={toggleOrder} className="font-label-md underline-offset-2 hover:underline">
                    Guidance score {order === "desc" ? "↓" : "↑"}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((rule) => (
                <tr key={rule.rule_id} className="border-t border-outline-variant">
                  <td className="px-4 py-2.5">{rule.antecedent.join(" + ")}</td>
                  <td className="px-4 py-2.5">{rule.consequent.join(" + ")}</td>
                  <MetricCell rule={rule} metric="support" value={rule.support} threshold={threshold} />
                  <MetricCell rule={rule} metric="confidence" value={rule.confidence} threshold={threshold} />
                  <MetricCell rule={rule} metric="lift" value={rule.lift} threshold={threshold} />
                  <MetricCell rule={rule} metric="term_recurrence" value={rule.term_recurrence} threshold={threshold} />
                  <MetricCell rule={rule} metric="guidance_score" value={rule.guidance_score} threshold={threshold} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {total > 0 && (
        <div className="flex items-center justify-between gap-3 text-sm">
          <p className="text-on-surface-variant">
            Showing {first}–{last} of {total}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={loading || offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - RULES_PAGE_SIZE))}
              className="px-3 py-1.5 rounded-lg border border-outline-variant disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={loading || last >= total}
              onClick={() => setOffset((value) => value + RULES_PAGE_SIZE)}
              className="px-3 py-1.5 rounded-lg border border-outline-variant disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
      </div>
      </details>
    </section>
  );
}

export default function RuleSetsPage() {
  // Reconciliation after a settled run outlives the sync that noticed it, so the
  // page tracks its own mount state instead of borrowing a hook's.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const [ruleSets, setRuleSets] = useState<RuleSetSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Mirrors `selectedId` for the mount sync, which must not re-select once the
  // administrator has chosen a version to inspect.
  const selectedIdRef = useRef<string | null>(null);
  const [detail, setDetail] = useState<RuleSetDetail | null>(null);
  // What the panel is showing, mirrored for the periodic sync: it compares the
  // version on screen against the inventory it has just read, and it runs from a
  // timer rather than from a render.
  const detailRef = useRef<RuleSetDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [mining, setMining] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<ARMJobStatusResponse | null>(null);
  const [jobError, setJobError] = useState("");
  const settledJobRef = useRef<string | null>(null);

  const [pending, setPending] = useState<PendingTransition | null>(null);
  const [transitionBusy, setTransitionBusy] = useState(false);
  const [transitionError, setTransitionError] = useState("");

  const activeVersion = (() => { const active = ruleSets.find((ruleSet) => ruleSet.status === "active"); return active ? ruleSetVersionName(active) : null; })();

  // Every inventory read — periodic, post-run, and post-lifecycle — is sequenced
  // against this counter so a slower earlier response can never overwrite a
  // freshly activated or rolled-back status with the state it superseded.
  const inventorySeqRef = useRef(0);
  // The detail panel is sequenced separately: it is requested by its own
  // selections, which interleave with inventory reads rather than follow them.
  const detailSeqRef = useRef(0);

  const applyDetail = useCallback((next: RuleSetDetail | null) => {
    detailRef.current = next;
    setDetail(next);
  }, []);

  const loadRuleSets = useCallback(async (): Promise<InventoryRead> => {
    const seq = ++inventorySeqRef.current;
    const current = () => mountedRef.current && seq === inventorySeqRef.current;
    try {
      const response = await api.listRuleSets();
      // A superseded read returns "stale", never its items: they describe an
      // inventory that something newer has already replaced, and a caller that
      // acted on them would undo that newer state.
      if (!current()) return { outcome: "stale" };
      setRuleSets(response.items);
      setListError("");
      return { outcome: "loaded", items: response.items };
    } catch (caught: unknown) {
      if (!current()) return { outcome: "stale" };
      setListError(messageOf(caught, "Could not load rule set versions."));
      return { outcome: "failed" };
    } finally {
      if (current()) setListLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (ruleSetId: string) => {
    const seq = ++detailSeqRef.current;
    selectedIdRef.current = ruleSetId;
    setSelectedId(ruleSetId);
    setDetailLoading(true);
    setDetailError("");
    const current = () =>
      mountedRef.current &&
      seq === detailSeqRef.current &&
      selectedIdRef.current === ruleSetId;
    try {
      const response = await api.getRuleSet(ruleSetId);
      if (!current()) return;
      applyDetail(response);
    } catch (caught: unknown) {
      if (!current()) return;
      applyDetail(null);
      setDetailError(messageOf(caught, "Could not load this rule set version."));
    } finally {
      if (current()) setDetailLoading(false);
    }
  }, [applyDetail]);

  // The settled run's draft is reconciled outside the polling callback: setting a
  // terminal job disables that sync, so its `isStale()` reports true from the
  // moment the result arrives. Sequencing inside the loaders and the page's own
  // mount state are what keep this read safe.
  const reconcileSettledRun = useCallback(
    async (settled: ARMJobStatusResponse) => {
      // What the administrator was inspecting when the run settled. If they pick
      // another version while this inventory read is in flight, that choice is
      // newer than the run's result and the produced draft must not displace it.
      const selectionAtStart = selectedIdRef.current;
      const detailSeqAtStart = detailSeqRef.current;
      const read = await loadRuleSets();
      if (!mountedRef.current || read.outcome !== "loaded") return;
      if (
        selectedIdRef.current !== selectionAtStart ||
        detailSeqRef.current !== detailSeqAtStart
      ) {
        return;
      }
      const producedId = settled.result?.rule_set_id;
      if (
        typeof producedId === "string" &&
        read.items.some((item) => item.rule_set_id === producedId)
      ) {
        await loadDetail(producedId);
      }
    },
    [loadRuleSets, loadDetail],
  );

  const { sync: syncInventory } = usePeriodicSync(
    async ({ isStale }) => {
      const read = await loadRuleSets();
      if (isStale() || read.outcome !== "loaded" || read.items.length === 0) return;
      const inspecting = selectedIdRef.current;
      if (inspecting !== null) {
        // Another administrator can activate or roll back the very version being
        // inspected here. The inventory read above already carries that change,
        // so the panel is re-read whenever the lifecycle state it is showing no
        // longer matches the one the server reports — the sequence guards inside
        // loadDetail still decide whether that read is allowed to land.
        const listed = read.items.find((ruleSet) => ruleSet.rule_set_id === inspecting);
        const shown = detailRef.current;
        const drifted =
          listed !== undefined &&
          (shown === null ||
            shown.rule_set_id !== inspecting ||
            listed.status !== shown.status ||
            listed.activated_at !== shown.activated_at);
        if (drifted) await loadDetail(inspecting);
        return;
      }
      // Land on the version that is guiding the GA right now, so the page opens
      // on the state a reviewer is being asked about.
      const opening =
        read.items.find((ruleSet) => ruleSet.status === "active") ?? read.items[0];
      await loadDetail(opening.rule_set_id);
    },
    // One read at mount. Versions only change through this page's own actions
    // and through a mining run, both of which refresh the list themselves.
    { enabled: true, intervalMs: 60_000 },
  );

  const pollingJob = jobId !== null && (job === null || !isTerminalJobState(job.status));

  usePeriodicSync(
    async ({ isStale }) => {
      if (jobId === null) return;
      try {
        const next = await api.getARMJob(jobId);
        if (isStale()) return;
        setJob(next);
        setJobError("");
        if (isTerminalJobState(next.status) && settledJobRef.current !== next.job_id) {
          settledJobRef.current = next.job_id;
          void reconcileSettledRun(next);
        }
      } catch (caught: unknown) {
        if (isStale()) return;
        setJobError(messageOf(caught, "Could not read the mining run's progress."));
      }
    },
    { enabled: pollingJob, intervalMs: JOB_POLL_INTERVAL_MS, deps: [jobId] },
  );

  const producedVersion =
    job?.status === "succeeded" && typeof job.result?.rule_set_id === "string"
      ? (() => {
          const produced = ruleSets.find((ruleSet) => ruleSet.rule_set_id === job.result?.rule_set_id);
          return produced ? ruleSetVersionName(produced) : null;
        })()
      : null;

  // Both transitions end the same way server-side: the target becomes active and
  // whatever was active is superseded. Applying that from the response — rather
  // than waiting on the refresh below — keeps the listed statuses, and so the
  // lifecycle actions each row offers, from describing the state the transition
  // just replaced when that refresh is slow or fails outright.
  function applyTransitionToInventory(updated: RuleSetDetail) {
    inventorySeqRef.current += 1;
    setRuleSets((current) =>
      current.map((ruleSet) => {
        if (ruleSet.rule_set_id === updated.rule_set_id) {
          return { ...ruleSet, status: updated.status, activated_at: updated.activated_at };
        }
        return ruleSet.status === "active" ? { ...ruleSet, status: "superseded" } : ruleSet;
      }),
    );
  }

  async function confirmTransition() {
    if (pending === null) return;
    setTransitionBusy(true);
    setTransitionError("");
    try {
      const updated =
        pending.action === "activate"
          ? await api.activateRuleSet(pending.ruleSet.rule_set_id)
          : await api.rollbackRuleSet(pending.ruleSet.rule_set_id);
      // The lifecycle response is the newest truth about this version, so it
      // supersedes any detail read still in flight. That read is now stale and
      // will return without touching the panel, so the loading and error state
      // it would have cleared is cleared here instead.
      detailSeqRef.current += 1;
      applyDetail(updated);
      setDetailLoading(false);
      setDetailError("");
      selectedIdRef.current = updated.rule_set_id;
      setSelectedId(updated.rule_set_id);
      setPending(null);
      applyTransitionToInventory(updated);
      await loadRuleSets();
    } catch (caught: unknown) {
      // A structural validation refusal is the answer to "is this candidate
      // sound", so it is reported where the administrator asked, not as a
      // generic page error.
      setTransitionError(
        caught instanceof ApiError && caught.code === "RULE_SET_INVALID"
          ? `Structural validation failed: ${caught.message} The version stays as it is.`
          : messageOf(caught, "Could not change the rule set's status."),
      );
    } finally {
      setTransitionBusy(false);
    }
  }

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">
                  Administrator only
                </p>
                <h1 className="font-headline-lg text-[32px] mt-1">Rule Sets</h1>
                <p className="text-on-surface-variant">
                  Immutable versions of the association rules that guide schedule generation.
                </p>
              </div>
              {/* The page tracks one run at a time: starting a second would
                  replace the job being polled while the first keeps running on
                  the server and may still produce a draft nobody is watching
                  for. */}
              <button
                onClick={() => setMining(true)}
                disabled={pollingJob}
                title={
                  pollingJob
                    ? "A mining run is already in progress. It has to settle before another can start."
                    : undefined
                }
                className="flex items-center gap-2 bg-surface-tint text-white px-4 py-2.5 rounded-lg font-bold disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <Sparkles className="w-4 h-4" /> Mine new candidate
              </button>
            </div>

            {listError && ruleSets.length > 0 && (
              <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">
                {listError}
              </p>
            )}
            {jobError && (
              <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">
                {jobError}
              </p>
            )}
            {job && <JobBanner job={job} draftVersion={producedVersion} />}

            {listError && ruleSets.length === 0 ? (
              <LoadError
                message={listError}
                retrying={listLoading}
                onRetry={() => {
                  setListLoading(true);
                  // The same read as the mount sync, so it lands on the active version.
                  void syncInventory(false);
                }}
              />
            ) : listLoading ? (
              <p className="text-on-surface-variant">Loading rule set versions...</p>
            ) : ruleSets.length === 0 ? (
              <p className="border border-outline-variant rounded-xl bg-surface-container-lowest p-10 text-center text-on-surface-variant">
                No rule sets have been mined yet. Start a mining run to produce the first draft
                version.
              </p>
            ) : (
              <div className="grid lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] gap-6 items-start">
                {/* Both the inventory and the detail panel head their versions
                    "Version N", so each side is named for assistive technology
                    and for the tests that read them apart. */}
                <ul aria-label="Rule set versions" className="space-y-3">
                  {ruleSets.map((ruleSet) => {
                    const isSelected = ruleSet.rule_set_id === selectedId;
                    return (
                      <li key={ruleSet.rule_set_id}>
                        <article
                          className={`bg-surface-container-lowest border rounded-xl p-4 shadow-sm ${isSelected ? "border-primary" : "border-outline-variant"}`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <h2 className="font-headline-md text-lg">Version {ruleSetVersionName(ruleSet)}</h2>
                              <p className="text-sm text-on-surface-variant">
                                Generated {formatTimestamp(ruleSet.generated_at)}
                              </p>
                              <p className="text-sm text-on-surface-variant">
                                Activated {formatTimestamp(ruleSet.activated_at)}
                              </p>
                            </div>
                            <StatusBadge status={ruleSet.status} />
                          </div>
                          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-on-surface-variant">
                            <span className="inline-flex items-center gap-1.5">
                              <History className="w-4 h-4" aria-hidden="true" />
                              {ruleSet.term_count} term{ruleSet.term_count === 1 ? "" : "s"}
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <Layers className="w-4 h-4" aria-hidden="true" />
                              {ruleSet.rule_count} rule{ruleSet.rule_count === 1 ? "" : "s"}
                            </span>
                          </p>
                          <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-outline-variant/60">
                            <button
                              onClick={() => loadDetail(ruleSet.rule_set_id)}
                              className="px-3 py-2 rounded border border-outline-variant text-xs hover:border-primary"
                            >
                              Inspect version {ruleSetVersionName(ruleSet)}
                            </button>
                            {ruleSet.status === "draft" && (
                              <button
                                onClick={() => {
                                  setTransitionError("");
                                  setPending({ ruleSet, action: "activate" });
                                }}
                                className="ml-auto inline-flex items-center gap-1.5 px-3 py-2 rounded border border-primary text-primary text-xs"
                              >
                                <ShieldCheck className="w-4 h-4" aria-hidden="true" />
                                Validate and activate
                              </button>
                            )}
                            {ruleSet.status === "superseded" && (
                              <button
                                onClick={() => {
                                  setTransitionError("");
                                  setPending({ ruleSet, action: "rollback" });
                                }}
                                className="ml-auto inline-flex items-center gap-1.5 px-3 py-2 rounded border border-outline-variant text-xs hover:border-primary"
                              >
                                <RotateCcw className="w-4 h-4" aria-hidden="true" />
                                Roll back to version {ruleSetVersionName(ruleSet)}
                              </button>
                            )}
                          </div>
                        </article>
                      </li>
                    );
                  })}
                </ul>

                <div className="space-y-6 min-w-0">
                  <section aria-label="Version detail" className="bg-surface-container-lowest border border-outline-variant rounded-xl p-6 shadow-sm">
                    {detailError && (
                      <p role="alert" className="text-sm text-error">{detailError}</p>
                    )}
                    {detailLoading ? (
                      <p className="text-on-surface-variant">Loading version detail...</p>
                    ) : detail ? (
                      <RuleSetDetailPanel detail={detail} />
                    ) : (
                      !detailError && (
                        <p className="text-on-surface-variant">
                          Select a version to inspect its terms, thresholds, and stream summaries.
                        </p>
                      )
                    )}
                  </section>
                  {/* Keyed so a different version starts on its first page. */}
                  {/* Stays mounted while the same version refreshes, so the
                      reviewer keeps their stream, sort and page. */}
                  {detail && (!detailLoading || detail.rule_set_id === selectedId) && (
                    <>
                      <FacultyCoveragePanel
                        key={`coverage-${detail.rule_set_id}`}
                        ruleSetId={detail.rule_set_id}
                        thresholds={detail.thresholds}
                      />
                      <RuleSetRulesPanel
                        key={detail.rule_set_id}
                        ruleSetId={detail.rule_set_id}
                        thresholds={detail.thresholds}
                        streams={detail.streams}
                      />
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </main>
      </div>

      {mining && (
        <MineDraftDialog
          onClose={() => setMining(false)}
          onSubmitted={(startedJobId) => {
            setMining(false);
            setJob(null);
            setJobError("");
            settledJobRef.current = null;
            setJobId(startedJobId);
          }}
        />
      )}

      {pending && (
        <ConfirmTransitionDialog
          pending={pending}
          activeVersion={activeVersion}
          busy={transitionBusy}
          error={transitionError}
          onCancel={() => {
            setPending(null);
            setTransitionError("");
          }}
          onConfirm={() => void confirmTransition()}
        />
      )}
    </div>
  );
}
