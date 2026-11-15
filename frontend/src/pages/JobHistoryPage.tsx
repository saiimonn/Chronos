import { useEffect, useRef, useState } from "react";
import { RefreshCw, Trash2 } from "lucide-react";
import { formatCompleteness } from "../utils/formatCompleteness";
import {
  ApiError,
  api,
  type DeleteJobsResult,
  type JobDetail,
  type JobHistoryFilters,
  type JobHistoryItem,
  type JobHistoryPage as HistoryPageData,
  type JobKind,
  type JobStatus,
  type ScheduleValidation,
} from "../api/client";
import LoadError from "../components/LoadError";
import ConfirmDeleteDialog from "../components/modals/ConfirmDeleteDialog";
import Modal from "../components/modals/Modal";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import { useIsAdmin } from "../hooks/useRole";

const PAGE_SIZE = 20;

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  timed_out: "Timed out",
};

const KIND_LABELS: Record<JobKind, string> = { ga: "GA", arm: "ARM", evaluation: "Evaluation" };

const STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-surface-container text-on-surface-variant",
  running: "bg-primary/10 text-primary",
  succeeded: "bg-[#e6f4ea] text-[#1e6b34]",
  failed: "bg-error-container/40 text-error",
  timed_out: "bg-tertiary-fixed/40 text-on-tertiary-fixed-variant",
};

const ACTIVE_STATUSES: ReadonlySet<JobStatus> = new Set(["queued", "running"]);

const SKIP_REASONS: Record<DeleteJobsResult["skipped"][number]["reason"], string> = {
  active: "still queued or running",
  in_use: "still used by a schedule, rule set or evaluation",
  not_found: "already gone",
};

function deleteSummary(result: DeleteJobsResult): string {
  const parts = [`Deleted ${result.deleted.length} job${result.deleted.length === 1 ? "" : "s"}.`];
  const counts = new Map<string, number>();
  for (const item of result.skipped) counts.set(item.reason, (counts.get(item.reason) ?? 0) + 1);
  for (const [reason, count] of counts) {
    parts.push(`Kept ${count}: ${SKIP_REASONS[reason as keyof typeof SKIP_REASONS]}.`);
  }
  return parts.join(" ");
}

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function scopeLabel(job: JobHistoryItem): string {
  const scope = job.scope;
  if (!scope?.term_id) return job.kind === "arm" ? "—" : "Unknown term";
  const termNumber = scope.term_number === null ? "" : ` · Term ${scope.term_number}`;
  const term = scope.academic_year ? `AY ${scope.academic_year}${termNumber}` : scope.term_id;
  return scope.block_id ? `${term} · Block` : `${term} · Term-wide`;
}

function guidanceLabel(job: JobHistoryItem): string {
  if (job.kind === "arm") return "—";
  if (!job.guidance) return "Unknown";
  return job.guidance.mode === "guided" ? `Rule set v${job.guidance.rule_set_version}` : "Unguided";
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status]}`}>{STATUS_LABELS[status]}</span>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">{label}</dt>
      <dd className="mt-0.5 break-all">{children}</dd>
    </div>
  );
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

const METRIC_KEYS = [
  "termination_reason",
  "termination_generation",
  "elapsed_seconds",
  "assignment_completeness",
  "hard_conflict_count",
  "historical_pattern_attainment",
  // ARM publication counts (handlers.py); never the mined records themselves.
  "stream_count",
  "input_record_count",
  "mined_rule_count",
  "is_empty",
];

function JobDetailPanel({ job }: { job: JobDetail }) {
  const [validation, setValidation] = useState<ScheduleValidation | null>(null);
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState("");
  const result = record(job.result);
  const metrics = record(result.metrics);
  const candidate = record(result.candidate);
  const scheduleId = typeof candidate.schedule_id === "string" ? candidate.schedule_id : null;
  const assignments = Array.isArray(candidate.assignments) ? candidate.assignments.length : null;
  const metricKeys = METRIC_KEYS.filter((key) => key in metrics);
  const evaluationBatchId = job.kind !== "evaluation"
    ? null
    : job.evaluation_batch?.evaluation_batch_id
      ?? (typeof result.evaluation_batch_id === "string" ? result.evaluation_batch_id : null);

  async function validate() {
    if (!scheduleId) return;
    setValidating(true);
    setValidationError("");
    try {
      setValidation(await api.validateSchedule(scheduleId));
    } catch (caught) {
      setValidationError(
        caught instanceof ApiError && caught.status === 404
          ? "This candidate schedule was deleted, so it can no longer be validated."
          : messageOf(caught, "Could not validate the candidate schedule."),
      );
    } finally {
      setValidating(false);
    }
  }

  return (
    <section aria-label="Job details" className="p-6 space-y-5 overflow-y-auto">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={job.status} />
        <code className="text-xs text-on-surface-variant break-all">{job.job_id}</code>
      </div>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
        <Field label="Submitted">{formatTimestamp(job.submitted_at)}</Field>
        <Field label="Started">{formatTimestamp(job.started_at)}</Field>
        <Field label="Completed">{formatTimestamp(job.completed_at)}</Field>
        <Field label="Scope">{scopeLabel(job)}</Field>
        <Field label="Frozen guidance">
          {guidanceLabel(job)}
          {job.guidance?.rule_set_id && <span className="block text-xs text-on-surface-variant">{job.guidance.rule_set_id}</span>}
        </Field>
        {/* Evaluations run once per listed seed; config.random_seed is not used. */}
        {job.config && (job.kind === "evaluation"
          ? <Field label="Run seeds">{Array.isArray(job.config.seeds) ? job.config.seeds.join(", ") : "—"}</Field>
          : <Field label="Random seed">{String(job.config.random_seed ?? "—")}</Field>)}
      </dl>

      {job.error && (
        <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-3 text-sm">
          <span className="font-medium">{job.status === "timed_out" ? "Timed out" : "Failed"} ({job.error.code}):</span> {job.error.message}
        </p>
      )}

      {job.config && (
        <div>
          <h3 className="font-medium mb-2">Configuration snapshot</h3>
          <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
            {/* An evaluation's seeds are shown as "Run seeds" above. */}
            {Object.entries(job.config).filter(([key]) => job.kind !== "evaluation" || (key !== "random_seed" && key !== "seeds")).map(([key, value]) => (
              <Field key={key} label={key.replaceAll("_", " ")}>{Array.isArray(value) ? value.join(", ") : String(value ?? "—")}</Field>
            ))}
          </dl>
        </div>
      )}

      {metricKeys.length > 0 && (
        <div>
          <h3 className="font-medium mb-2">Run metrics</h3>
          <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
            {metricKeys.map((key) => (
              <Field key={key} label={key.replaceAll("_", " ")}>{String(metrics[key] ?? "—")}</Field>
            ))}
          </dl>
        </div>
      )}

      {job.kind === "ga" && job.status === "succeeded" && (
        <div className="border-t border-outline-variant pt-4 space-y-3">
          <h3 className="font-medium">Candidate provenance</h3>
          {scheduleId ? (
            <>
              <p className="text-sm">Published candidate schedule <code className="break-all">{scheduleId}</code>{assignments !== null && ` · ${assignments} assignments`}</p>
              <p className="text-xs text-on-surface-variant">Finalization and export eligibility are decided only by the independent validator, re-run on the schedule as it is now.</p>
              <button type="button" onClick={() => void validate()} disabled={validating} className="px-4 py-2 border border-outline-variant rounded-lg disabled:opacity-60">
                {validating ? "Validating..." : "Run independent validation"}
              </button>
              {validationError && <p role="alert" className="text-sm text-error">{validationError}</p>}
              {validation && (
                <dl aria-label="Validation report" className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                  <Field label="Conflict errors">{validation.hard_conflict_count}</Field>
                  <Field label="Completeness">{formatCompleteness(validation.completeness_percentage)}</Field>
                  <Field label="Assigned">{validation.assigned_class_count} / {validation.required_class_count}</Field>
                  <Field label="Export eligible">{validation.export_allowed ? "Yes" : "No"}</Field>
                </dl>
              )}
            </>
          ) : (
            <p className="text-sm text-on-surface-variant">This run did not retain a candidate schedule{assignments !== null && ` (${assignments} assignments reported)`}, so no validation report is available.</p>
          )}
        </div>
      )}

      {evaluationBatchId && (
        <dl aria-label="Evaluation outcome" className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm border-t border-outline-variant pt-4">
          <Field label="Evaluation batch">{evaluationBatchId}</Field>
          {job.evaluation_batch ? (
            <Field label="Completed runs">{job.evaluation_batch.completed_run_count} / {job.evaluation_batch.planned_run_count}</Field>
          ) : (
            <>
              <Field label="Succeeded runs">{String(result.succeeded_run_count ?? "—")}</Field>
              <Field label="Failed runs">{String(result.failed_run_count ?? "—")}</Field>
            </>
          )}
        </dl>
      )}

      {job.kind === "arm" && typeof result.rule_set_id === "string" && (
        <p className="text-sm border-t border-outline-variant pt-4">Published draft rule set <code className="break-all">{result.rule_set_id}</code></p>
      )}
    </section>
  );
}

export default function JobHistoryPage() {
  const admin = useIsAdmin();
  const [filters, setFilters] = useState<JobHistoryFilters>({ limit: PAGE_SIZE, offset: 0 });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState<HistoryPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<JobDetail | null>(null);
  // Bumped only by Refresh, so it remounts the panel and drops an old validation report.
  const [refreshCount, setRefreshCount] = useState(0);
  // A different job whose details are still loading; its predecessor is hidden meanwhile.
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState("");
  const detailRequest = useRef(0);
  const requestedJobId = useRef<string | null>(null);
  // The query the rows on screen answer, so a failed reload of the same query can keep them.
  const loadedQuery = useRef("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  // Jobs awaiting delete confirmation: one row's, or every checked row's.
  const [confirmIds, setConfirmIds] = useState<string[] | null>(null);
  const [deleteNotice, setDeleteNotice] = useState("");

  useEffect(() => {
    let current = true;
    const query = JSON.stringify(filters);
    setLoading(true);
    setChecked(new Set());
    api.listJobs(filters)
      .then((response) => {
        if (!current) return;
        // The page emptied under us (jobs left this filter): step back to the last page.
        if (response.items.length === 0 && response.offset > 0) {
          const last = response.total > 0 ? Math.floor((response.total - 1) / PAGE_SIZE) * PAGE_SIZE : 0;
          setFilters((previous) => ({ ...previous, offset: last }));
          return;
        }
        setPage(response);
        loadedQuery.current = query;
        setError("");
      })
      .catch((caught: unknown) => {
        if (!current) return;
        // Rows and paging from other filters no longer describe this query; a
        // failed reload of the same query keeps what was loaded.
        if (loadedQuery.current !== query) setPage(null);
        setError(messageOf(caught, "Could not load job history."));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => { current = false; };
  }, [filters, reload]);

  function updateFilters(next: Partial<JobHistoryFilters>) {
    setFilters((previous) => ({ ...previous, ...next, offset: 0 }));
  }

  async function open(jobId: string) {
    // Only the latest click may fill the panel; an older response is dropped.
    const request = ++detailRequest.current;
    requestedJobId.current = jobId;
    setPendingJobId(jobId);
    setDetailError("");
    try {
      const detail = await api.getJobDetail(jobId);
      if (request !== detailRequest.current) return;
      setSelected(detail);
    } catch (caught) {
      if (request !== detailRequest.current) return;
      setSelected(null);
      setDetailError(messageOf(caught, "Could not load job details."));
    } finally {
      if (request === detailRequest.current) setPendingJobId(null);
    }
  }

  function closeDetail() {
    // Drop any details still loading so they cannot reopen the dialog.
    detailRequest.current += 1;
    requestedJobId.current = null;
    setPendingJobId(null);
    setSelected(null);
    setDetailError("");
  }

  function toggle(jobId: string) {
    setChecked((previous) => {
      const next = new Set(previous);
      if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
      return next;
    });
  }

  async function confirmDelete(jobIds: string[]) {
    // A rejection propagates so the dialog stays open and shows it.
    const result = await api.deleteJobs(jobIds);
    setDeleteNotice(deleteSummary(result));
    const gone = new Set(result.deleted);
    if (selected && gone.has(selected.job_id)) setSelected(null);
    if (requestedJobId.current && gone.has(requestedJobId.current)) requestedJobId.current = null;
    setChecked(new Set());
    setReload((value) => value + 1);
  }

  function refresh() {
    // aria-disabled, not disabled: a disabled button drops keyboard focus to <body>.
    if (loading) return;
    setReload((value) => value + 1);
    setRefreshCount((value) => value + 1);
    // Reopen the last clicked job, which may still be loading.
    if (requestedJobId.current) void open(requestedJobId.current);
  }

  const offset = filters.offset ?? 0;
  const total = page?.total ?? 0;
  const switching = pendingJobId !== null && pendingJobId !== selected?.job_id;
  const highlightedId = pendingJobId ?? selected?.job_id;
  // An empty last read is not data to keep: a failure over no rows blocks too.
  const jobsBlocked = Boolean(error) && (!page || page.items.length === 0);
  const noPrevious = loading || offset === 0;
  const noNext = loading || offset + PAGE_SIZE >= total;
  const pageIndex = Math.floor(offset / PAGE_SIZE);
  const lastIndex = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const goTo = (index: number) => { if (!loading) setFilters((previous) => ({ ...previous, offset: index * PAGE_SIZE })); };
  const deletable = (page?.items ?? []).filter((job) => !ACTIVE_STATUSES.has(job.status));
  const allChecked = deletable.length > 0 && deletable.every((job) => checked.has(job.job_id));

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-6xl mx-auto space-y-6">
            <div className="flex flex-wrap justify-between items-end gap-4">
              <div>
                <h1 className="font-headline-lg text-[32px]">Job History</h1>
                <p className="text-on-surface-variant mt-1">
                  {admin ? "Every GA, ARM and evaluation job." : "Your GA and evaluation jobs."} Outcomes only — raw historical inputs are never shown.
                </p>
              </div>
              <button onClick={refresh} aria-disabled={loading} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg bg-surface-container-lowest aria-disabled:opacity-60">
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
            </div>

            <form
              className="flex flex-wrap gap-3 items-end"
              onSubmit={(event) => { event.preventDefault(); updateFilters({ q: search.trim() || undefined }); }}
            >
              <label className="flex-1 min-w-[200px]">
                <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Search job ID or error code</span>
                <input value={search} onChange={(event) => setSearch(event.target.value)} maxLength={100} className="w-full mt-1 border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest" />
              </label>
              <label>
                <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Kind</span>
                <select value={filters.kind ?? ""} onChange={(event) => updateFilters({ kind: (event.target.value || undefined) as JobKind | undefined })} className="block mt-1 border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest">
                  <option value="">All kinds</option>
                  {(Object.keys(KIND_LABELS) as JobKind[]).filter((kind) => admin || kind !== "arm").map((kind) => (
                    <option key={kind} value={kind}>{KIND_LABELS[kind]}</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">Status</span>
                <select value={filters.status ?? ""} onChange={(event) => updateFilters({ status: (event.target.value || undefined) as JobStatus | undefined })} className="block mt-1 border border-outline-variant rounded-lg px-3 py-2 bg-surface-container-lowest">
                  <option value="">All statuses</option>
                  {(Object.keys(STATUS_LABELS) as JobStatus[]).map((status) => (
                    <option key={status} value={status}>{STATUS_LABELS[status]}</option>
                  ))}
                </select>
              </label>
              <button type="submit" className="px-4 py-2 rounded-lg bg-surface-tint text-white font-bold">Search</button>
            </form>

            {deleteNotice && <p role="status" className="border border-outline-variant bg-surface-container-lowest rounded-lg p-4">{deleteNotice}</p>}

            {error && !jobsBlocked && <p role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4">{error}</p>}

            {jobsBlocked ? (
              <LoadError message={error} retrying={loading} onRetry={() => setReload((count) => count + 1)} />
            ) : !page ? (
              <p className="text-on-surface-variant">Loading job history...</p>
            ) : (
            <>
            {checked.size > 0 && (
              <div className="flex items-center justify-between gap-3 border border-outline-variant rounded-lg bg-surface-container-lowest px-4 py-2 text-sm">
                <span>{checked.size} selected</span>
                <button type="button" onClick={() => setConfirmIds([...checked])} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-error text-on-error font-bold">
                  <Trash2 className="w-4 h-4" /> Delete selected
                </button>
              </div>
            )}
            <div className="border border-outline-variant rounded-xl bg-surface-container-lowest overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-on-surface-variant">
                  <tr>
                    <th className="p-3 w-10">
                      <input
                        type="checkbox"
                        aria-label="Select all finished jobs on this page"
                        checked={allChecked}
                        disabled={deletable.length === 0}
                        onChange={() => setChecked(allChecked ? new Set() : new Set(deletable.map((job) => job.job_id)))}
                      />
                    </th>
                    <th className="p-3">Job</th>
                    <th className="p-3">Kind</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Scope</th>
                    <th className="p-3">Guidance</th>
                    <th className="p-3">Submitted</th>
                    <th className="p-3"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {page?.items.map((job) => (
                    <tr key={job.job_id} className={`border-t border-outline-variant ${highlightedId === job.job_id ? "bg-primary/5" : ""}`}>
                      <td className="p-3">
                        <input
                          type="checkbox"
                          aria-label={`Select job ${job.job_id}`}
                          checked={checked.has(job.job_id)}
                          disabled={ACTIVE_STATUSES.has(job.status)}
                          title={ACTIVE_STATUSES.has(job.status) ? "Queued and running jobs cannot be deleted" : undefined}
                          onChange={() => toggle(job.job_id)}
                        />
                      </td>
                      <td className="p-3">
                        <button type="button" onClick={() => void open(job.job_id)} aria-current={highlightedId === job.job_id ? "true" : undefined} className="text-primary underline font-mono" aria-label={`Open job ${job.job_id}`}>
                          {job.display_id ?? job.job_id.slice(0, 8)}
                        </button>
                      </td>
                      <td className="p-3">{KIND_LABELS[job.kind]}</td>
                      <td className="p-3"><StatusBadge status={job.status} /></td>
                      <td className="p-3">{scopeLabel(job)}</td>
                      <td className="p-3">{guidanceLabel(job)}</td>
                      <td className="p-3">{formatTimestamp(job.submitted_at)}</td>
                      <td className="p-3">
                        {!ACTIVE_STATUSES.has(job.status) && (
                          <button type="button" onClick={() => setConfirmIds([job.job_id])} aria-label={`Delete job ${job.job_id}`} className="p-1.5 rounded text-on-surface-variant hover:text-error hover:bg-error-container/30">
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!loading && page?.items.length === 0 && (
                    <tr><td colSpan={8} className="p-8 text-center text-on-surface-variant">No jobs match these filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between text-sm">
              <span>{total === 0 ? "0 jobs" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`}</span>
              <div className="flex gap-2">
                {pageIndex >= 2 && <button type="button" aria-disabled={loading} onClick={() => goTo(0)} className="px-3 py-1.5 border border-outline-variant rounded-lg aria-disabled:opacity-50">First</button>}
                {/* aria-disabled like Refresh, so a keyboard user keeps focus while paging. */}
                <button type="button" aria-disabled={noPrevious} onClick={() => { if (!noPrevious) setFilters((previous) => ({ ...previous, offset: Math.max(0, offset - PAGE_SIZE) })); }} className="px-3 py-1.5 border border-outline-variant rounded-lg aria-disabled:opacity-50">Previous</button>
                <button type="button" aria-disabled={noNext} onClick={() => { if (!noNext) setFilters((previous) => ({ ...previous, offset: offset + PAGE_SIZE })); }} className="px-3 py-1.5 border border-outline-variant rounded-lg aria-disabled:opacity-50">Next</button>
                {pageIndex <= lastIndex - 2 && <button type="button" aria-disabled={loading} onClick={() => goTo(lastIndex)} className="px-3 py-1.5 border border-outline-variant rounded-lg aria-disabled:opacity-50">Last</button>}
              </div>
            </div>
            </>
            )}

            {(selected || pendingJobId || detailError) && (
              <Modal
                title={selected && !switching ? `${KIND_LABELS[selected.kind]} job${selected.display_id ? ` ${selected.display_id}` : ""}` : "Job details"}
                width="max-w-4xl"
                onClose={closeDetail}
              >
                {detailError && <p role="alert" className="p-6 text-error">{detailError}</p>}
                {switching && <p role="status" className="p-6 text-on-surface-variant">Loading job details...</p>}
                {selected && !switching && <JobDetailPanel key={`${selected.job_id}:${refreshCount}`} job={selected} />}
              </Modal>
            )}

            {confirmIds && (
              <ConfirmDeleteDialog
                title={confirmIds.length === 1 ? "Delete job" : `Delete ${confirmIds.length} jobs`}
                subtitle={confirmIds.length === 1 ? confirmIds[0] : "Selected jobs"}
                actionLabel="Delete"
                pendingLabel="Deleting..."
                fallbackError="Could not delete the jobs."
                onConfirm={() => confirmDelete(confirmIds)}
                onClose={() => setConfirmIds(null)}
              >
                <p>The job record and its stored result are removed from history permanently.</p>
                <p className="text-sm text-on-surface-variant">Jobs that a schedule, rule set or evaluation still uses are kept.</p>
              </ConfirmDeleteDialog>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
