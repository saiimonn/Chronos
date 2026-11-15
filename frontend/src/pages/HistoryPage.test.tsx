import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type HistoricalSnapshot } from "../api/client";
import HistoryPage from "./HistoryPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listTerms: vi.fn(),
      listSchedules: vi.fn(),
      getHistoricalSnapshot: vi.fn(),
      captureHistoricalSnapshot: vi.fn(),
      listHistoricalRecords: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { user_id: "admin-1", username: "admin", role: "department_administrator" }, logout: vi.fn() }),
}));

const inactiveTerm = { term_id: "term-1", academic_year: "2025-2026", term_number: 3, is_active: false };
const otherTerm = { term_id: "term-2", academic_year: "2024-2025", term_number: 1, is_active: false };
const master = {
  schedule_id: "master-1",
  term_id: inactiveTerm.term_id,
  schedule_type: "master" as const,
  created_at: "2026-09-01T00:00:00Z",
  finalized_at: "2026-10-01T00:00:00Z",
  assignments: [],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listTerms).mockResolvedValue({ items: [inactiveTerm, otherTerm] });
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [master] });
  vi.mocked(api.getHistoricalSnapshot).mockRejectedValue(new ApiError(404, "HISTORY_NOT_CAPTURED", "This term has not been captured."));
  vi.mocked(api.listHistoricalRecords).mockResolvedValue({ items: [] });
});

afterEach(() => vi.useRealTimers());

const capturedSnapshot = {
  snapshot_id: "snapshot-1",
  source_term_id: inactiveTerm.term_id,
  source_schedule_id: null,
  approved_by_user_id: null,
  academic_year: inactiveTerm.academic_year,
  term_number: inactiveTerm.term_number,
  captured_at: "2026-10-02T00:00:00Z",
  record_count: 3,
};

function pastClass(id: string, faculty: string, code: string, days: string | null, time: string | null, room: string | null) {
  return { historical_record_id: id, source_faculty_id: `id-${faculty}`, faculty_name: faculty, subject_code: code, subject_title: `${code} title`, room_code: room, meeting_days: days, time_slot: time };
}

it("lists a captured term's classes by teacher and by room, ordered by day then time", async () => {
  vi.mocked(api.getHistoricalSnapshot).mockResolvedValue(capturedSnapshot);
  vi.mocked(api.listHistoricalRecords).mockResolvedValue({
    items: [
      pastClass("r1", "CRUZ, ANA", "CS 1101", "TTh", "09:00-10:30", "LB304TC"),
      pastClass("r2", "CRUZ, ANA", "CS 2203", "MW", "13:30-15:00", "LB305TC"),
      pastClass("r3", "REYES, BEN", "IS 3206", "Th", "07:30-09:00", "LB304TC"),
    ],
  });

  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  const cruz = await screen.findByRole("region", { name: "CRUZ, ANA" });
  const cruzRows = within(cruz).getAllByRole("row").slice(1);
  expect(cruzRows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(["CS 2203 CS 2203 title", "CS 1101 CS 1101 title"]);
  expect(api.listHistoricalRecords).toHaveBeenCalledWith(inactiveTerm.term_id);

  await userEvent.click(screen.getByRole("button", { name: "By room" }));
  const room = screen.getByRole("region", { name: "LB304TC" });
  const roomRows = within(room).getAllByRole("row").slice(1);
  // Tuesday's CS 1101 lists before Thursday's IS 3206.
  expect(roomRows.map((row) => within(row).getAllByRole("cell")[3].textContent)).toEqual(["CRUZ, ANA", "REYES, BEN"]);
});

it("keeps two teachers who share a name in separate groups", async () => {
  vi.mocked(api.getHistoricalSnapshot).mockResolvedValue(capturedSnapshot);
  vi.mocked(api.listHistoricalRecords).mockResolvedValue({
    items: [
      pastClass("r1", "CRUZ, ANA", "CS 1101", "M", "07:30-09:00", "LB304TC"),
      { ...pastClass("r2", "CRUZ, ANA", "CS 2203", "T", "07:30-09:00", "LB305TC"), source_faculty_id: "another-cruz" },
    ],
  });

  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  const groups = await screen.findAllByRole("region", { name: "CRUZ, ANA" });
  expect(groups).toHaveLength(2);
});

it("shows teacher and subject only for history without placement", async () => {
  vi.mocked(api.getHistoricalSnapshot).mockResolvedValue(capturedSnapshot);
  vi.mocked(api.listHistoricalRecords).mockResolvedValue({ items: [pastClass("r1", "CRUZ, ANA", "CS 144", null, null, null)] });

  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  expect(await screen.findByText(/Days, times and rooms were not recorded/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "By room" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "CRUZ, ANA" })).toBeInTheDocument();
});

it.each(["success", "refusal"])("keeps a long-running capture recoverable across polling ticks: %s", async (outcome) => {
  vi.useFakeTimers();
  let resolveCapture: (value: HistoricalSnapshot) => void = () => {};
  let rejectCapture: (reason: ApiError) => void = () => {};
  vi.mocked(api.captureHistoricalSnapshot).mockReturnValue(new Promise((resolve, reject) => {
    resolveCapture = resolve;
    rejectCapture = reject;
  }));
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole("button", { name: "Capture immutable history" }));
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Capture history" }));

  const readsBeforeCapture = vi.mocked(api.listTerms).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.listTerms).toHaveBeenCalledTimes(readsBeforeCapture);
  expect(screen.getByRole("button", { name: "Capturing..." })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Capturing..." }));
  expect(api.captureHistoricalSnapshot).toHaveBeenCalledOnce();

  await act(async () => {
    if (outcome === "success") {
      resolveCapture({
        snapshot_id: "snapshot-1", source_term_id: inactiveTerm.term_id,
        source_schedule_id: master.schedule_id, approved_by_user_id: "admin-1",
        academic_year: inactiveTerm.academic_year, term_number: inactiveTerm.term_number,
        captured_at: "2026-10-02T00:00:00Z", record_count: 8,
      });
    } else {
      rejectCapture(new ApiError(409, "HISTORY_ALREADY_CAPTURED", "History was captured in another session."));
    }
  });
  if (outcome === "success") {
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/8 teaching meetings captured/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Completed term" })).toBeEnabled();
  } else {
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("History was captured in another session.");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  }
});

it("requires explicit administrator confirmation before capture", async () => {
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  await userEvent.click(await screen.findByRole("button", { name: "Capture immutable history" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "Capture history" })).toBeDisabled();

  await userEvent.click(within(dialog).getByRole("checkbox"));
  await userEvent.click(within(dialog).getByRole("button", { name: "Capture history" }));

  expect(api.captureHistoricalSnapshot).toHaveBeenCalledWith("term-1", {
    schedule_id: "master-1",
    confirm_completed_and_approved: true,
  });
});

it("shows an existing immutable capture instead of offering a duplicate", async () => {
  vi.mocked(api.getHistoricalSnapshot).mockResolvedValue({
    snapshot_id: "snapshot-1",
    source_term_id: inactiveTerm.term_id,
    source_schedule_id: master.schedule_id,
    approved_by_user_id: "admin-1",
    academic_year: inactiveTerm.academic_year,
    term_number: inactiveTerm.term_number,
    captured_at: "2026-10-02T00:00:00Z",
    record_count: 8,
  });

  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  expect(await screen.findByText("Immutable history already captured")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Capture immutable history" })).not.toBeInTheDocument();
});

it("refreshes the selected term's history without applying the prior term's state", async () => {
  const otherMaster = { ...master, schedule_id: "master-2", term_id: otherTerm.term_id };
  vi.mocked(api.listSchedules).mockImplementation(async (termId) => ({
    items: termId === otherTerm.term_id ? [otherMaster] : [master],
  }));

  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  const selector = await screen.findByRole("combobox", { name: "Completed term" });
  await userEvent.selectOptions(selector, otherTerm.term_id);

  expect(await screen.findByText(/Finalized master schedule available/)).toBeInTheDocument();
  expect(api.listSchedules).toHaveBeenLastCalledWith(otherTerm.term_id, "master");
  expect(api.getHistoricalSnapshot).toHaveBeenLastCalledWith(otherTerm.term_id);
});

it("reports the captured meeting count returned by the server", async () => {
  vi.mocked(api.captureHistoricalSnapshot).mockResolvedValue({
    snapshot_id: "snapshot-1",
    source_term_id: inactiveTerm.term_id,
    source_schedule_id: master.schedule_id,
    approved_by_user_id: "admin-1",
    academic_year: inactiveTerm.academic_year,
    term_number: inactiveTerm.term_number,
    captured_at: "2026-10-02T00:00:00Z",
    record_count: 8,
  });
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("button", { name: "Capture immutable history" }));
  const dialog = screen.getByRole("dialog");
  await userEvent.click(within(dialog).getByRole("checkbox"));
  await userEvent.click(within(dialog).getByRole("button", { name: "Capture history" }));

  expect(await screen.findByText(/8 teaching meetings captured/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("surfaces a server refusal without inferring eligibility locally", async () => {
  vi.mocked(api.captureHistoricalSnapshot).mockRejectedValue(
    new ApiError(409, "SCHEDULE_NOT_CAPTUREABLE", "The master schedule is no longer complete."),
  );
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("button", { name: "Capture immutable history" }));
  const dialog = screen.getByRole("dialog");
  await userEvent.click(within(dialog).getByRole("checkbox"));
  await userEvent.click(within(dialog).getByRole("button", { name: "Capture history" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The master schedule is no longer complete.");
});

it("shows a retryable load error instead of the no-terms message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listTerms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history."));
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load completed-term history.");
  expect(screen.queryByText(/No inactive terms are available/)).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByLabelText("Completed term")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each(["schedules", "snapshot"])("shows the error, not a no-master claim, when the %s read fails", async (which) => {
  const failure = new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history.");
  // Selecting the first term re-runs the sync, so the failure has to outlast both reads.
  if (which === "schedules") vi.mocked(api.listSchedules).mockRejectedValue(failure);
  else vi.mocked(api.getHistoricalSnapshot).mockRejectedValue(failure);
  const user = userEvent.setup();
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load completed-term history.");
  expect(screen.queryByText(/no finalized master schedule/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Capture immutable history" })).not.toBeInTheDocument();

  vi.mocked(api.listSchedules).mockResolvedValue({ items: [master] });
  vi.mocked(api.getHistoricalSnapshot).mockRejectedValue(new ApiError(404, "HISTORY_NOT_CAPTURED", "This term has not been captured."));
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Capture immutable history" })).toBeInTheDocument();
});

it("keeps the blocked state without a loading box or live Retry while a retry is in flight", async () => {
  vi.mocked(api.listTerms).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history."));
  const user = userEvent.setup();
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await screen.findByRole("alert");

  vi.mocked(api.listTerms).mockReturnValueOnce(new Promise(() => {}));
  await user.click(screen.getByRole("button", { name: "Retry" }));

  expect(screen.getByRole("button", { name: /Retrying/ })).toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("Loading completed terms...")).not.toBeInTheDocument();
});

it("shows the error, not a no-master claim, when the next term's read fails after picking it", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await screen.findByLabelText("Completed term");

  vi.mocked(api.listSchedules).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history."));
  await user.selectOptions(screen.getByLabelText("Completed term"), "term-2");

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load completed-term history.");
  expect(screen.queryByText(/no finalized master schedule/)).not.toBeInTheDocument();
});

it("does not flip a shown error to Retrying or re-announce it on a failed background poll", async () => {
  vi.useFakeTimers();
  vi.mocked(api.listTerms).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history."));
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const alert = screen.getByRole("alert");

  vi.mocked(api.listTerms).mockReturnValue(new Promise(() => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

  expect(api.listTerms).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("alert")).toBe(alert);
  expect(screen.getByRole("button", { name: "Retry" })).toHaveAttribute("aria-disabled", "false");
});

it("shows the error, not the no-master claim, when a refresh fails over an empty term", async () => {
  vi.useFakeTimers();
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [] });
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText(/no finalized master schedule/)).toBeInTheDocument();

  vi.mocked(api.listTerms).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history."));
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

  expect(screen.getByRole("alert")).toHaveTextContent("Could not load completed-term history.");
  expect(screen.queryByText(/no finalized master schedule/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

it("shows loading, not a blank page, when a newly picked term's read hangs after a failure", async () => {
  const failure = new ApiError(500, "INTERNAL_ERROR", "Could not load completed-term history.");
  vi.mocked(api.listSchedules).mockRejectedValue(failure);
  const user = userEvent.setup();
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);
  await screen.findByRole("alert");

  vi.mocked(api.listSchedules).mockReturnValue(new Promise(() => {}));
  await user.selectOptions(screen.getByLabelText("Completed term"), "term-2");

  expect(await screen.findByText("Loading completed terms...")).toBeInTheDocument();
});

it("does not ask for a snapshot the server reports as not captured", async () => {
  vi.mocked(api.listTerms).mockResolvedValue({ items: [{ ...inactiveTerm, is_captured: false }] });
  render(<MemoryRouter><HistoryPage /></MemoryRouter>);

  expect(await screen.findByRole("button", { name: "Capture immutable history" })).toBeInTheDocument();
  expect(api.getHistoricalSnapshot).not.toHaveBeenCalled();
});
