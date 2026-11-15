import { Profiler } from "react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  ApiError,
  api,
  type EvaluationEvidence,
  type EvaluationJobStatusResponse,
  type GAConfiguration,
} from "../api/client";
import MatchedEvaluationPage from "./MatchedEvaluationPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listTerms: vi.fn(),
      listRuleSets: vi.fn(),
      getGAConfiguration: vi.fn(),
      submitEvaluation: vi.fn(),
      getEvaluationJob: vi.fn(),
      getEvaluationEvidence: vi.fn(),
      exportEvaluation: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "admin-1", username: "admin", role: "department_administrator" }, logout: vi.fn() }),
}));

vi.mock("./CommitteeTimetablePage", () => ({ saveFile: vi.fn() }));

const configuration: GAConfiguration = {
  version: 3,
  created_at: null,
  created_by_user_id: null,
  config: {
    population_size: 50,
    max_generations: 100,
    crossover_rate: 0.8,
    mutation_rate: 0.2,
    gene_mutation_rate: 0.1,
    tournament_size: 3,
    elite_count: 1,
    stagnation_limit: 20,
    random_seed: 7,
    meeting_day_patterns: ["MWF", "TTh"],
  },
};

function job(overrides: Partial<EvaluationJobStatusResponse>): EvaluationJobStatusResponse {
  return {
    job_id: "job-1",
    kind: "evaluation",
    status: "queued",
    submitted_at: "2026-10-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    result: null,
    error: null,
    evaluation_batch: null,
    ...overrides,
  };
}

function run(pair: number, mode: "guided" | "unguided", status: EvaluationEvidence["runs"][number]["status"], feasible?: boolean) {
  const done = status === "succeeded";
  return {
    pair_index: pair,
    mode,
    random_seed: pair,
    status,
    termination_reason: done ? "MAX_GENERATIONS" : null,
    required_class_count: done ? 10 : null,
    assigned_class_count: done ? (feasible ? 10 : 9) : null,
    hard_conflict_count: done ? 0 : null,
    error_message: status === "failed" ? "Candidate failed validation." : null,
    validation_report: done ? { is_export_eligible: Boolean(feasible) } : null,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listTerms).mockResolvedValue({
    items: [
      { term_id: "term-old", academic_year: "2024-2025", term_number: 1, is_active: false },
      { term_id: "term-1", academic_year: "2025-2026", term_number: 1, is_active: true },
    ],
  });
  vi.mocked(api.listRuleSets).mockResolvedValue({
    items: [{
      rule_set_id: "rs-1", version: 2, status: "draft", generated_at: "2026-01-01T00:00:00Z",
      activated_at: null, notes: null, term_count: 4, rule_count: 12,
    }],
  });
  vi.mocked(api.getGAConfiguration).mockResolvedValue(configuration);
});

afterEach(() => vi.useRealTimers());

function renderPage(path = "/evaluations") {
  render(<MemoryRouter initialEntries={[path]}><MatchedEvaluationPage /></MemoryRouter>);
}

it("submits the selected term, rule set, seeds and config, then follows the job", async () => {
  const user = userEvent.setup();
  // A saved seed outside 64 bits must not leak into the evaluation's base config.
  vi.mocked(api.getGAConfiguration).mockResolvedValue({
    ...configuration,
    config: { ...configuration.config, random_seed: 2 ** 70 },
  });
  vi.mocked(api.submitEvaluation).mockResolvedValue({ job_id: "job-1", kind: "evaluation", status: "queued" });
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({ status: "queued" }));
  renderPage();

  const population = await screen.findByLabelText(/Population size/);
  await user.clear(population);
  await user.type(population, "80");
  const seeds = screen.getByLabelText(/Seeds/);
  await user.clear(seeds);
  await user.type(seeds, "10, 11, 12, 13, 14, 15, 16, 17, 18, 19");
  expect(screen.queryByLabelText(/Random seed/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Submit matched evaluation/ }));

  expect(api.submitEvaluation).toHaveBeenCalledWith({
    term_id: "term-1",
    rule_set_id: "rs-1",
    seeds: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    config: { ...configuration.config, population_size: 80, random_seed: 10 },
  });
  expect(await screen.findByTestId("job-status")).toHaveTextContent("queued");
  expect(api.getEvaluationJob).toHaveBeenCalledWith("job-1");
  // Queued: export is refused by the backend, so no download is offered.
  expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
}, 15_000); // Types forty characters; the default 5 s is tight under the parallel suite.

it("refuses seeds the browser cannot send exactly, without calling the backend", async () => {
  const user = userEvent.setup();
  renderPage();
  const seeds = await screen.findByLabelText(/Seeds/);
  await user.clear(seeds);
  await user.type(seeds, "0, 1, 9223372036854775807");
  await user.click(screen.getByRole("button", { name: /Submit matched evaluation/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/send them exactly/);
  expect(api.submitEvaluation).not.toHaveBeenCalled();
});

it("surfaces a holdout refusal with its withheld periods", async () => {
  const user = userEvent.setup();
  vi.mocked(api.submitEvaluation).mockRejectedValue(new ApiError(
    409, "RULE_SET_INCLUDES_HOLDOUT", "The selected rule set was mined from holdout or newer terms.",
    { withheld_periods: ["2024-2025", "2025-2026"] },
  ));
  renderPage();
  await user.click(await screen.findByRole("button", { name: /Submit matched evaluation/ }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("RULE_SET_INCLUDES_HOLDOUT");
  expect(alert).toHaveTextContent("Withheld periods: 2024-2025, 2025-2026");
});

it("surfaces provenance and validation refusals as the backend reports them", async () => {
  const user = userEvent.setup();
  vi.mocked(api.submitEvaluation)
    .mockRejectedValueOnce(new ApiError(409, "RULE_SET_PROVENANCE_UNKNOWN", "Source terms are missing."))
    .mockRejectedValueOnce(new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
      errors: [{ field: "body.seeds", type: "value_error" }],
    }));
  renderPage();
  const submit = await screen.findByRole("button", { name: /Submit matched evaluation/ });

  await user.click(submit);
  expect(await screen.findByRole("alert")).toHaveTextContent("RULE_SET_PROVENANCE_UNKNOWN");
  await user.click(submit);
  expect(await screen.findByText(
    "body.seeds: value_error (the backend requires exactly ten distinct seeds, each a signed 64-bit integer)",
  )).toBeInTheDocument();
});

it("polls a running job until it succeeds, then separates execution from feasibility", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.getEvaluationJob)
    .mockResolvedValueOnce(job({ status: "running", started_at: "2026-10-01T00:01:00Z" }))
    .mockResolvedValue(job({
      status: "succeeded",
      result: {
        evaluation_batch_id: "batch-1", rule_set_id: "rs-1", rule_set_version: 2,
        input_fingerprint: "abc", succeeded_run_count: 2, failed_run_count: 0,
      },
    }));
  vi.mocked(api.getEvaluationEvidence).mockResolvedValue({
    schema_version: 1, evaluation_batch_id: "batch-1", job_status: "succeeded",
    planned_run_count: 2, completed_run_count: 2,
    runs: [run(0, "guided", "succeeded", true), run(0, "unguided", "succeeded", false)],
  });
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByTestId("job-status")).toHaveTextContent("running");
  expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

  expect(await screen.findByTestId("job-status")).toHaveTextContent("succeeded");
  expect(screen.getByRole("status")).toHaveTextContent(/not a claim that its schedule is feasible/);
  expect(await screen.findByText("Executed successfully: 2. Execution failed: 0.", { exact: false })).toBeInTheDocument();
  const rows = await screen.findAllByRole("row");
  expect(within(rows[1]).getByText("Yes")).toBeInTheDocument();
  expect(within(rows[2]).getByText("No")).toBeInTheDocument();
  expect(api.getEvaluationEvidence).toHaveBeenCalledWith("batch-1");

  // Terminal: polling stops.
  const polls = vi.mocked(api.getEvaluationJob).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(api.getEvaluationJob).toHaveBeenCalledTimes(polls);
});

it("shows every slot of an interrupted batch and offers its evidence for download", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({
    status: "timed_out",
    error: { code: "job_timeout", message: "The job exceeded its timeout." },
    evaluation_batch: { evaluation_batch_id: "batch-2", completed_run_count: 1, planned_run_count: 4 },
  }));
  vi.mocked(api.getEvaluationEvidence).mockResolvedValue({
    schema_version: 1, evaluation_batch_id: "batch-2", job_status: "timed_out",
    planned_run_count: 4, completed_run_count: 1,
    runs: [
      { ...run(0, "guided", "failed"), validation_report: { is_export_eligible: true } },
      run(0, "unguided", "running"),
      run(1, "guided", "pending"), run(1, "unguided", "pending"),
    ],
  });
  vi.mocked(api.exportEvaluation)
    .mockResolvedValueOnce({ blob: new Blob(["x"]), filename: "chronos-evaluation-batch-2.csv" })
    .mockRejectedValueOnce(new ApiError(409, "EVALUATION_RUNNING", "Wait for the evaluation job to finish."));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/The job exceeded its timeout/)).toBeInTheDocument();
  const counts = await screen.findByText("Planned runs");
  const list = counts.closest("dl") as HTMLElement;
  // The started count comes from the evidence request, which resolves after the job renders.
  expect(await within(list).findByText("2")).toBeInTheDocument(); // started
  expect(within(list).getByText("4")).toBeInTheDocument(); // planned
  expect(within(list).getByText("1")).toBeInTheDocument(); // completed
  // Presented as the backend export does: failed, with the native state kept.
  expect(screen.getAllByText("pending")).toHaveLength(2);
  expect(screen.getByText("running")).toBeInTheDocument();
  expect(screen.getAllByText("failed")).toHaveLength(5); // four Execution cells plus one native state
  expect(screen.getAllByText("Run not started before job terminated.")).toHaveLength(2);
  expect(screen.getByText("Run interrupted before outcome checkpoint.")).toBeInTheDocument();
  expect(screen.getByText("Candidate failed validation.")).toBeInTheDocument();
  // A failed run never reads as a feasible schedule.
  expect(screen.queryByText("Yes")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /CSV/ }));
  expect(api.exportEvaluation).toHaveBeenCalledWith("batch-2", "csv");
  await user.click(screen.getByRole("button", { name: /JSON evidence/ }));
  expect(await screen.findByText(/EVALUATION_RUNNING/)).toBeInTheDocument();
  expect(api.submitEvaluation).not.toHaveBeenCalled();
});

it("reports a failed job with no batch and offers no downloads", async () => {
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({
    status: "failed",
    error: { code: "execution_error", message: "Worker crashed before the batch was created." },
  }));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/No evaluation batch was recorded/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
  expect(api.getEvaluationEvidence).not.toHaveBeenCalled();
});

it("does not claim every run passed when a succeeded job recorded failed runs", async () => {
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({
    status: "succeeded",
    result: {
      evaluation_batch_id: "batch-3", rule_set_id: "rs-1", rule_set_version: 2,
      input_fingerprint: "abc", succeeded_run_count: 19, failed_run_count: 1,
    },
  }));
  vi.mocked(api.getEvaluationEvidence).mockResolvedValue({
    schema_version: 1, evaluation_batch_id: "batch-3", job_status: "succeeded",
    planned_run_count: 1, completed_run_count: 1, runs: [run(0, "guided", "failed")],
  });
  renderPage("/evaluations?job=job-1");

  const status = await screen.findByRole("status");
  expect(status).not.toHaveTextContent(/every run finished and passed/);
  expect(status).toHaveTextContent(/may still have failed execution/);
  expect(screen.getByText(/Execution failed: 1\./)).toBeInTheDocument();
});

it("stops polling a job the backend refuses", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.getEvaluationJob).mockRejectedValue(
    new ApiError(404, "NOT_FOUND", "The requested job was not found."),
  );
  renderPage("/evaluations?job=missing");

  expect(await screen.findByText(/NOT_FOUND/)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.getEvaluationJob).toHaveBeenCalledTimes(1);
});

it("refuses to present a GA or ARM job as a matched evaluation", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({ kind: "ga", status: "running" }));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText("Job job-1 is a ga job, not a matched evaluation.")).toBeInTheDocument();
  expect(screen.queryByTestId("job-status")).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.getEvaluationJob).toHaveBeenCalledTimes(1);
});

it("does not pull the operator back after they leave during submission", async () => {
  const user = userEvent.setup();
  let accept: (value: { job_id: string; kind: "evaluation"; status: "queued" }) => void = () => {};
  vi.mocked(api.submitEvaluation).mockReturnValue(new Promise((resolve) => { accept = resolve; }));
  render(
    <MemoryRouter initialEntries={["/evaluations"]}>
      <Routes>
        <Route path="/evaluations" element={<MatchedEvaluationPage />} />
        <Route path="/dashboard" element={<p>Dashboard page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: /Submit matched evaluation/ }));
  await user.click(screen.getByRole("link", { name: "Ops Dashboard" }));
  expect(screen.getByText("Dashboard page")).toBeInTheDocument();

  await act(async () => { accept({ job_id: "job-9", kind: "evaluation", status: "queued" }); });
  expect(screen.getByText("Dashboard page")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "GA Evaluation" })).not.toBeInTheDocument();
});

function terminalJob(batchId: string): EvaluationJobStatusResponse {
  return job({
    status: "timed_out",
    error: { code: "job_timeout", message: "The job exceeded its timeout." },
    evaluation_batch: { evaluation_batch_id: batchId, completed_run_count: 3, planned_run_count: 20 },
  });
}

it("does not show an earlier batch's download failure under a newer job", async () => {
  const user = userEvent.setup();
  let refuse: (error: unknown) => void = () => {};
  vi.mocked(api.getEvaluationJob).mockImplementation((id) =>
    Promise.resolve(id === "job-1" ? terminalJob("batch-a") : { ...terminalJob("batch-b"), job_id: id }));
  vi.mocked(api.getEvaluationEvidence).mockRejectedValue(new ApiError(500, "INTERNAL", "Evidence unavailable."));
  vi.mocked(api.exportEvaluation).mockReturnValue(new Promise((_, reject) => { refuse = reject; }));
  vi.mocked(api.submitEvaluation).mockResolvedValue({ job_id: "job-2", kind: "evaluation", status: "queued" });
  renderPage("/evaluations?job=job-1");

  await user.click(await screen.findByRole("button", { name: /CSV/ }));
  await user.click(screen.getByRole("button", { name: /Submit matched evaluation/ }));
  expect(await screen.findByText(/batch-b/, {}, { timeout: 5000 })).toBeInTheDocument();
  await act(async () => { refuse(new ApiError(404, "NOT_FOUND", "Batch A vanished.")); });

  expect(screen.queryByText(/Batch A vanished/)).not.toBeInTheDocument();
});

it("falls back to the job's counts when evidence cannot be read, and reloads on request", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getEvaluationJob).mockResolvedValue(terminalJob("batch-a"));
  vi.mocked(api.getEvaluationEvidence).mockRejectedValue(new ApiError(500, "INTERNAL", "Evidence unavailable."));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/Evidence unavailable/)).toBeInTheDocument();
  const list = screen.getByText("Planned runs").closest("dl") as HTMLElement;
  expect(within(list).getByText("20")).toBeInTheDocument();
  expect(within(list).getByText("3")).toBeInTheDocument();
  expect(within(list).getByText("—")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Reload evidence" }));
  expect(api.getEvaluationEvidence).toHaveBeenCalledTimes(2);
});

it("keeps polling through a transient error and clears it on the next success", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.getEvaluationJob)
    .mockResolvedValueOnce(job({ status: "running" }))
    .mockRejectedValueOnce(new ApiError(503, "UNAVAILABLE", "Backend restarting."))
    .mockResolvedValue(job({ status: "running" }));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByTestId("job-status")).toHaveTextContent("running");
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(await screen.findByText(/Backend restarting/)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.queryByText(/Backend restarting/)).not.toBeInTheDocument();
  expect(api.getEvaluationJob).toHaveBeenCalledTimes(3);
});

it("marks a seed the browser could not read exactly", async () => {
  vi.mocked(api.getEvaluationJob).mockResolvedValue(terminalJob("batch-a"));
  vi.mocked(api.getEvaluationEvidence).mockResolvedValue({
    schema_version: 1, evaluation_batch_id: "batch-a", job_status: "timed_out",
    planned_run_count: 1, completed_run_count: 0,
    runs: [{ ...run(0, "guided", "pending"), random_seed: 2 ** 60 }],
  });
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/rounded; see the downloaded evidence/)).toBeInTheDocument();
});

it("restates the backend's cross-field config rules on a body.config refusal", async () => {
  const user = userEvent.setup();
  vi.mocked(api.submitEvaluation).mockRejectedValue(new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
    errors: [{ field: "body.config", type: "value_error" }],
  }));
  renderPage();
  await user.click(await screen.findByRole("button", { name: /Submit matched evaluation/ }));

  expect(await screen.findByText(/body.config: value_error \(the backend requires tournament size at most the population size/)).toBeInTheDocument();
});

it("restates the meeting day rule on a meeting pattern refusal", async () => {
  const user = userEvent.setup();
  vi.mocked(api.submitEvaluation).mockRejectedValue(new ApiError(422, "VALIDATION_ERROR", "The request contains invalid data.", {
    errors: [{ field: "body.config.meeting_day_patterns", type: "value_error" }],
  }));
  renderPage();
  await user.click(await screen.findByRole("button", { name: /Submit matched evaluation/ }));

  expect(await screen.findByText(
    "body.config.meeting_day_patterns: value_error (the backend requires at least one meeting day pattern, using only M, T, W, Th, F or Sa)",
  )).toBeInTheDocument();
});

it("says why submission is unavailable when there are no terms or rule sets", async () => {
  vi.mocked(api.listTerms).mockResolvedValue({ items: [] });
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [] });
  renderPage();

  expect(await screen.findByText(/No terms exist yet/)).toBeInTheDocument();
  expect(screen.getByText(/No Rule Sets exist yet/)).toBeInTheDocument();
  const select = screen.getByRole("combobox", { name: "Rule Set" });
  expect(select).toHaveDescription(/No Rule Sets exist yet/);
  expect(within(select).getByRole("option", { name: "No Rule Sets available" })).toBeDisabled();
  expect(within(screen.getByText(/No Rule Sets exist yet/)).getByRole("link", { name: "Rule Sets" })).toHaveAttribute("href", "/rule-sets");
  expect(screen.getByRole("button", { name: /Submit matched evaluation/ })).toBeDisabled();
});

it("shows no empty-state hint or placeholder when Rule Sets exist", async () => {
  renderPage();

  const select = await screen.findByRole("combobox", { name: "Rule Set" });
  expect(screen.queryByText(/No Rule Sets exist yet/)).not.toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: "No Rule Sets available" })).not.toBeInTheDocument();
  expect(select).not.toHaveAttribute("aria-describedby");
});

it("treats an empty job parameter as no job", async () => {
  renderPage("/evaluations?job=");

  expect(await screen.findByRole("button", { name: /Submit matched evaluation/ })).toBeInTheDocument();
  expect(api.getEvaluationJob).not.toHaveBeenCalled();
});

it("never shows evidence that belongs to a different batch", async () => {
  vi.mocked(api.getEvaluationJob).mockResolvedValue(terminalJob("batch-b"));
  vi.mocked(api.getEvaluationEvidence).mockResolvedValue({
    schema_version: 1, evaluation_batch_id: "batch-a", job_status: "failed",
    planned_run_count: 99, completed_run_count: 99,
    runs: [{ ...run(0, "guided", "failed"), error_message: "RUN-FROM-BATCH-A" }],
  });
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/Evaluation batch batch-b/)).toBeInTheDocument();
  await act(async () => { await Promise.resolve(); });
  expect(api.getEvaluationEvidence).toHaveBeenCalledWith("batch-b");
  expect(screen.queryByText("RUN-FROM-BATCH-A")).not.toBeInTheDocument();
  expect(screen.queryByText("99")).not.toBeInTheDocument();
  // The job's own counts are shown instead.
  const list = screen.getByText("Planned runs").closest("dl") as HTMLElement;
  expect(within(list).getByText("20")).toBeInTheDocument();
});

it("falls back to a succeeded job's result counts when evidence cannot be read", async () => {
  vi.mocked(api.getEvaluationJob).mockResolvedValue(job({
    status: "succeeded",
    result: {
      evaluation_batch_id: "batch-s", rule_set_id: "rs-1", rule_set_version: 2,
      input_fingerprint: "abc", succeeded_run_count: 18, failed_run_count: 2,
    },
  }));
  vi.mocked(api.getEvaluationEvidence).mockRejectedValue(new ApiError(500, "INTERNAL", "Evidence unavailable."));
  renderPage("/evaluations?job=job-1");

  expect(await screen.findByText(/Evidence unavailable/)).toBeInTheDocument();
  const list = screen.getByText("Planned runs").closest("dl") as HTMLElement;
  expect(within(list).getAllByText("20")).toHaveLength(3);
});

it("never pairs a new job id with the previous job's state, even for one frame", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getEvaluationJob).mockImplementation((id) =>
    id === "job-1" ? Promise.resolve(terminalJob("batch-a")) : new Promise(() => {}));
  vi.mocked(api.getEvaluationEvidence).mockRejectedValue(new ApiError(500, "INTERNAL", "Evidence unavailable."));
  vi.mocked(api.submitEvaluation).mockResolvedValue({ job_id: "job-2", kind: "evaluation", status: "queued" });
  // Profiler callbacks run at commit, before passive effects, so they see the
  // frame a browser could paint before the page's reset effect runs.
  let staleFrame = false;
  render(
    <Profiler id="page" onRender={() => {
      const text = document.body.textContent ?? "";
      if (text.includes("Job job-2") && (text.includes("batch-a") || text.includes("timed out"))) staleFrame = true;
    }}>
      <MemoryRouter initialEntries={["/evaluations?job=job-1"]}><MatchedEvaluationPage /></MemoryRouter>
    </Profiler>,
  );

  expect(await screen.findByText(/Evaluation batch batch-a/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Submit matched evaluation/ }));
  expect(await screen.findByText("Job job-2")).toBeInTheDocument();

  expect(staleFrame).toBe(false);
  expect(screen.getByText("Reading job status...")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
});

it("follows a job pasted with a non-canonical id to its terminal state", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const canonical = "0f8e2c7a-1b2c-4d3e-8f90-a1b2c3d4e5f6";
  vi.mocked(api.getEvaluationJob)
    .mockResolvedValueOnce({ ...job({ status: "running" }), job_id: canonical })
    .mockResolvedValue({ ...terminalJob("batch-a"), job_id: canonical });
  vi.mocked(api.getEvaluationEvidence).mockRejectedValue(new ApiError(500, "INTERNAL", "Evidence unavailable."));
  renderPage(`/evaluations?job=${canonical.toUpperCase()}`);

  expect(await screen.findByTestId("job-status")).toHaveTextContent("running");
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(await screen.findByTestId("job-status")).toHaveTextContent("timed out");
  expect(screen.getByRole("button", { name: /CSV/ })).toBeInTheDocument();
  const polls = vi.mocked(api.getEvaluationJob).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(api.getEvaluationJob).toHaveBeenCalledTimes(polls);
});

it("never pairs a new job id with the previous job's error, even for one frame", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getEvaluationJob).mockImplementation((id) =>
    id === "job-1" ? Promise.resolve(job({ kind: "ga", status: "running" })) : new Promise(() => {}));
  vi.mocked(api.submitEvaluation).mockResolvedValue({ job_id: "job-2", kind: "evaluation", status: "queued" });
  let staleFrame = false;
  render(
    <Profiler id="page" onRender={() => {
      const text = document.body.textContent ?? "";
      if (text.includes("Job job-2") && text.includes("is a ga job")) staleFrame = true;
    }}>
      <MemoryRouter initialEntries={["/evaluations?job=job-1"]}><MatchedEvaluationPage /></MemoryRouter>
    </Profiler>,
  );

  expect(await screen.findByText(/Job job-1 is a ga job/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Submit matched evaluation/ }));
  expect(await screen.findByText("Job job-2")).toBeInTheDocument();

  expect(staleFrame).toBe(false);
  expect(screen.getByText("Reading job status...")).toBeInTheDocument();
  expect(api.getEvaluationJob).toHaveBeenCalledWith("job-2");
});
