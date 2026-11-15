import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, FlaskConical } from "lucide-react";
import {
  ApiError,
  api,
  isTerminalJobState,
  type EvaluationEvidence,
  type EvaluationExportFormat,
  type EvaluationJobStatusResponse,
  ruleSetVersionName,
  type RuleSetSummary,
  type Term,
} from "../api/client";
import Sidebar from "../components/layout/Sidebar";
import TopBar from "../components/layout/TopBar";
import { usePeriodicSync } from "../hooks/usePeriodicSync";
import { saveFile } from "./CommitteeTimetablePage";
import { NUMERIC_FIELDS, fromDraft, toDraft, type Draft } from "./GAConfigurationPage";

const JOB_POLL_INTERVAL_MS = 2000;
const DEFAULT_SEEDS = "0, 1, 2, 3, 4, 5, 6, 7, 8, 9";
const EXPORT_FORMATS: { format: EvaluationExportFormat; label: string }[] = [
  { format: "csv", label: "CSV" },
  { format: "xlsx", label: "XLSX" },
  { format: "json", label: "JSON evidence" },
];
// Each run replaces random_seed with its pair seed, so the form does not offer it.
const CONFIG_FIELDS = NUMERIC_FIELDS.filter(({ key }) => key !== "random_seed");

// Count, uniqueness and range are the backend's checks. The browser only refuses
// what it cannot send exactly: a JSON number past 2^53 would be silently rounded.
function parseSeeds(text: string): number[] | string {
  const tokens = text.split(",").map((token) => token.trim()).filter((token) => token !== "");
  const seeds = tokens.map(Number);
  if (tokens.some((token, index) => !/^-?\d+$/.test(token) || !Number.isSafeInteger(seeds[index]))) {
    return "Seeds must be whole numbers within ±9007199254740991 so the browser can send them exactly.";
  }
  return seeds;
}

function describeError(caught: unknown, fallback: string): string[] {
  if (!(caught instanceof ApiError)) return [fallback];
  const lines = [`${caught.message} (${caught.code})`];
  const periods = caught.details.withheld_periods;
  if (Array.isArray(periods)) lines.push(`Withheld periods: ${periods.join(", ")}`);
  const errors = caught.details.errors;
  if (Array.isArray(errors)) {
    for (const error of errors) {
      const field = String(error?.field ?? "body");
      // The 422 envelope carries only field and type, so restate the backend's rule.
      const rule = field.startsWith("body.seeds")
        ? ` (${SEED_RULE})`
        : field === "body.config"
          ? ` (${CONFIG_RULE})`
          : field.startsWith("body.config.meeting_day_patterns") ? ` (${MEETING_RULE})` : "";
      lines.push(`${field}: ${String(error?.type ?? "invalid")}${rule}`);
    }
  }
  return lines;
}

// Backend rule (app/schemas/evaluation.py), restated for the operator only.
const SEED_RULE = "the backend requires exactly ten distinct seeds, each a signed 64-bit integer";
// Cross-field checks in app/schemas/jobs.py, reported against "body.config" alone.
const CONFIG_RULE = "the backend requires tournament size at most the population size and elite count below the population size";
// app/services/ga/config.py validate_meeting_day_patterns.
const MEETING_RULE = "the backend requires at least one meeting day pattern, using only M, T, W, Th, F or Sa";

// How the backend's CSV/XLSX export presents a slot that never reached an
// outcome checkpoint in a terminal batch (app/services/evaluation.py).
const UNFINISHED_MESSAGE: Record<string, string> = {
  pending: "Run not started before job terminated.",
  running: "Run interrupted before outcome checkpoint.",
};

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function ErrorBox({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <div role="alert" className="border border-error/30 bg-error-container/30 text-on-error-container rounded-lg p-4 space-y-1">
      {lines.map((line) => <p key={line}>{line}</p>)}
    </div>
  );
}

const STATE_TEXT: Record<EvaluationJobStatusResponse["status"], string> = {
  queued: "Queued. Waiting for the local worker to claim the job.",
  running: "Running on the local worker. The job status does not report per-run progress; counts appear once the job is terminal.",
  succeeded: "The job finished and every planned run has a recorded outcome. Individual runs may still have failed execution (see the counts), and a successful run is not a claim that its schedule is feasible; see the per-run feasibility below.",
  failed: "Failed. Runs completed before the failure are retained as evidence.",
  timed_out: "Timed out. Runs completed before the timeout are retained as evidence.",
};

export default function MatchedEvaluationPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const jobId = searchParams.get("job") || null;

  const [terms, setTerms] = useState<Term[]>([]);
  const [ruleSets, setRuleSets] = useState<RuleSetSummary[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [termId, setTermId] = useState("");
  const [ruleSetId, setRuleSetId] = useState("");
  const [seeds, setSeeds] = useState(DEFAULT_SEEDS);
  const [loadError, setLoadError] = useState<string[]>([]);
  const [submitError, setSubmitError] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const [loadedJob, setJob] = useState<{ forId: string; job: EvaluationJobStatusResponse } | null>(null);
  // The reset effect below runs after the first render with a new ?job=, so
  // the job state can still hold the previous job for that frame. Compare with
  // the id it was requested for: the backend answers with the canonical UUID,
  // which a pasted uppercase or unhyphenated id does not equal.
  const job = loadedJob?.forId === jobId ? loadedJob.job : null;
  const [loadedJobError, setJobError] = useState<{ forId: string; lines: string[] } | null>(null);
  const jobError = loadedJobError?.forId === jobId ? loadedJobError.lines : [];
  const [loadedEvidence, setEvidence] = useState<EvaluationEvidence | null>(null);
  const [loadedEvidenceError, setEvidenceError] = useState<{ batchId: string; lines: string[] } | null>(null);
  const [evidenceAttempt, setEvidenceAttempt] = useState(0);
  const [downloadError, setDownloadError] = useState<{ batchId: string; lines: string[] } | null>(null);
  const [downloading, setDownloading] = useState<EvaluationExportFormat | null>(null);
  // A 4xx or a non-evaluation job will not change by polling again.
  const [unreadableJobId, setUnreadableJobId] = useState<string | null>(null);
  const jobUnreadable = jobId !== null && unreadableJobId === jobId;
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listTerms(), api.listRuleSets(), api.getGAConfiguration()])
      .then(([termList, ruleSetList, configuration]) => {
        if (cancelled) return;
        setTerms(termList.items);
        setRuleSets(ruleSetList.items);
        setDraft(toDraft(configuration.config));
        setTermId((termList.items.find((term) => term.is_active) ?? termList.items[0])?.term_id ?? "");
        setRuleSetId(ruleSetList.items[0]?.rule_set_id ?? "");
      })
      .catch((caught: unknown) => {
        if (!cancelled) setLoadError(describeError(caught, "Could not load terms, rule sets or the GA configuration."));
      });
    return () => { cancelled = true; };
  }, []);

  // A different job in the URL must not show the previous job's state.
  useEffect(() => {
    setJob(null);
    setJobError(null);
    setUnreadableJobId(null);
    setEvidence(null);
    setEvidenceError(null);
    setDownloadError(null);
  }, [jobId]);

  usePeriodicSync(
    async ({ isStale }) => {
      if (jobId === null) return;
      try {
        const next = await api.getEvaluationJob(jobId);
        if (isStale()) return;
        if (next.kind !== "evaluation") {
          setUnreadableJobId(jobId);
          setJobError({ forId: jobId, lines: [`Job ${jobId} is a ${next.kind} job, not a matched evaluation.`] });
          return;
        }
        setJob({ forId: jobId, job: next });
        setJobError(null);
      } catch (caught) {
        if (isStale()) return;
        if (caught instanceof ApiError && caught.status >= 400 && caught.status < 500) setUnreadableJobId(jobId);
        setJobError({ forId: jobId, lines: describeError(caught, "Could not read the evaluation job.") });
      }
    },
    {
      enabled: jobId !== null && !jobUnreadable && (job === null || !isTerminalJobState(job.status)),
      intervalMs: JOB_POLL_INTERVAL_MS,
      deps: [jobId],
    },
  );

  const terminal = job !== null && isTerminalJobState(job.status);
  const batchId = terminal
    ? job.result?.evaluation_batch_id ?? job.evaluation_batch?.evaluation_batch_id ?? null
    : null;
  // A response for an earlier job can land after the URL moved on; only the
  // evidence for the batch on screen is ever shown.
  const evidence = loadedEvidence?.evaluation_batch_id === batchId ? loadedEvidence : null;
  const evidenceError = loadedEvidenceError?.batchId === batchId ? loadedEvidenceError.lines : [];

  useEffect(() => {
    setEvidence(null);
    if (batchId === null) return;
    let cancelled = false;
    setEvidenceError(null);
    api.getEvaluationEvidence(batchId)
      .then((read) => { if (!cancelled) setEvidence(read); })
      .catch((caught: unknown) => {
        if (!cancelled) setEvidenceError({ batchId, lines: describeError(caught, "Could not read the evaluation evidence.") });
      });
    return () => { cancelled = true; };
  }, [batchId, evidenceAttempt]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft || submitting) return;
    const parsed = parseSeeds(seeds);
    if (typeof parsed === "string") {
      setSubmitError([parsed]);
      return;
    }
    setSubmitting(true);
    setSubmitError([]);
    try {
      const accepted = await api.submitEvaluation({
        term_id: termId,
        rule_set_id: ruleSetId,
        seeds: parsed,
        // Every run replaces random_seed with its pair seed. Sending the first
        // pair seed keeps the base config inside the backend's 64-bit rule even
        // when the saved GA configuration holds a seed outside it.
        config: { ...fromDraft(draft), random_seed: parsed[0] ?? 0 },
      });
      // Leaving the page while the request was in flight must not pull the
      // operator back here.
      if (mountedRef.current) setSearchParams({ job: accepted.job_id });
    } catch (caught) {
      setSubmitError(describeError(caught, "Could not submit the evaluation."));
    } finally {
      setSubmitting(false);
    }
  }

  async function download(format: EvaluationExportFormat) {
    if (batchId === null) return;
    setDownloading(format);
    setDownloadError(null);
    try {
      const file = await api.exportEvaluation(batchId, format);
      saveFile(file.blob, file.filename);
    } catch (caught) {
      setDownloadError({ batchId, lines: describeError(caught, "Could not download the evaluation evidence.") });
    } finally {
      setDownloading(null);
    }
  }

  const fieldClass = "w-full mt-2 border border-outline-variant rounded-lg px-3 py-2.5 bg-surface-container-lowest";
  const labelClass = "font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant";
  const started = evidence?.runs.filter((run) => run.status !== "pending").length;
  // Without evidence, the job still reports counts: a failed/timed-out job in
  // evaluation_batch, and a succeeded job in result, where every planned run
  // has an outcome.
  const resultTotal = job?.result ? job.result.succeeded_run_count + job.result.failed_run_count : null;
  const counts = evidence
    ? { planned: evidence.planned_run_count, started: String(started), completed: evidence.completed_run_count }
    : job?.evaluation_batch
      ? { planned: job.evaluation_batch.planned_run_count, started: "—", completed: job.evaluation_batch.completed_run_count }
      : resultTotal !== null
        ? { planned: resultTotal, started: String(resultTotal), completed: resultTotal }
        : null;

  return (
    <div className="bg-background text-on-surface font-body-md min-h-screen flex">
      <Sidebar compactOnMobile />
      <div className="flex-1 min-w-0 flex flex-col min-h-screen ml-16 md:ml-[260px] bg-background">
        <TopBar />
        <main id="main-content" className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8">
          <div className="max-w-5xl mx-auto space-y-6">
            <div>
              <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Matched run, administrator only</p>
              <h1 className="font-headline-lg text-[32px] mt-1">GA Evaluation</h1>
              <p className="text-on-surface-variant mt-1">
                Ten matched pairs: an ARM-guided and an unguided GA run per seed, sharing the term, configuration and seed.
                The backend decides holdout, provenance and validity; this page only submits and reports.
              </p>
            </div>

            <ErrorBox lines={loadError} />

            {draft && (
              <form onSubmit={(event) => void submit(event)} className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block">
                    <span className={labelClass}>Term</span>
                    <select required value={termId} onChange={(event) => setTermId(event.target.value)} disabled={submitting} className={fieldClass}>
                      {terms.map((term) => (
                        <option key={term.term_id} value={term.term_id}>
                          {term.academic_year} term {term.term_number}{term.is_active ? " (active)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div>
                    <label className="block">
                      <span className={labelClass}>Rule Set</span>
                      <select required value={ruleSetId} onChange={(event) => setRuleSetId(event.target.value)} disabled={submitting} aria-describedby={ruleSets.length === 0 ? "rule-set-empty-hint" : undefined} className={fieldClass}>
                        {ruleSets.length === 0 && <option value="" disabled>No Rule Sets available</option>}
                        {ruleSets.map((ruleSet) => (
                          <option key={ruleSet.rule_set_id} value={ruleSet.rule_set_id}>
                            Version {ruleSetVersionName(ruleSet)} ({ruleSet.status}, {ruleSet.term_count} terms)
                          </option>
                        ))}
                      </select>
                    </label>
                    {ruleSets.length === 0 && (
                      <p id="rule-set-empty-hint" className="text-sm mt-1 text-error">
                        No Rule Sets exist yet. Mine one in <Link to="/rule-sets" className="underline">Rule Sets</Link> before submitting.
                      </p>
                    )}
                  </div>
                  <label className="block sm:col-span-2">
                    <span className={labelClass}>Seeds</span>
                    <input type="text" value={seeds} onChange={(event) => setSeeds(event.target.value)} disabled={submitting} className={fieldClass} />
                    <span className="block text-xs mt-1 text-on-surface-variant">Ten distinct whole numbers, comma-separated. Each seed is used by one guided and one unguided run.</span>
                  </label>
                  {CONFIG_FIELDS.map(({ key, label, hint, step }) => (
                    <label key={key} className="block">
                      <span className={labelClass}>{label}</span>
                      <input type="number" step={step} required value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} disabled={submitting} className={fieldClass} />
                      <span className="block text-xs mt-1 text-on-surface-variant">{hint}</span>
                    </label>
                  ))}
                  <label className="block">
                    <span className={labelClass}>Stagnation limit</span>
                    <input type="number" step="1" value={draft.stagnation_limit} onChange={(event) => setDraft({ ...draft, stagnation_limit: event.target.value })} disabled={submitting} className={fieldClass} />
                    <span className="block text-xs mt-1 text-on-surface-variant">Greater than 0; leave blank for no limit</span>
                  </label>
                  <label className="block">
                    <span className={labelClass}>Meeting day patterns</span>
                    <input type="text" value={draft.meeting_day_patterns} onChange={(event) => setDraft({ ...draft, meeting_day_patterns: event.target.value })} disabled={submitting} className={fieldClass} />
                    <span className="block text-xs mt-1 text-on-surface-variant">Comma-separated, for example MW, TTh</span>
                  </label>
                </div>
                <p className="text-sm text-on-surface-variant">Prefilled from the active GA configuration. Every run uses these values with only the random seed replaced.</p>
                {terms.length === 0 && <p className="text-sm text-error">No terms exist yet. Create one in Term Setup before submitting.</p>}
                <ErrorBox lines={submitError} />
                <div className="flex justify-end">
                  <button type="submit" disabled={submitting || !termId || !ruleSetId} className="flex items-center gap-2 px-5 py-2 rounded-lg bg-surface-tint text-white font-bold disabled:opacity-60">
                    <FlaskConical className="w-4 h-4" /> {submitting ? "Submitting..." : "Submit matched evaluation"}
                  </button>
                </div>
              </form>
            )}

            {jobId && (
              <section aria-label="Evaluation job" className="border border-outline-variant rounded-xl bg-surface-container-lowest p-6 space-y-4">
                <div className="flex flex-wrap justify-between gap-2">
                  <h2 className="font-headline-md text-xl break-all">Job {jobId}</h2>
                  {job && <span data-testid="job-status" className="font-bold uppercase">{job.status.replace("_", " ")}</span>}
                </div>
                <ErrorBox lines={jobError} />
                {!job && jobError.length === 0 && <p className="text-on-surface-variant">Reading job status...</p>}
                {job && (
                  <>
                    <p role="status">{STATE_TEXT[job.status]}</p>
                    <dl className="grid gap-2 sm:grid-cols-3 text-sm">
                      <div><dt className={labelClass}>Submitted</dt><dd>{formatTimestamp(job.submitted_at)}</dd></div>
                      <div><dt className={labelClass}>Started</dt><dd>{formatTimestamp(job.started_at)}</dd></div>
                      <div><dt className={labelClass}>Finished</dt><dd>{formatTimestamp(job.completed_at)}</dd></div>
                    </dl>
                    {job.error && <ErrorBox lines={[`${job.error.message} (${job.error.code})`]} />}
                    {job.result && (
                      <p>
                        Rule Set version {job.result.rule_set_version}. Executed successfully: {job.result.succeeded_run_count}. Execution failed: {job.result.failed_run_count}.
                      </p>
                    )}
                    {terminal && batchId === null && (
                      <p>No evaluation batch was recorded for this job, so there is no evidence to export.</p>
                    )}
                    {batchId && (
                      <>
                        <p className="text-sm break-all">Evaluation batch {batchId}</p>
                        {counts && (
                          <dl className="grid gap-2 sm:grid-cols-3 text-sm">
                            <div><dt className={labelClass}>Planned runs</dt><dd>{counts.planned}</dd></div>
                            <div><dt className={labelClass}>Started runs</dt><dd>{counts.started}</dd></div>
                            <div><dt className={labelClass}>Completed runs</dt><dd>{counts.completed}</dd></div>
                          </dl>
                        )}
                        {evidenceError.length > 0 && (
                          <div className="space-y-2">
                            <ErrorBox lines={evidenceError} />
                            <button type="button" onClick={() => setEvidenceAttempt((attempt) => attempt + 1)} className="px-4 py-2 border border-outline-variant rounded-lg">
                              Reload evidence
                            </button>
                          </div>
                        )}
                        <div className="flex flex-wrap gap-2">
                          {EXPORT_FORMATS.map(({ format, label }) => (
                            <button key={format} type="button" onClick={() => void download(format)} disabled={downloading !== null} className="flex items-center gap-2 px-4 py-2 border border-outline-variant rounded-lg disabled:opacity-60">
                              <Download className="w-4 h-4" /> {label}
                            </button>
                          ))}
                        </div>
                        <ErrorBox lines={downloadError?.batchId === batchId ? downloadError.lines : []} />
                        <p className="text-xs text-on-surface-variant">
                          Evidence contains internal faculty and scheduling data; keep it private. There is no automatic retry:
                          a new submission is a new execution, and earlier evidence must be retained.
                        </p>
                        {evidence && (
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm text-left">
                              <thead>
                                <tr className={labelClass}>
                                  <th className="p-2">Pair</th><th className="p-2">Mode</th><th className="p-2">Seed</th>
                                  <th className="p-2">Execution</th><th className="p-2">Execution state</th><th className="p-2">Termination</th><th className="p-2">Assigned / required</th>
                                  <th className="p-2">Conflict errors</th><th className="p-2">Feasible schedule</th><th className="p-2">Message</th>
                                </tr>
                              </thead>
                              <tbody>
                                {evidence.runs.map((run) => (
                                  <tr key={`${run.pair_index}-${run.mode}`} className="border-t border-outline-variant/40">
                                    <td className="p-2 tabular-nums">{run.pair_index}</td>
                                    <td className="p-2">{run.mode}</td>
                                    <td className="p-2 tabular-nums">{Number.isSafeInteger(run.random_seed) ? run.random_seed : `${run.random_seed} (rounded; see the downloaded evidence)`}</td>
                                    <td className="p-2">{run.status in UNFINISHED_MESSAGE ? "failed" : run.status}</td>
                                    <td className="p-2">{run.status}</td>
                                    <td className="p-2">{run.termination_reason ?? "—"}</td>
                                    <td className="p-2 tabular-nums">{run.assigned_class_count ?? "—"} / {run.required_class_count ?? "—"}</td>
                                    <td className="p-2 tabular-nums">{run.hard_conflict_count ?? "—"}</td>
                                    <td className="p-2">
                                      {run.status !== "succeeded" ? "—" : run.validation_report ? (run.validation_report.is_export_eligible ? "Yes" : "No") : "Not validated"}
                                    </td>
                                    <td className="p-2">{UNFINISHED_MESSAGE[run.status] ?? run.error_message ?? ""}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
              </section>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
