import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type JobDetail, type JobHistoryItem, type JobHistoryPage as HistoryResponse } from "../api/client";
import JobHistoryPage from "./JobHistoryPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: { ...original.api, listJobs: vi.fn(), getJobDetail: vi.fn(), deleteJobs: vi.fn(), validateSchedule: vi.fn() },
  };
});

const role = vi.hoisted(() => ({ value: "scheduling_committee" }));
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "u-1", username: "sched", role: role.value }, logout: vi.fn() }),
}));
vi.mock("../hooks/useActiveTerm", () => ({ useActiveTerm: () => ({ term: null, loading: false }) }));

function item(overrides: Partial<JobHistoryItem> = {}): JobHistoryItem {
  return {
    job_id: "11111111-aaaa-bbbb-cccc-000000000001",
    kind: "ga",
    status: "succeeded",
    submitted_at: "2026-10-10T02:00:00Z",
    started_at: "2026-10-10T02:00:01Z",
    completed_at: "2026-10-10T02:00:09Z",
    error: null,
    guidance: { mode: "guided", rule_set_id: "rs-1", rule_set_version: 4 },
    scope: { term_id: "t-1", academic_year: "2025-2026", term_number: 1, block_id: null },
    display_id: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  role.value = "scheduling_committee";
});

function renderPage() {
  return render(<MemoryRouter><JobHistoryPage /></MemoryRouter>);
}

it("lists jobs with distinct state labels and pages through results", async () => {
  vi.mocked(api.listJobs).mockResolvedValue({
    items: [
      item(),
      item({ job_id: "22222222-0000-0000-0000-000000000000", status: "timed_out", error: { code: "execution_timed_out", message: "Too slow." } }),
      item({ job_id: "33333333-0000-0000-0000-000000000000", status: "queued", completed_at: null, started_at: null }),
    ],
    total: 45,
    limit: 20,
    offset: 0,
  });
  const user = userEvent.setup();
  renderPage();

  const table = await screen.findByRole("table");
  expect(within(table).getByText("Succeeded")).toBeInTheDocument();
  expect(within(table).getByText("Timed out")).toBeInTheDocument();
  expect(within(table).getByText("Queued")).toBeInTheDocument();
  expect(within(table).getAllByText("Rule set v4")).toHaveLength(3);
  expect(screen.getByText("1–20 of 45")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 20 }));

  await user.selectOptions(screen.getByLabelText("Status"), "failed");
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 0, status: "failed" }));

  await user.type(screen.getByLabelText(/Search job ID/), "ga_infeasible");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 0, status: "failed", q: "ga_infeasible" }));
});

it("offers the ARM filter only to administrators", async () => {
  vi.mocked(api.listJobs).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  const { unmount } = renderPage();
  await screen.findByText("No jobs match these filters.");
  expect(within(screen.getByLabelText("Kind")).queryByRole("option", { name: "ARM" })).toBeNull();
  unmount();

  role.value = "department_administrator";
  renderPage();
  await screen.findByText("No jobs match these filters.");
  expect(within(screen.getByLabelText("Kind")).getByRole("option", { name: "ARM" })).toBeInTheDocument();
});

it("shows provenance and runs the independent validator on a retained candidate", async () => {
  vi.mocked(api.listJobs).mockResolvedValue({ items: [item()], total: 1, limit: 20, offset: 0 });
  const detail: JobDetail = {
    ...item(),
    config: { random_seed: 42, population_size: 10 },
    result: {
      candidate: { schedule_id: "sched-9", assignments: [{}, {}] },
      metrics: { termination_reason: "stagnation", hard_conflict_count: 0 },
    },
  };
  vi.mocked(api.getJobDetail).mockResolvedValue(detail);
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 2,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: true,
  });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText("sched-9")).toBeInTheDocument();
  expect(within(panel).getByText("stagnation")).toBeInTheDocument();
  expect(within(panel).getAllByText("42").length).toBeGreaterThan(0);

  await user.click(within(panel).getByRole("button", { name: "Run independent validation" }));
  const report = await within(panel).findByLabelText("Validation report");
  expect(api.validateSchedule).toHaveBeenCalledWith("sched-9");
  expect(within(report).getByText("Yes")).toBeInTheDocument();
});

it("explains a failed job's error", async () => {
  const failed = item({ status: "failed", completed_at: "2026-10-10T02:00:05Z", error: { code: "ga_infeasible", message: "No feasible schedule." } });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [failed], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue({ ...failed, config: null, result: null });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  expect(await screen.findByText(/No feasible schedule\./)).toBeInTheDocument();
  expect(screen.getByText(/Failed \(ga_infeasible\)/)).toBeInTheDocument();
});

function detailOf(job: JobHistoryItem, extra: Partial<JobDetail> = {}): JobDetail {
  return { ...job, config: null, result: null, ...extra };
}

it("keeps the panel on the last clicked job when responses arrive out of order", async () => {
  const first = item();
  const second = item({ job_id: "22222222-0000-0000-0000-000000000000", status: "failed", error: { code: "x_failed", message: "Second job." } });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [first, second], total: 2, limit: 20, offset: 0 });
  let resolveFirst: (value: JobDetail) => void = () => {};
  vi.mocked(api.getJobDetail).mockImplementation((jobId) =>
    jobId === first.job_id
      ? new Promise<JobDetail>((resolve) => { resolveFirst = resolve; })
      : Promise.resolve(detailOf(second)),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Open job ${first.job_id}` }));
  await user.click(screen.getByRole("button", { name: `Open job ${second.job_id}` }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText(second.job_id)).toBeInTheDocument();
  await act(async () => resolveFirst(detailOf(first)));
  const current = screen.getByRole("region", { name: "Job details" });
  expect(within(current).getByText(second.job_id)).toBeInTheDocument();
  expect(within(current).queryByText(first.job_id)).toBeNull();
});

it("shows ARM counts and omits the metrics section when no known metric exists", async () => {
  role.value = "department_administrator";
  const arm = item({ kind: "arm", guidance: null, scope: null });
  const odd = item({ job_id: "44444444-0000-0000-0000-000000000000", kind: "arm", guidance: null, scope: null });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [arm, odd], total: 2, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockImplementation(async (jobId) =>
    jobId === arm.job_id
      ? detailOf(arm, { result: { rule_set_id: "rs-7", metrics: { stream_count: 4, mined_rule_count: 12 } } })
      : detailOf(odd, { result: { metrics: { unknown_key: 1 } } }),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Open job ${arm.job_id}` }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText("Run metrics")).toBeInTheDocument();
  expect(within(panel).getByText("12")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: `Open job ${odd.job_id}` }));
  await screen.findByText(odd.job_id);
  expect(within(screen.getByRole("region", { name: "Job details" })).queryByText("Run metrics")).toBeNull();
});

it("refreshes an open job's details along with the list", async () => {
  const running = item({ status: "running", completed_at: null });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [running], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail)
    .mockResolvedValueOnce(detailOf(running))
    .mockResolvedValueOnce(detailOf({ ...running, status: "succeeded" }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText("Running")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  // The refreshed panel is a fresh mount, so query it again.
  await waitFor(() => expect(within(screen.getByRole("region", { name: "Job details" })).getByText("Succeeded")).toBeInTheDocument());
});

it("drops stale rows and paging when a reload fails", async () => {
  vi.mocked(api.listJobs)
    .mockResolvedValueOnce({ items: [item()], total: 45, limit: 20, offset: 0 })
    .mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup();
  renderPage();

  await screen.findByText("1–20 of 45");
  await user.selectOptions(screen.getByLabelText("Status"), "failed");
  expect(await screen.findByText("Could not load job history.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Open job/ })).toBeNull();
  expect(screen.queryByText("0 jobs")).toBeNull();
  expect(screen.queryByText(/of 45/)).toBeNull();
});

it("clears a validation report when the job is refreshed", async () => {
  const job = item();
  vi.mocked(api.listJobs).mockResolvedValue({ items: [job], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(job, { result: { candidate: { schedule_id: "sched-9", assignments: [] } } }));
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 1, assigned_class_count: 1, completeness_percentage: 100,
    unassigned_class_ids: [], hard_conflict_count: 0, conflicts: [], export_allowed: true,
  });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  await user.click(await screen.findByRole("button", { name: "Run independent validation" }));
  await screen.findByLabelText("Validation report");
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  await waitFor(() => expect(api.getJobDetail).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByLabelText("Validation report")).toBeNull());
});

it("steps back to the last page when the current page empties", async () => {
  // The queue drains to 5 jobs while page 2 is being requested.
  let drained = false;
  vi.mocked(api.listJobs).mockImplementation(async (filters) => {
    if (filters?.offset === 20) {
      drained = true;
      return { items: [], total: 5, limit: 20, offset: 20 };
    }
    return { items: [item()], total: drained ? 5 : 45, limit: 20, offset: 0 };
  });
  const user = userEvent.setup();
  renderPage();

  await screen.findByText("1–20 of 45");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 0 }));
  expect(await screen.findByText("1–5 of 5")).toBeInTheDocument();
});

it("keeps unused seed fields out of an evaluation's configuration snapshot", async () => {
  const evaluation = item({ kind: "evaluation" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [evaluation], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(evaluation, {
    config: { random_seed: "0", population_size: 10, seeds: ["1", "2"] },
  }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText("population size")).toBeInTheDocument();
  expect(within(panel).queryByText("random seed")).toBeNull();
  expect(within(panel).queryByText("seeds")).toBeNull();
  expect(within(panel).getByText("1, 2")).toBeInTheDocument();
});

it("keeps a validation report when the open job is clicked again", async () => {
  const job = item();
  vi.mocked(api.listJobs).mockResolvedValue({ items: [job], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(job, { result: { candidate: { schedule_id: "sched-9", assignments: [] } } }));
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 1, assigned_class_count: 1, completeness_percentage: 100,
    unassigned_class_ids: [], hard_conflict_count: 0, conflicts: [], export_allowed: true,
  });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  await user.click(await screen.findByRole("button", { name: "Run independent validation" }));
  await screen.findByLabelText("Validation report");
  await user.click(screen.getByRole("button", { name: /Open job/ }));
  await waitFor(() => expect(api.getJobDetail).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Validation report")).toBeInTheDocument();
});

it("hides the previous job while another job's details load", async () => {
  const first = item();
  const second = item({ job_id: "22222222-0000-0000-0000-000000000000" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [first, second], total: 2, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockImplementation((jobId) =>
    jobId === first.job_id
      ? Promise.resolve(detailOf(first, { result: { candidate: { schedule_id: "sched-1", assignments: [] } } }))
      : new Promise<JobDetail>(() => {}),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Open job ${first.job_id}` }));
  await screen.findByRole("button", { name: "Run independent validation" });
  await user.click(screen.getByRole("button", { name: `Open job ${second.job_id}` }));
  expect(screen.getByText("Loading job details...")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Job details" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run independent validation" })).toBeNull();
});

it("returns to the first page when the filtered jobs all disappear", async () => {
  let emptied = false;
  vi.mocked(api.listJobs).mockImplementation(async (filters) => {
    if (filters?.offset === 20) {
      emptied = true;
      return { items: [], total: 0, limit: 20, offset: 20 };
    }
    return emptied
      ? { items: [], total: 0, limit: 20, offset: 0 }
      : { items: [item()], total: 45, limit: 20, offset: 0 };
  });
  const user = userEvent.setup();
  renderPage();

  await screen.findByText("1–20 of 45");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 0 }));
  await screen.findByText("No jobs match these filters.");
  expect(screen.getByRole("button", { name: "Previous" })).toHaveAttribute("aria-disabled", "true");
});

it("marks the open job's row with aria-current", async () => {
  const first = item();
  const second = item({ job_id: "22222222-0000-0000-0000-000000000000" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [first, second], total: 2, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockImplementation(async (jobId) => detailOf(jobId === first.job_id ? first : second));
  const user = userEvent.setup();
  renderPage();

  const firstButton = await screen.findByRole("button", { name: `Open job ${first.job_id}` });
  await user.click(firstButton);
  await screen.findByRole("region", { name: "Job details" });
  expect(firstButton).toHaveAttribute("aria-current", "true");
  expect(screen.getByRole("button", { name: `Open job ${second.job_id}` })).not.toHaveAttribute("aria-current");
});

it("keeps Previous and Next focusable while loading and ignores presses then", async () => {
  let finish: (value: HistoryResponse) => void = () => {};
  vi.mocked(api.listJobs)
    .mockResolvedValueOnce({ items: [item()], total: 45, limit: 20, offset: 0 })
    .mockImplementationOnce(() => new Promise<HistoryResponse>((done) => { finish = done; }));
  const user = userEvent.setup();
  renderPage();

  await screen.findByText("1–20 of 45");
  const previous = screen.getByRole("button", { name: "Previous" });
  const next = screen.getByRole("button", { name: "Next" });
  // First page: Previous is announced unavailable and ignored, but stays focusable.
  expect(previous).toHaveAttribute("aria-disabled", "true");
  expect(previous).not.toBeDisabled();
  await user.click(previous);
  expect(api.listJobs).toHaveBeenCalledTimes(1);

  await user.click(next);
  expect(api.listJobs).toHaveBeenCalledTimes(2);
  expect(next).not.toBeDisabled();
  expect(next).toHaveAttribute("aria-disabled", "true");
  expect(previous).toHaveAttribute("aria-disabled", "true");
  await user.click(next);
  await user.click(previous);
  expect(api.listJobs).toHaveBeenCalledTimes(2);
  expect(previous).toHaveFocus();

  await act(async () => finish({ items: [item()], total: 45, limit: 20, offset: 20 }));
  await waitFor(() => expect(previous).toHaveAttribute("aria-disabled", "false"));
  expect(next).toHaveAttribute("aria-disabled", "false");
});

it("keeps Refresh focusable while loading and ignores presses then", async () => {
  let finish: (value: HistoryResponse) => void = () => {};
  vi.mocked(api.listJobs)
    .mockResolvedValueOnce({ items: [item()], total: 1, limit: 20, offset: 0 })
    .mockImplementationOnce(() => new Promise<HistoryResponse>((done) => { finish = done; }));
  const user = userEvent.setup();
  renderPage();

  await screen.findByRole("table");
  const refresh = screen.getByRole("button", { name: /Refresh/ });
  await waitFor(() => expect(refresh).toHaveAttribute("aria-disabled", "false"));
  await user.click(refresh);
  expect(refresh).not.toBeDisabled();
  expect(refresh).toHaveAttribute("aria-disabled", "true");
  await user.click(refresh);
  expect(api.listJobs).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveFocus();
  await act(async () => finish({ items: [item()], total: 1, limit: 20, offset: 0 }));
  await waitFor(() => expect(refresh).toHaveAttribute("aria-disabled", "false"));
});

it("omits the term number from the scope when it is unknown", async () => {
  vi.mocked(api.listJobs).mockResolvedValue({
    items: [item({ scope: { term_id: "t-1", academic_year: "2023-2024", term_number: null, block_id: null } })],
    total: 1, limit: 20, offset: 0,
  });
  renderPage();

  const table = await screen.findByRole("table");
  expect(within(table).getByText("AY 2023-2024 · Term-wide")).toBeInTheDocument();
  expect(within(table).queryByText(/null/)).toBeNull();
});

it("labels evaluation guidance and scope like GA runs", async () => {
  const evaluation = item({ kind: "evaluation", scope: null, guidance: { mode: "guided", rule_set_id: "rs-3", rule_set_version: 3 } });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [evaluation], total: 1, limit: 20, offset: 0 });
  renderPage();

  const table = await screen.findByRole("table");
  expect(within(table).getByText("Rule set v3")).toBeInTheDocument();
  expect(within(table).getByText("Unknown term")).toBeInTheDocument();
});

it("shows an evaluation's exact run seeds and batch outcome", async () => {
  const evaluation = item({ kind: "evaluation" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [evaluation], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(evaluation, {
    config: { random_seed: "0", seeds: ["9223372036854775807", "7"] },
    result: { evaluation_batch_id: "eb-1", succeeded_run_count: 18, failed_run_count: 2 },
  }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText("Run seeds")).toBeInTheDocument();
  expect(within(panel).getAllByText("9223372036854775807, 7").length).toBeGreaterThan(0);
  expect(within(panel).queryByText("Random seed")).toBeNull();
  const outcome = within(panel).getByLabelText("Evaluation outcome");
  expect(within(outcome).getByText("eb-1")).toBeInTheDocument();
  expect(within(outcome).getByText("18")).toBeInTheDocument();
  expect(within(outcome).getByText("2")).toBeInTheDocument();
});

it("shows the retained batch of a failed evaluation", async () => {
  const failed = item({ kind: "evaluation", status: "failed", error: { code: "evaluation_failed", message: "Stopped." } });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [failed], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(failed, {
    evaluation_batch: { evaluation_batch_id: "eb-2", completed_run_count: 5, planned_run_count: 20 },
  }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const outcome = await screen.findByLabelText("Evaluation outcome");
  expect(within(outcome).getByText("eb-2")).toBeInTheDocument();
  expect(within(outcome).getByText("5 / 20")).toBeInTheDocument();
});

it("refreshes the most recently clicked job, not the one on screen", async () => {
  const first = item();
  const second = item({ job_id: "22222222-0000-0000-0000-000000000000", status: "running" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [first, second], total: 2, limit: 20, offset: 0 });
  const pending: ((value: JobDetail) => void)[] = [];
  vi.mocked(api.getJobDetail).mockImplementation((jobId) =>
    jobId === first.job_id
      ? Promise.resolve(detailOf(first))
      : new Promise<JobDetail>((resolve) => { pending.push(resolve); }),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Open job ${first.job_id}` }));
  await screen.findByRole("region", { name: "Job details" });
  await user.click(screen.getByRole("button", { name: `Open job ${second.job_id}` }));
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  expect(api.getJobDetail).toHaveBeenLastCalledWith(second.job_id);
  await act(async () => pending.forEach((resolve) => resolve(detailOf(second))));
  const panel = screen.getByRole("region", { name: "Job details" });
  expect(within(panel).getByText(second.job_id)).toBeInTheDocument();
});

it("says so when the published candidate schedule was deleted", async () => {
  vi.mocked(api.listJobs).mockResolvedValue({ items: [item()], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue({
    ...item(),
    config: { random_seed: 42, population_size: 10 },
    result: { candidate: { schedule_id: "sched-9", assignments: [{}] }, metrics: {} },
  });
  vi.mocked(api.validateSchedule).mockRejectedValue(
    new ApiError(404, "NOT_FOUND", "The requested schedule was not found."),
  );
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /Open job/ }));
  const panel = await screen.findByRole("region", { name: "Job details" });
  expect(within(panel).getByText(/Published candidate schedule/)).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: "Run independent validation" }));

  expect(await within(panel).findByText(/schedule was deleted/)).toBeInTheDocument();
});

it("shows a retryable load error instead of an empty job table", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listJobs).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load job history."));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load job history.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("0 jobs")).not.toBeInTheDocument();

  vi.mocked(api.listJobs).mockResolvedValue({ items: [item()], total: 1, limit: 20, offset: 0 });
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("table")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("shows no empty table or job count while a failed load is being retried", async () => {
  vi.mocked(api.listJobs).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load job history."));
  const user = userEvent.setup();
  renderPage();
  await screen.findByRole("alert");

  vi.mocked(api.listJobs).mockReturnValueOnce(new Promise(() => {}));
  await user.click(screen.getByRole("button", { name: "Retry" }));

  expect(screen.getByRole("button", { name: /Retrying/ })).toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("0 jobs")).not.toBeInTheDocument();
  expect(screen.queryByText("No jobs match these filters.")).not.toBeInTheDocument();
});

it("keeps the loaded rows when a refresh of the same query fails", async () => {
  vi.mocked(api.listJobs)
    .mockResolvedValueOnce({ items: [item()], total: 1, limit: 20, offset: 0 })
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load job history."));
  const user = userEvent.setup();
  renderPage();
  await screen.findByRole("button", { name: /Open job/ });

  await user.click(screen.getByRole("button", { name: /Refresh/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load job history.");
  expect(screen.getByRole("button", { name: /Open job/ })).toBeInTheDocument();
});

it("shows loading, not an empty table or job count, while the first load is pending", () => {
  vi.mocked(api.listJobs).mockReturnValue(new Promise(() => {}));
  renderPage();

  expect(screen.getByText("Loading job history...")).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("0 jobs")).not.toBeInTheDocument();
});

it("shows the error, not an empty-result claim, when a refresh fails over no rows", async () => {
  vi.mocked(api.listJobs)
    .mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 })
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load job history."));
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("No jobs match these filters.");

  await user.click(screen.getByRole("button", { name: /Refresh/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load job history.");
  expect(screen.queryByText("No jobs match these filters.")).not.toBeInTheDocument();
  expect(screen.queryByText("0 jobs")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

it("bulk deletes finished jobs and reports the ones it kept", async () => {
  const done = item();
  const failed = item({ job_id: "22222222-0000-0000-0000-000000000000", status: "failed" });
  const running = item({ job_id: "33333333-0000-0000-0000-000000000000", status: "running", completed_at: null });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [done, failed, running], total: 3, limit: 20, offset: 0 });
  vi.mocked(api.deleteJobs).mockResolvedValue({
    deleted: [done.job_id],
    skipped: [{ job_id: failed.job_id, reason: "in_use" }],
  });
  const user = userEvent.setup();
  renderPage();

  await screen.findByRole("table");
  expect(screen.getByLabelText(`Select job ${running.job_id}`)).toBeDisabled();
  expect(screen.queryByRole("button", { name: `Delete job ${running.job_id}` })).not.toBeInTheDocument();

  await user.click(screen.getByLabelText("Select all finished jobs on this page"));
  expect(screen.getByText("2 selected")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Delete selected/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Delete 2 jobs")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));

  expect(api.deleteJobs).toHaveBeenCalledWith([done.job_id, failed.job_id]);
  expect(await screen.findByText(/Deleted 1 job\. Kept 1: still used by a schedule/)).toBeInTheDocument();
  await waitFor(() => expect(api.listJobs).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("deletes a single job from its row", async () => {
  const done = item();
  vi.mocked(api.listJobs).mockResolvedValue({ items: [done], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.deleteJobs).mockResolvedValue({ deleted: [done.job_id], skipped: [] });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Delete job ${done.job_id}` }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));

  expect(api.deleteJobs).toHaveBeenCalledWith([done.job_id]);
  expect(await screen.findByText("Deleted 1 job.")).toBeInTheDocument();
});

it("labels jobs by their readable ID and opens details in a dialog", async () => {
  const job = item({ display_id: "2601CS1BV101" });
  vi.mocked(api.listJobs).mockResolvedValue({ items: [job], total: 1, limit: 20, offset: 0 });
  vi.mocked(api.getJobDetail).mockResolvedValue(detailOf(job));
  const user = userEvent.setup();
  renderPage();

  expect(await screen.findByRole("table")).toHaveTextContent("2601CS1BV101");
  const opener = screen.getByRole("button", { name: `Open job ${job.job_id}` });
  await user.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "GA job 2601CS1BV101" });
  expect(within(dialog).getByRole("region", { name: "Job details" })).toBeInTheDocument();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(opener).toHaveFocus();
});

it("does not reopen the dialog when details arrive after it was closed", async () => {
  const job = item();
  vi.mocked(api.listJobs).mockResolvedValue({ items: [job], total: 1, limit: 20, offset: 0 });
  let resolve: (value: JobDetail) => void = () => {};
  vi.mocked(api.getJobDetail).mockReturnValue(new Promise<JobDetail>((done) => { resolve = done; }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: `Open job ${job.job_id}` }));
  expect(await screen.findByRole("dialog", { name: "Job details" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Close dialog" }));
  await act(async () => resolve(detailOf(job)));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("offers First from page 3 and Last until two pages from the end", async () => {
  // 7 pages of 20.
  vi.mocked(api.listJobs).mockImplementation(async (filters) => ({
    items: [item()], total: 140, limit: 20, offset: filters?.offset ?? 0,
  }));
  const user = userEvent.setup();
  renderPage();
  await screen.findByRole("table");

  expect(screen.queryByRole("button", { name: "First" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Last" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 120 }));
  await screen.findByText("121–140 of 140");
  expect(screen.queryByRole("button", { name: "Last" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "Previous" }));
  await screen.findByText("101–120 of 140");
  expect(screen.queryByRole("button", { name: "Last" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Previous" }));
  await screen.findByText("81–100 of 140");
  expect(screen.getByRole("button", { name: "Last" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "First" }));
  await waitFor(() => expect(api.listJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 0 }));
  await screen.findByText("1–20 of 140");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("21–40 of 140");
  expect(screen.queryByRole("button", { name: "First" })).toBeNull();
});
