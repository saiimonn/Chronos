import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import {
  ApiError,
  api,
  type BlockClass,
  type BlockReplacementPreviewResponse,
  type GAJobStatusResponse,
  type Schedule,
  type ScheduleValidation,
} from "../api/client";
import MasterSchedulePage from "./MasterSchedulePage";
import { BlockGenerationProvider } from "../state/BlockGeneration";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      getActiveTerm: vi.fn(),
      getMasterSchedule: vi.fn(),
      createMasterSchedule: vi.fn(),
      getReadiness: vi.fn(),
      submitGAJob: vi.fn(),
      getGAJob: vi.fn(),
      previewBlockReplacement: vi.fn(),
      confirmBlockReplacement: vi.fn(),
      validateSchedule: vi.fn(),
      finalizeSchedule: vi.fn(),
      deleteSchedule: vi.fn(),
      updateAssignment: vi.fn(),
      unlockBlock: vi.fn(),
      lockBlock: vi.fn(),
      clearBlock: vi.fn(),
      listFaculty: vi.fn(),
      listRooms: vi.fn(),
      listSubjects: vi.fn(),
      listBlocks: vi.fn(),
      listBlockClasses: vi.fn(),
      listTimeSlots: vi.fn(),
    },
  };
});

const auth = vi.hoisted(() => ({ role: "scheduling_committee" }));
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: auth.role }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const term = { term_id: "term-1", academic_year: "2026-2027", term_number: 1, is_active: true };

const faculty = [
  { faculty_id: "faculty-1", full_name: "A. Rivera", is_full_time: true, availability_mask: "0", availability_reviewed_term_id: null, is_active: true },
  { faculty_id: "faculty-2", full_name: "B. Cruz", is_full_time: false, availability_mask: "0", availability_reviewed_term_id: null, is_active: true },
];
const rooms = [
  { room_code: "LB 483", building_name: "Library", capacity: 40, room_type: "lecture" as const, expanded: false, is_active: true },
  { room_code: "LB 484", building_name: "Library", capacity: 20, room_type: "laboratory" as const, expanded: false, is_active: true },
];
const subjects = [
  { subject_code: "CS101", subject_title: "Intro to Computing", units: 3, subject_type: "LEC" as const, duration_minutes: 90 as const, is_active: true },
  { subject_code: "CS102", subject_title: "Data Structures", units: 3, subject_type: "LEC" as const, duration_minutes: 90 as const, is_active: true },
  { subject_code: "CS103", subject_title: "Discrete Mathematics", units: 3, subject_type: "LEC" as const, duration_minutes: 90 as const, is_active: true },
];
const blocks = [
  { block_id: "block-1", term_id: term.term_id, program_code: "BSCS", year_level: 1, section_letter: "A", is_expanded: false, name: "BSCS 1-A", capacity: 20 },
  { block_id: "block-2", term_id: term.term_id, program_code: "BSCS", year_level: 2, section_letter: "B", is_expanded: false, name: "BSCS 2-B", capacity: 20 },
  { block_id: "block-3", term_id: term.term_id, program_code: "BSCS", year_level: 3, section_letter: "C", is_expanded: false, name: "BSCS 3-C", capacity: 20 },
];
const timeSlots = [
  { time_slot_id: "slot-1", term_id: term.term_id, label: "LEC 08:00-09:30", start_time: "08:00:00", end_time: "09:30:00", ordinal: 0 },
  { time_slot_id: "slot-2", term_id: term.term_id, label: "LEC 10:00-11:30", start_time: "10:00:00", end_time: "11:30:00", ordinal: 1 },
];
const SUBJECT_OF: Record<string, string> = { "block-1": "CS101", "block-2": "CS102", "block-3": "CS103" };

const acceptedBlockOne = {
  record_id: "record-1",
  class_id: "class-block-1",
  offering_id: "offering-block-1",
  block_id: "block-1",
  faculty_id: "faculty-1",
  subject_code: "CS101",
  room_code: "LB 483",
  meeting_days: "M",
  time_slot_id: "slot-1",
  time_slot: "LEC 08:00-09:30",
};

const emptyMaster: Schedule = {
  schedule_id: "master-1",
  term_id: term.term_id,
  schedule_type: "master",
  created_at: "2026-09-01T00:00:00Z",
  finalized_at: null,
  assignments: [],
};
const masterWithBlockOne: Schedule = { ...emptyMaster, assignments: [acceptedBlockOne] };

const cleanValidation: ScheduleValidation = {
  required_class_count: 3,
  assigned_class_count: 0,
  completeness_percentage: 0,
  unassigned_class_ids: [],
  hard_conflict_count: 0,
  conflicts: [],
  export_allowed: false,
};
const completeValidation: ScheduleValidation = { ...cleanValidation, assigned_class_count: 3, completeness_percentage: 100, export_allowed: true };

function job(jobId: string, status: GAJobStatusResponse["status"] = "succeeded"): GAJobStatusResponse {
  return {
    job_id: jobId,
    kind: "ga",
    status,
    submitted_at: "2026-09-08T00:00:00Z",
    started_at: null,
    completed_at: null,
    error: status === "failed" ? { code: "GA_FAILED", message: "No feasible placement was found." } : null,
    result: status === "succeeded"
      ? {
          candidate: { term_id: term.term_id, schedule_id: null, assignments: [] },
          metrics: {
            random_seed: 1, termination_reason: "complete", termination_generation: 1, elapsed_seconds: 1,
            required_class_count: 1, assigned_class_count: 1, assignment_completeness: 1, hard_conflict_count: 0,
            historical_pattern_attainment: 0.5, selected_hard_conflict_count: 0, combined_hard_conflict_count: 0, cross_block_conflict_count: 0,
          },
        }
      : null,
  };
}

interface PreviewOptions {
  roomCode?: string;
  facultyId?: string;
  meetingDays?: string;
  slotId?: string;
  token?: string;
  conflicts?: ScheduleValidation["conflicts"];
  current?: boolean;
}

function previewFor(blockId: string, options: PreviewOptions = {}): BlockReplacementPreviewResponse {
  const classId = `class-${blockId}`;
  const proposed = {
    class_id: classId,
    offering_id: `offering-${blockId}`,
    block_id: blockId,
    subject_code: SUBJECT_OF[blockId],
    faculty_id: options.facultyId ?? "faculty-1",
    room_code: options.roomCode ?? "LB 483",
    meeting_days: options.meetingDays ?? "MW",
    time_slot_id: options.slotId ?? "slot-1",
    time_slot: "LEC 08:00-09:30",
  };
  const conflicts = options.conflicts ?? [];
  return {
    schedule_id: "master-1",
    block_id: blockId,
    preview_token: options.token ?? `token-${blockId}`,
    proposed_assignments: [proposed],
    changed_values: [{
      class_id: classId,
      subject_code: SUBJECT_OF[blockId],
      status: options.current ? "modified" : "added",
      changed_fields: ["room_code"],
      current_assignment: options.current ? { ...proposed, room_code: "LB 483", meeting_days: "M" } : null,
      proposed_assignment: proposed,
    }],
    validation: { ...cleanValidation, hard_conflict_count: conflicts.length, conflicts },
  };
}

function renderPage() {
  return render(<MemoryRouter><BlockGenerationProvider><MasterSchedulePage /></BlockGenerationProvider></MemoryRouter>);
}

/** Wires every job to succeed at once with one proposal per Block. */
function succeedEveryRun() {
  let next = 0;
  vi.mocked(api.submitGAJob).mockImplementation(async () => {
    next += 1;
    return { job_id: `job-${next}`, kind: "ga", status: "queued" };
  });
  vi.mocked(api.getGAJob).mockImplementation(async (jobId) => job(jobId));
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_scheduleId, blockId) => previewFor(blockId));
}

/** Ticks every shown Block and presses the one Generate button. */
async function startGenerate(count = 3, selectable = 3) {
  await userEvent.click(await screen.findByRole("checkbox", { name: `Select all (${selectable})` }));
  const button = screen.getByRole("button", { name: `Generate (${count})` });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
}

async function generateAll(count = 3, selectable = 3) {
  await startGenerate(count, selectable);
  await screen.findByText(/Generation finished/);
}

const unlockedMaster: Schedule = { ...masterWithBlockOne, unlocked_block_ids: ["block-1"] };

/** Unlocks BSCS 1-A (approved, so locked) through the server. */
async function unlockBlockOne() {
  vi.mocked(api.unlockBlock).mockResolvedValue({ schedule: unlockedMaster, validation: cleanValidation });
  await userEvent.click(await screen.findByRole("button", { name: "Unlock" }));
  vi.mocked(api.getMasterSchedule).mockResolvedValue(unlockedMaster);
  expect(api.unlockBlock).toHaveBeenCalledWith("master-1", "block-1");
  await screen.findByText(/Unlocked for editing\./);
}

function gridEntry(name: RegExp) {
  return screen.getAllByRole("button", { name })[0];
}

// An in-memory Storage, so recovery is tested the same on every Node version
// (newer Node releases ship their own global localStorage that shadows jsdom's).
function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() { return items.size; },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => { items.delete(key); },
    setItem: (key, value) => { items.set(key, String(value)); },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("localStorage", memoryStorage());
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.getActiveTerm).mockResolvedValue(term);
  vi.mocked(api.getMasterSchedule).mockResolvedValue(emptyMaster);
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: term.term_id, ready: true, blockers: [], warnings: [] });
  vi.mocked(api.getGAJob).mockReturnValue(new Promise(() => {}));
  vi.mocked(api.validateSchedule).mockResolvedValue(cleanValidation);
  vi.mocked(api.listFaculty).mockResolvedValue({ items: faculty });
  vi.mocked(api.listRooms).mockResolvedValue({ items: rooms });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: subjects });
  vi.mocked(api.listBlocks).mockResolvedValue({ items: blocks });
  vi.mocked(api.listBlockClasses).mockImplementation(async (blockId) => ({ items: [{ class_id: `class-${blockId}` } as BlockClass] }));
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: timeSlots });
});

// ---- Setup states ----------------------------------------------------------

it("creates the active term's master schedule", async () => {
  vi.mocked(api.getMasterSchedule).mockRejectedValue(new ApiError(404, "NOT_FOUND", "No master schedule exists for this term."));
  vi.mocked(api.createMasterSchedule).mockResolvedValue(masterWithBlockOne);
  renderPage();

  await userEvent.click(await screen.findByRole("button", { name: "Create master schedule" }));

  expect(api.createMasterSchedule).toHaveBeenCalledWith(term.term_id);
  expect(await screen.findByText("1 approved assignment.")).toBeInTheDocument();
});

it("directs the operator to term setup when no active term is selected", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(new ApiError(409, "NO_ACTIVE_TERM", "No active term is selected."));
  renderPage();

  expect(await screen.findByRole("heading", { name: "No active term selected" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Term Setup" })).toHaveAttribute("href", "/schedule");
});

it("lists outstanding term setup and keeps generation disabled", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: term.term_id,
    ready: false,
    blockers: [{ code: "NO_TIME_SLOTS", message: "The term has no time slots.", details: {} }],
    warnings: [],
  });
  renderPage();

  expect(await screen.findByRole("heading", { name: "Term setup is not complete yet" })).toBeInTheDocument();
  expect(screen.getByText("The term has no time slots.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "Select all (3)" }));
  expect(screen.getByRole("button", { name: "Generate (3)" })).toBeDisabled();
});

// ---- Generate --------------------------------------------------------------

it("generates the selected Blocks one after another with one button, passing earlier proposals as context", async () => {
  succeedEveryRun();
  renderPage();

  await generateAll();

  expect(vi.mocked(api.submitGAJob).mock.calls.map(([request]) => request)).toEqual([
    { term_id: "term-1", block_id: "block-1", base_schedule_id: "master-1" },
    { term_id: "term-1", block_id: "block-2", base_schedule_id: "master-1", context_job_ids: ["job-1"] },
    { term_id: "term-1", block_id: "block-3", base_schedule_id: "master-1", context_job_ids: ["job-1", "job-2"] },
  ]);
  // Sequential: each Block is submitted only after the one before has its proposal.
  const submits = vi.mocked(api.submitGAJob).mock.invocationCallOrder;
  const previews = vi.mocked(api.previewBlockReplacement).mock.invocationCallOrder;
  expect(previews[0]).toBeLessThan(submits[1]);
  expect(previews[1]).toBeLessThan(submits[2]);
  expect(api.previewBlockReplacement).toHaveBeenCalledWith("master-1", "block-1", { job_id: "job-1" });
  expect(screen.getByText("Generation finished: 3 proposals are ready to review.")).toBeInTheDocument();
  expect(api.confirmBlockReplacement).not.toHaveBeenCalled();
});

it("keeps generating the remaining Blocks while the user is on another page", async () => {
  succeedEveryRun();
  let releaseFirst: () => void = () => {};
  const firstHeld = new Promise<void>((resolve) => { releaseFirst = resolve; });
  vi.mocked(api.getGAJob).mockImplementation(async (jobId) => {
    if (jobId === "job-1") await firstHeld;
    return job(jobId);
  });
  const page = (shown: boolean) => (
    <MemoryRouter><BlockGenerationProvider>{shown ? <MasterSchedulePage /> : <p>Another page</p>}</BlockGenerationProvider></MemoryRouter>
  );
  const { rerender } = render(page(true));
  await startGenerate();
  await waitFor(() => expect(api.submitGAJob).toHaveBeenCalledTimes(1));

  // Leave the page mid-batch; the queue keeps going without it.
  rerender(page(false));
  await act(async () => { releaseFirst(); });
  await waitFor(() => expect(api.submitGAJob).toHaveBeenCalledTimes(3));

  rerender(page(true));
  expect(await screen.findByText("Generation finished: 3 proposals are ready to review.")).toBeInTheDocument();
});

it("keeps going after a failed Block, which can be selected and generated again", async () => {
  succeedEveryRun();
  vi.mocked(api.submitGAJob)
    .mockResolvedValueOnce({ job_id: "job-1", kind: "ga", status: "queued" })
    .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "The worker queue is unavailable."))
    .mockResolvedValueOnce({ job_id: "job-3", kind: "ga", status: "queued" });
  renderPage();

  await generateAll();

  expect(api.submitGAJob).toHaveBeenCalledTimes(3);
  expect(screen.getByText("Generation finished: 2 proposals are ready to review, 1 failed.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^BSCS 2-B/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("The worker queue is unavailable.");

  vi.mocked(api.submitGAJob).mockResolvedValueOnce({ job_id: "job-4", kind: "ga", status: "queued" });
  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 2-B" }));
  await userEvent.click(screen.getByRole("button", { name: "Generate (1)" }));
  await waitFor(() => expect(api.submitGAJob).toHaveBeenCalledTimes(4));
  expect(vi.mocked(api.submitGAJob).mock.calls[3][0]).toEqual({
    term_id: "term-1", block_id: "block-2", base_schedule_id: "master-1", context_job_ids: ["job-1", "job-3"],
  });
  expect(await screen.findByText("Generation finished: 1 proposal is ready to review.")).toBeInTheDocument();
});

it("reports a failed run's reason and leaves the Block unchanged", async () => {
  succeedEveryRun();
  vi.mocked(api.getGAJob).mockImplementation(async (jobId) => job(jobId, jobId === "job-1" ? "failed" : "succeeded"));
  renderPage();

  await generateAll();

  expect(screen.getByRole("alert")).toHaveTextContent("No feasible placement was found.");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});

it("stops the batch at a term-wide refusal instead of failing every Block", async () => {
  vi.mocked(api.submitGAJob).mockRejectedValue(new ApiError(409, "TERM_NOT_READY", "The term is not ready for schedule generation."));
  renderPage();

  await generateAll();

  expect(api.submitGAJob).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/0 proposals are ready to review, 1 failed, 2 not started \(The term is not ready/)).toBeInTheDocument();
});

it("stops after the current Block when asked, keeping its proposal", async () => {
  succeedEveryRun();
  let finish: (value: GAJobStatusResponse) => void = () => {};
  vi.mocked(api.getGAJob).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  renderPage();

  await startGenerate();
  expect(await screen.findByText(/Generating 1 of 3: BSCS 1-A/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop after this Block" }));
  await act(async () => { finish(job("job-1")); });

  expect(await screen.findByText("Generation finished: 1 proposal is ready to review, 2 not started (stopped).")).toBeInTheDocument();
  expect(api.submitGAJob).toHaveBeenCalledTimes(1);
});

it("never generates a locked Block, even when it is selected", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(masterWithBlockOne);
  succeedEveryRun();
  renderPage();

  await userEvent.click(await screen.findByRole("checkbox", { name: "Select all (3)" }));
  expect(screen.getByText(/3 selected · 2 to generate · 1 locked \(unlock to generate again\)/)).toBeInTheDocument();
  const button = screen.getByRole("button", { name: "Generate (2)" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
  await screen.findByText(/Generation finished/);

  expect(vi.mocked(api.submitGAJob).mock.calls.map(([request]) => request.block_id)).toEqual(["block-2", "block-3"]);
});

// ---- Review ----------------------------------------------------------------

it("shows one Block's proposed week at a time with time rows and day columns", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();

  const grid = screen.getByRole("group", { name: "Proposed week for BSCS 1-A" });
  expect(within(grid).getByRole("list", { name: "Monday" })).toBeInTheDocument();
  expect(within(grid).getByRole("list", { name: "Saturday" })).toBeInTheDocument();
  // MW: drawn on Monday and Wednesday, with every detail on the card.
  const entries = within(grid).getAllByRole("button", { name: /^Edit CS101 Intro to Computing/ });
  expect(entries).toHaveLength(2);
  expect(entries[0]).toHaveAccessibleName(/Monday 8:00 AM – 9:30 AM, room LB 483, A\. Rivera, proposed/);
  expect(within(grid).queryByText("CS102")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^BSCS 2-B/ }));
  expect(screen.getByRole("group", { name: "Proposed week for BSCS 2-B" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Proposed week for BSCS 1-A" })).not.toBeInTheDocument();
});

it("approves a Block after re-checking it against the latest master, then opens the next proposal", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockClear();
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => previewFor(blockId, { token: `fresh-${blockId}` }));
  vi.mocked(api.confirmBlockReplacement).mockResolvedValue({
    schedule: { ...emptyMaster, assignments: [{ ...acceptedBlockOne, class_id: "class-block-1" }] },
    validation: cleanValidation,
  });

  await userEvent.click(screen.getByRole("button", { name: "Approve and lock" }));

  expect(api.previewBlockReplacement).toHaveBeenCalledWith("master-1", "block-1", {
    assignments: [{ class_id: "class-block-1", faculty_id: "faculty-1", room_code: "LB 483", meeting_days: "MW", time_slot_id: "slot-1" }],
  });
  expect(api.confirmBlockReplacement).toHaveBeenCalledWith("master-1", "block-1", { preview_token: "fresh-block-1" });
  expect(await screen.findByText("BSCS 1-A was approved and added to the master schedule. Now reviewing BSCS 2-B.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "BSCS 2-B" })).toBeInTheDocument();
});

it("does not approve when the re-check finds a new conflict, and says why", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockResolvedValue(previewFor("block-1", {
    conflicts: [{
      code: "ROOM_OVERLAP", resource_key: ["LB 483"], class_ids: ["class-block-1", "class-other"], time_slot_ids: ["slot-1"],
      message: "Room LB 483 is booked twice on Monday 08:00-09:30.",
    }],
  }));

  await userEvent.click(screen.getByRole("button", { name: "Approve and lock" }));

  expect(await screen.findByText(/Checked against the latest master schedule, this proposal now has a conflict/)).toBeInTheDocument();
  expect(screen.getByText(/Room double-booked: Room LB 483 is booked twice/)).toBeInTheDocument();
  expect(api.confirmBlockReplacement).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Approve and lock" })).toBeDisabled();
});

it("approves every selected proposal at once, re-checking each in turn", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockClear();
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => previewFor(blockId, { token: `fresh-${blockId}` }));
  vi.mocked(api.confirmBlockReplacement).mockImplementation(async () => ({ schedule: emptyMaster, validation: cleanValidation }));

  expect(screen.queryByRole("button", { name: /^Approve and lock \(/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "Select all (3)" }));
  await userEvent.click(screen.getByRole("button", { name: "Approve and lock (3)" }));

  expect(await screen.findByText("3 Blocks were approved and added to the master schedule.")).toBeInTheDocument();
  expect(vi.mocked(api.confirmBlockReplacement).mock.calls).toEqual([
    ["master-1", "block-1", { preview_token: "fresh-block-1" }],
    ["master-1", "block-2", { preview_token: "fresh-block-2" }],
    ["master-1", "block-3", { preview_token: "fresh-block-3" }],
  ]);
  // Sequential: each proposal is re-checked only after the one before it is saved.
  const confirms = vi.mocked(api.confirmBlockReplacement).mock.invocationCallOrder;
  const previews = vi.mocked(api.previewBlockReplacement).mock.invocationCallOrder;
  expect(confirms[0]).toBeLessThan(previews[1]);
  expect(screen.getByText("Nothing selected")).toBeInTheDocument();
});

it("lists Blocks by year, then BSCS, BSIT, BSIS, then letter when no filter is set", async () => {
  const mixed = [
    { ...blocks[0], block_id: "is-1a", program_code: "BSIS", year_level: 1, section_letter: "A", name: "BSIS 1-A" },
    { ...blocks[0], block_id: "cs-2a", program_code: "BSCS", year_level: 2, section_letter: "A", name: "BSCS 2-A" },
    { ...blocks[0], block_id: "it-1a", program_code: "BSIT", year_level: 1, section_letter: "A", name: "BSIT 1-A" },
    { ...blocks[0], block_id: "cs-1b", program_code: "BSCS", year_level: 1, section_letter: "B", name: "BSCS 1-B" },
    { ...blocks[0], block_id: "cs-1a", program_code: "BSCS", year_level: 1, section_letter: "A", name: "BSCS 1-A" },
  ];
  vi.mocked(api.listBlocks).mockResolvedValue({ items: mixed });
  renderPage();

  const list = await screen.findByRole("list", { name: "Blocks" });
  const order = within(list).getAllByRole("listitem").map((item) => item.getAttribute("data-block-row"));
  expect(order).toEqual(["cs-1a", "cs-1b", "it-1a", "is-1a", "cs-2a"]);
  const programs = within(screen.getByRole("combobox", { name: "Program" })).getAllByRole("option").map((option) => option.textContent);
  expect(programs.filter((text) => text?.startsWith("BS"))).toEqual(["BSCS", "BSIT", "BSIS"]);
});

it("filters Blocks by year and only approves the proposals shown", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => previewFor(blockId, { token: `fresh-${blockId}` }));
  vi.mocked(api.confirmBlockReplacement).mockImplementation(async () => ({ schedule: emptyMaster, validation: cleanValidation }));

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Year" }), "2");
  expect(screen.getByText("Showing 1 of 3 Blocks")).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "Blocks" });
  expect(within(list).getByText("BSCS 2-B")).toBeInTheDocument();
  expect(within(list).queryByText("BSCS 1-A")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown (1)" }));
  await userEvent.click(screen.getByRole("button", { name: "Approve and lock (1)" }));

  await waitFor(() => expect(api.confirmBlockReplacement).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.confirmBlockReplacement).mock.calls[0][1]).toBe("block-2");
});

it("holds back a selected proposal that now conflicts, approving the rest", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => previewFor(blockId, blockId === "block-3"
    ? {
        conflicts: [{
          code: "ROOM_OVERLAP", resource_key: ["LB 483"], class_ids: ["class-block-3", "class-block-1"], time_slot_ids: ["slot-1"],
          message: "Room LB 483 is booked twice on Monday 08:00-09:30.",
        }],
      }
    : {}));
  vi.mocked(api.confirmBlockReplacement).mockResolvedValue({ schedule: masterWithBlockOne, validation: cleanValidation });

  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 1-A" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 3-C" }));
  await userEvent.click(screen.getByRole("button", { name: "Approve and lock (2)" }));

  expect(await screen.findByText(/1 Block was approved and added to the master schedule\. BSCS 3-C was not approved/)).toBeInTheDocument();
  expect(api.confirmBlockReplacement).toHaveBeenCalledTimes(1);
  expect(api.confirmBlockReplacement).toHaveBeenCalledWith("master-1", "block-1", { preview_token: "token-block-1" });
  // The held-back Block opens with the reason, and BSCS 2-B (never ticked) still waits.
  expect(screen.getByRole("heading", { name: "BSCS 3-C" })).toBeInTheDocument();
  expect(screen.getByText(/Checked against the latest master schedule, this proposal now has a conflict/)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 2-B" })).not.toBeChecked();
});

it("discards the selected proposals at once, then generates them again from the selection", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();

  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 1-A" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 2-B" }));
  await userEvent.click(screen.getByRole("button", { name: "Don't approve (2)" }));
  await userEvent.click(screen.getByRole("button", { name: "Discard 2" }));

  expect(screen.getByText(/2 proposals were not approved \(BSCS 1-A, BSCS 2-B\)\. Nothing in the master schedule changed\./)).toBeInTheDocument();
  expect(api.confirmBlockReplacement).not.toHaveBeenCalled();
  // BSCS 3-C was not ticked and still waits for review.
  expect(screen.getByRole("checkbox", { name: "Select BSCS 3-C" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Generate again" })).toBeInTheDocument();

  // Discard the last one too, then regenerate every discarded Block in one go.
  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 3-C" }));
  await userEvent.click(screen.getByRole("button", { name: "Don't approve (1)" }));
  await userEvent.click(screen.getByRole("button", { name: "Discard 1" }));
  vi.mocked(api.submitGAJob).mockClear();
  await generateAll();
  expect(vi.mocked(api.submitGAJob).mock.calls.map(([request]) => request.block_id)).toEqual(["block-1", "block-2", "block-3"]);
});

it("discards a proposal on Don't approve after confirming, leaving the master untouched", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();

  await userEvent.click(screen.getByRole("button", { name: "Don't approve" }));
  await userEvent.click(screen.getByRole("button", { name: "Discard it" }));

  expect(screen.getByText("The proposal for BSCS 1-A was not approved. Nothing in the master schedule changed.")).toBeInTheDocument();
  expect(screen.getAllByText("Not approved").length).toBeGreaterThan(0);
  expect(api.confirmBlockReplacement).not.toHaveBeenCalled();
});

it("compares a replacement proposal with the Block's currently approved week", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(masterWithBlockOne);
  succeedEveryRun();
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => previewFor(blockId, { current: blockId === "block-1" }));
  renderPage();

  // Approved means locked: it is generated again only once unlocked.
  expect(await screen.findByText(/Approved and locked\./)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate again" })).not.toBeInTheDocument();
  await unlockBlockOne();
  await userEvent.click(await screen.findByRole("button", { name: "Generate again" }));
  await screen.findByText(/Generation finished/);
  expect(screen.getByRole("button", { name: "Approve, replace and lock" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("radio", { name: "Compare with approved" }));
  const grid = screen.getByRole("group", { name: "Proposed and approved week for BSCS 1-A" });
  expect(within(grid).getByLabelText(/CS101 Intro to Computing, Monday 8:00 AM – 9:30 AM, room LB 483, A\. Rivera, currently approved/)).toBeInTheDocument();
  expect(within(grid).getAllByRole("button", { name: /proposed, Changed/ })).toHaveLength(2);

  await userEvent.click(screen.getByRole("radio", { name: "Currently approved" }));
  expect(screen.getByRole("group", { name: "Approved week for BSCS 1-A" })).toBeInTheDocument();
});

it("applies a conflict-free edit to the proposal after the server checks it", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockResolvedValue(previewFor("block-1", { roomCode: "LB 484", facultyId: "faculty-2", slotId: "slot-2" }));

  await userEvent.click(gridEntry(/^Edit CS101/));
  const dialog = within(screen.getByRole("dialog"));
  await userEvent.click(dialog.getByRole("combobox", { name: "Instructor" }));
  await userEvent.click(dialog.getByRole("option", { name: /B\. Cruz/ }));
  await userEvent.click(dialog.getByRole("combobox", { name: "Room" }));
  await userEvent.click(dialog.getByRole("option", { name: /LB 484/ }));
  await userEvent.click(dialog.getByRole("combobox", { name: "Time window" }));
  await userEvent.click(dialog.getByRole("option", { name: /LEC 10:00-11:30/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Apply to proposal" }));

  expect(api.previewBlockReplacement).toHaveBeenLastCalledWith("master-1", "block-1", {
    assignments: [{ class_id: "class-block-1", faculty_id: "faculty-2", room_code: "LB 484", meeting_days: "MW", time_slot_id: "slot-2" }],
    strict_class_ids: ["class-block-1"],
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(gridEntry(/^Edit CS101/)).toHaveAccessibleName(/10:00 AM – 11:30 AM, room LB 484, B\. Cruz, proposed, Edited/);
  expect(api.confirmBlockReplacement).not.toHaveBeenCalled();
});

it("refuses a conflicting edit with the validator's reason and keeps the proposal unchanged", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.previewBlockReplacement).mockRejectedValue(new ApiError(
    409,
    "PROPOSAL_EDIT_CONFLICT",
    "The change was not applied. Faculty B. Cruz is already teaching BSCS 2-B on Monday 08:00-09:30.",
    { conflicts: [{ code: "FACULTY_OVERLAP", message: "Faculty B. Cruz is already teaching BSCS 2-B on Monday 08:00-09:30." }] },
  ));

  await userEvent.click(gridEntry(/^Edit CS101/));
  const dialog = within(screen.getByRole("dialog"));
  await userEvent.click(dialog.getByRole("combobox", { name: "Instructor" }));
  await userEvent.click(dialog.getByRole("option", { name: /B\. Cruz/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Apply to proposal" }));

  const alert = await dialog.findByRole("alert");
  expect(alert).toHaveTextContent("The change was not applied.");
  expect(alert).toHaveTextContent("Instructor double-booked: Faculty B. Cruz is already teaching BSCS 2-B");
  // The dialog keeps the entered values; the proposal still shows the original.
  expect((dialog.getByRole("combobox", { name: "Instructor" }) as HTMLInputElement).value).toContain("B. Cruz");
  await userEvent.click(dialog.getByRole("button", { name: "Cancel" }));
  expect(gridEntry(/^Edit CS101/)).toHaveAccessibleName(/A\. Rivera, proposed$/);
});

it("lists only free instructors and rooms, and says why the others are hidden", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue({
    ...emptyMaster,
    assignments: [{ ...acceptedBlockOne, record_id: "record-3", class_id: "class-block-3", offering_id: "offering-block-3", block_id: "block-3", subject_code: "CS103", faculty_id: "faculty-2", room_code: "LB 484" }],
  });
  succeedEveryRun();
  renderPage();
  await generateAll(2);

  await userEvent.click(gridEntry(/^Edit CS101/));
  const dialog = within(screen.getByRole("dialog"));
  await userEvent.click(dialog.getByRole("combobox", { name: "Instructor" }));
  expect(dialog.queryByRole("option", { name: /B\. Cruz/ })).not.toBeInTheDocument();
  expect(dialog.getByRole("option", { name: /A\. Rivera.*0 of 24 units taken/ })).toBeInTheDocument();
  expect(dialog.getByText(/\(\d+ hidden\)/)).toBeInTheDocument();

  await userEvent.click(dialog.getByRole("checkbox", { name: "Show all options" }));
  await userEvent.click(dialog.getByRole("combobox", { name: "Instructor" }));
  expect(dialog.getByRole("option", { name: /B\. Cruz.*3 of 17 units taken.*busy then \(CS103 BSCS 3-C\)/ })).toBeInTheDocument();
  await userEvent.click(dialog.getByRole("combobox", { name: "Room" }));
  expect(dialog.getByRole("option", { name: /LB 484.*in use then \(CS103 BSCS 3-C\)/ })).toBeInTheDocument();
});

it("lets an unplaced class be placed by hand", async () => {
  succeedEveryRun();
  const unplaced = previewFor("block-1");
  unplaced.proposed_assignments = [];
  unplaced.changed_values[0].proposed_assignment = null;
  vi.mocked(api.previewBlockReplacement).mockImplementation(async (_s, blockId) => (blockId === "block-1" ? unplaced : previewFor(blockId)));
  renderPage();
  await generateAll();

  expect(screen.getByRole("button", { name: "Approve and lock" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Place it by hand" }));
  const dialog = within(screen.getByRole("dialog", { name: "Place class in proposal" }));
  expect(dialog.getByRole("button", { name: "Apply to proposal" })).toBeDisabled();
});

// ---- Approved week, conflicts, recovery, finalize --------------------------

it("keeps an approved week read-only until it is unlocked, then edits and locks it again", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(masterWithBlockOne);
  vi.mocked(api.updateAssignment).mockResolvedValue({ schedule: unlockedMaster, validation: cleanValidation });
  renderPage();

  expect(await screen.findByText(/Approved and locked\./)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Edit CS101/ })).not.toBeInTheDocument();
  await unlockBlockOne();

  await userEvent.click(await screen.findByRole("button", { name: /^Edit CS101 Intro to Computing, Monday .* approved$/ }));
  const dialog = within(screen.getByRole("dialog", { name: "Edit class in unlocked Block" }));
  await userEvent.click(dialog.getByRole("checkbox", { name: "Wednesday" }));
  await userEvent.click(dialog.getByRole("button", { name: "Save assignment" }));

  expect(api.updateAssignment).toHaveBeenCalledWith("master-1", "record-1", {
    faculty_id: "faculty-1", room_code: "LB 483", meeting_days: "MW", time_slot_id: "slot-1",
  });
  expect(await screen.findByText(/Assignment saved/)).toBeInTheDocument();

  vi.mocked(api.lockBlock).mockResolvedValue({ schedule: masterWithBlockOne, validation: cleanValidation });
  await userEvent.click(screen.getByRole("button", { name: "Lock" }));
  expect(api.lockBlock).toHaveBeenCalledWith("master-1", "block-1");
  expect(await screen.findByText(/BSCS 1-A is locked/)).toBeInTheDocument();
});

it("lists approved conflicts and jumps to the class's Block", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue({
    ...emptyMaster,
    assignments: [acceptedBlockOne, { ...acceptedBlockOne, record_id: "record-2", class_id: "class-block-2", block_id: "block-2", subject_code: "CS102" }],
  });
  vi.mocked(api.validateSchedule).mockResolvedValue({
    ...cleanValidation,
    hard_conflict_count: 1,
    conflicts: [{ code: "FACULTY_OVERLAP", resource_key: ["faculty-1"], class_ids: ["class-block-1", "class-block-2"], time_slot_ids: ["slot-1"], message: "A. Rivera teaches two classes at once." }],
  });
  renderPage();

  expect(await screen.findByText("A. Rivera teaches two classes at once.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Go to CS102 — BSCS 2-B" }));
  expect(screen.getByRole("heading", { name: "BSCS 2-B" })).toBeInTheDocument();
  expect(screen.getAllByLabelText(/CS102 .*conflict: Instructor double-booked/).length).toBeGreaterThan(0);
});

it("restores proposals waiting for review after a reload", async () => {
  localStorage.setItem("chronos.pendingBlockBatch.v1:operator", JSON.stringify({
    termId: "term-1",
    scheduleId: "master-1",
    runs: [{ blockId: "block-2", jobId: "job-9" }],
  }));
  vi.mocked(api.getGAJob).mockResolvedValue(job("job-9"));
  vi.mocked(api.previewBlockReplacement).mockResolvedValue(previewFor("block-2"));
  renderPage();

  expect(await screen.findByText("Restored 1 proposal from your last visit.")).toBeInTheDocument();
  expect(api.previewBlockReplacement).toHaveBeenCalledWith("master-1", "block-2", { job_id: "job-9" });
  expect(await screen.findByRole("group", { name: "Proposed week for BSCS 2-B" })).toBeInTheDocument();
});

it("ignores stored proposals for another master schedule", async () => {
  localStorage.setItem("chronos.pendingBlockBatch.v1:operator", JSON.stringify({ termId: "term-1", scheduleId: "other", runs: [{ blockId: "block-2", jobId: "job-9" }] }));
  renderPage();

  await screen.findByRole("button", { name: "Generate (0)" });
  expect(api.getGAJob).not.toHaveBeenCalled();
});

it("finalizes through the server once the schedule is complete and nothing waits for review", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(masterWithBlockOne);
  vi.mocked(api.validateSchedule).mockResolvedValue(completeValidation);
  vi.mocked(api.finalizeSchedule).mockResolvedValue({
    schedule: { ...masterWithBlockOne, finalized_at: "2026-09-09T00:00:00Z" },
    validation: completeValidation,
  });
  renderPage();

  const finalize = await screen.findByRole("button", { name: "Finalize master schedule" });
  await waitFor(() => expect(finalize).toBeEnabled());
  await userEvent.click(finalize);
  await userEvent.click(screen.getByRole("button", { name: "Confirm finalization" }));

  expect(api.finalizeSchedule).toHaveBeenCalledWith("master-1");
  expect(await screen.findByText(/This schedule is read-only/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Generate/ })).not.toBeInTheDocument();
});

it("lets only a Department Administrator delete a finalized master schedule", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue({ ...masterWithBlockOne, finalized_at: "2026-09-09T00:00:00Z" });
  vi.mocked(api.validateSchedule).mockResolvedValue(completeValidation);
  const { unmount } = renderPage();
  await screen.findByText(/This schedule is read-only/);
  expect(screen.queryByRole("button", { name: "Delete master schedule" })).not.toBeInTheDocument();
  unmount();

  auth.role = "department_administrator";
  try {
    vi.mocked(api.deleteSchedule).mockResolvedValue(undefined);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Delete master schedule" }));
    vi.mocked(api.getMasterSchedule).mockRejectedValue(new ApiError(404, "NOT_FOUND", "No master schedule exists for this term."));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete master schedule" }));
    expect(api.deleteSchedule).toHaveBeenCalledWith("master-1");
    expect(await screen.findByText(/finalized master schedule was deleted/)).toBeInTheDocument();
    expect(screen.queryByText(/This schedule is read-only/)).not.toBeInTheDocument();
  } finally {
    auth.role = "scheduling_committee";
  }
});

it("holds finalization while proposals wait for review", async () => {
  vi.mocked(api.validateSchedule).mockResolvedValue(completeValidation);
  succeedEveryRun();
  renderPage();
  await generateAll();

  expect(screen.getByRole("button", { name: "Finalize master schedule" })).toBeDisabled();
  expect(screen.getByText(/Finalize is unavailable while Blocks are generating or proposals wait for review/)).toBeInTheDocument();
});

it("holds finalization while a Block is unlocked", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(unlockedMaster);
  vi.mocked(api.validateSchedule).mockResolvedValue(completeValidation);
  renderPage();

  expect(await screen.findByText(/Finalize is unavailable while a Block is unlocked\. Lock BSCS 1-A first\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Finalize master schedule" })).toBeDisabled();
});

it("shows a deactivated instructor's classes as conflict errors and their units as needing attention", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(masterWithBlockOne);
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...faculty[0], is_active: false }, faculty[1]] });
  vi.mocked(api.validateSchedule).mockResolvedValue({
    ...cleanValidation,
    assigned_class_count: 1,
    hard_conflict_count: 1,
    conflicts: [{
      code: "FACULTY_INACTIVE", resource_key: ["faculty-1"], class_ids: ["class-block-1"], time_slot_ids: ["slot-1"],
      message: "A. Rivera teaches CS101 (BSCS 1-A) but is marked as not currently teaching. Assign another instructor.",
    }],
  });
  renderPage();

  expect(await screen.findByText(/A\. Rivera teaches CS101 \(BSCS 1-A\) but is marked as not currently teaching/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Conflict errors in the approved schedule" })).toBeInTheDocument();
  expect(screen.getByText(/It has 1 conflict error now: unlock it to fix it\./)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Show 2 instructors" }));
  const row = screen.getByRole("row", { name: /A\. Rivera/ });
  expect(within(row).getByText("No longer teaching: reassign their classes")).toBeInTheDocument();
  expect(within(row).getByText("3")).toBeInTheDocument();
});

it("re-checks proposals when the roster changes and blocks one that uses a deactivated instructor", async () => {
  succeedEveryRun();
  renderPage();
  await generateAll();
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...faculty[0], is_active: false }, faculty[1]] });
  vi.mocked(api.previewBlockReplacement).mockRejectedValue(new ApiError(
    409, "FACULTY_INACTIVE", "This faculty member is marked as not currently teaching.", { faculty_id: "faculty-1" },
  ));

  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

  expect(await screen.findByText(/A\. Rivera was marked as not currently teaching\. Generate the Block again, or reassign their classes\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve and lock" })).toBeDisabled();
});

it("selects several Blocks by holding a row and dragging over the others", async () => {
  renderPage();
  const list = await screen.findByRole("list", { name: "Blocks" });
  const rows = await waitFor(() => {
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    return items;
  });
  // jsdom has no layout: say which row is under the pointer.
  let under: Element = rows[0];
  const original = document.elementFromPoint;
  document.elementFromPoint = () => under;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    act(() => { fireEvent.pointerDown(rows[0], { button: 0, clientX: 10, clientY: 10 }); });
    act(() => { vi.advanceTimersByTime(400); });
    // A fast drag skips straight to the last row; the one in between is still taken.
    under = within(rows[2]).getByText("BSCS 3-C");
    act(() => { fireEvent.pointerMove(window, { clientX: 10, clientY: 120 }); });
    for (const name of ["BSCS 1-A", "BSCS 2-B", "BSCS 3-C"]) {
      expect(screen.getByRole("checkbox", { name: `Select ${name}` })).toBeChecked();
    }
    // Dragging back gives up the rows past the pointer.
    under = rows[1];
    act(() => { fireEvent.pointerMove(window, { clientX: 10, clientY: 60 }); });
    act(() => { fireEvent.pointerUp(window); });
  } finally {
    vi.useRealTimers();
    document.elementFromPoint = original;
  }

  expect(screen.getByRole("checkbox", { name: "Select BSCS 1-A" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 2-B" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 3-C" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Generate (2)" })).toBeInTheDocument();
});

it("undoes a drag when the pointer comes back to the row it started on", async () => {
  renderPage();
  const list = await screen.findByRole("list", { name: "Blocks" });
  const rows = await waitFor(() => {
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    return items;
  });
  // BSCS 3-C was already ticked before the drag; the undo must keep it.
  await userEvent.click(screen.getByRole("checkbox", { name: "Select BSCS 3-C" }));
  let under: Element = rows[0];
  const original = document.elementFromPoint;
  document.elementFromPoint = () => under;
  try {
    act(() => { fireEvent.pointerDown(rows[0], { button: 0, pointerType: "mouse", clientX: 10, clientY: 10 }); });
    under = rows[1];
    act(() => { fireEvent.pointerMove(window, { pointerType: "mouse", clientX: 10, clientY: 60 }); });
    expect(screen.getByRole("checkbox", { name: "Select BSCS 1-A" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select BSCS 2-B" })).toBeChecked();
    under = rows[0];
    act(() => { fireEvent.pointerMove(window, { pointerType: "mouse", clientX: 10, clientY: 12 }); });
    act(() => { fireEvent.pointerUp(window); });
  } finally {
    document.elementFromPoint = original;
  }
  expect(screen.getByRole("checkbox", { name: "Select BSCS 1-A" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 2-B" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 3-C" })).toBeChecked();
});

it("starts a mouse drag without a hold once the pointer reaches another row", async () => {
  renderPage();
  const list = await screen.findByRole("list", { name: "Blocks" });
  const rows = await waitFor(() => {
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    return items;
  });
  let under: Element = rows[0];
  const original = document.elementFromPoint;
  document.elementFromPoint = () => under;
  try {
    act(() => { fireEvent.pointerDown(rows[0], { button: 0, pointerType: "mouse", clientX: 10, clientY: 10 }); });
    // Wobbling on the pressed row is still just a click.
    act(() => { fireEvent.pointerMove(window, { pointerType: "mouse", clientX: 14, clientY: 22 }); });
    expect(screen.getByRole("checkbox", { name: "Select BSCS 1-A" })).not.toBeChecked();
    under = rows[1];
    act(() => { fireEvent.pointerMove(window, { pointerType: "mouse", clientX: 10, clientY: 60 }); });
    act(() => { fireEvent.pointerUp(window); });
  } finally {
    document.elementFromPoint = original;
  }
  expect(screen.getByRole("checkbox", { name: "Select BSCS 1-A" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 2-B" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select BSCS 3-C" })).not.toBeChecked();
});

it("treats a touch that moves before the hold registers as a scroll, not a drag", async () => {
  renderPage();
  const list = await screen.findByRole("list", { name: "Blocks" });
  const rows = await waitFor(() => {
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    return items;
  });
  const original = document.elementFromPoint;
  document.elementFromPoint = () => rows[2];
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    act(() => { fireEvent.pointerDown(rows[0], { button: 0, pointerType: "touch", clientX: 10, clientY: 10 }); });
    act(() => { fireEvent.pointerMove(window, { pointerType: "touch", clientX: 10, clientY: 80 }); });
    act(() => { vi.advanceTimersByTime(400); });
    act(() => { fireEvent.pointerMove(window, { clientX: 10, clientY: 120 }); });
    act(() => { fireEvent.pointerUp(window); });
  } finally {
    vi.useRealTimers();
    document.elementFromPoint = original;
  }
  for (const name of ["BSCS 1-A", "BSCS 2-B", "BSCS 3-C"]) {
    expect(screen.getByRole("checkbox", { name: `Select ${name}` })).not.toBeChecked();
  }
});

// ---- Paired Blocks (A-B share lectures and lab times) ------------------------

const pairedBlocks = [
  blocks[0],
  { block_id: "block-4", term_id: term.term_id, program_code: "BSCS", year_level: 1, section_letter: "B", is_expanded: false, name: "BSCS 1-B", capacity: 20 },
];

it("offers to discard both halves of a pair, so the pair is generated afresh together", async () => {
  SUBJECT_OF["block-4"] = "CS101";
  vi.mocked(api.listBlocks).mockResolvedValue({ items: pairedBlocks });
  succeedEveryRun();
  renderPage();
  await generateAll(2, 2);

  await userEvent.click(screen.getByRole("button", { name: "Don't approve" }));
  expect(screen.getByText(/BSCS 1-B's proposal shares its lectures and lab times with this one/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Discard both" }));
  expect(screen.getByText(/The proposals for BSCS 1-A and BSCS 1-B were not approved\./)).toBeInTheDocument();

  vi.mocked(api.submitGAJob).mockClear();
  await generateAll(2, 2);
  expect(vi.mocked(api.submitGAJob).mock.calls.map(([request]) => request.block_id)).toEqual(["block-1", "block-4"]);
  // A is generated freely (B's discarded proposal no longer holds it); B around the new A.
  expect(vi.mocked(api.submitGAJob).mock.calls[0][0]).not.toHaveProperty("context_job_ids");
});

it("regenerates a pair when only one half was discarded, dropping the other half's proposal too", async () => {
  SUBJECT_OF["block-4"] = "CS101";
  vi.mocked(api.listBlocks).mockResolvedValue({ items: pairedBlocks });
  succeedEveryRun();
  renderPage();
  await generateAll(2, 2);

  await userEvent.click(screen.getByRole("button", { name: "Don't approve" }));
  await userEvent.click(screen.getByRole("button", { name: "Discard only BSCS 1-A" }));
  expect(screen.getByText(/BSCS 1-B's proposal is still in review, so generating BSCS 1-A alone fits its lectures and labs around that proposal\./)).toBeInTheDocument();

  vi.mocked(api.submitGAJob).mockClear();
  await userEvent.click(screen.getByRole("button", { name: "Regenerate pair (A+B)" }));
  await userEvent.click(screen.getByRole("button", { name: "Regenerate pair" }));
  await screen.findByText(/Generation finished: 2 proposals/);
  expect(vi.mocked(api.submitGAJob).mock.calls.map(([request]) => request.block_id)).toEqual(["block-1", "block-4"]);
  expect(vi.mocked(api.submitGAJob).mock.calls[0][0]).not.toHaveProperty("context_job_ids");
  expect(api.clearBlock).not.toHaveBeenCalled();
});

/** BSCS 1-A in review while its partner BSCS 1-B is approved and unlocked, holding their shared lecture. */
async function editProposalBesideApprovedPartner(refuse = false) {
  SUBJECT_OF["block-4"] = "CS101";
  vi.mocked(api.listBlocks).mockResolvedValue({ items: pairedBlocks });
  const partnerMaster: Schedule = {
    ...emptyMaster,
    unlocked_block_ids: ["block-4"],
    assignments: [{ ...acceptedBlockOne, record_id: "record-4", class_id: "class-block-4", block_id: "block-4", meeting_days: "MW" }],
  };
  vi.mocked(api.getMasterSchedule).mockResolvedValue(partnerMaster);
  vi.mocked(api.updateAssignment).mockResolvedValue({ schedule: partnerMaster, validation: cleanValidation });
  succeedEveryRun();
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "Select BSCS 1-A" }));
  await userEvent.click(screen.getByRole("button", { name: "Generate (1)" }));
  await screen.findByText(/Generation finished/);
  if (refuse) {
    vi.mocked(api.previewBlockReplacement).mockRejectedValue(new ApiError(409, "PROPOSAL_EDIT_CONFLICT", "The change was not applied. Room LB 483 is booked twice."));
  }

  await userEvent.click(gridEntry(/^Edit CS101/));
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByRole("note")).toHaveTextContent("BSCS 1-B (approved) shares this lecture");
  await userEvent.click(dialog.getByRole("combobox", { name: "Time window" }));
  await userEvent.click(dialog.getByRole("option", { name: /LEC 10:00-11:30/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Apply to proposal" }));
  return dialog;
}

it("moves an approved partner's shared lecture with a proposal edit, so one edit moves the pair", async () => {
  await editProposalBesideApprovedPartner();

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.updateAssignment).toHaveBeenCalledWith("master-1", "record-4", {
    faculty_id: "faculty-1", room_code: "LB 483", meeting_days: "MW", time_slot_id: "slot-2",
  });
  expect(api.previewBlockReplacement).toHaveBeenCalledWith("master-1", "block-1", expect.objectContaining({ strict_class_ids: ["class-block-1"] }));
  expect(screen.getByText(/BSCS 1-B's approved session moved with it/)).toBeInTheDocument();
});

it("puts the approved partner back when the proposal edit is refused", async () => {
  const dialog = await editProposalBesideApprovedPartner(true);

  expect(await dialog.findByRole("alert")).toHaveTextContent("Room LB 483 is booked twice.");
  expect(api.updateAssignment).toHaveBeenCalledTimes(2);
  expect(api.updateAssignment).toHaveBeenLastCalledWith("master-1", "record-4", {
    faculty_id: "faculty-1", room_code: "LB 483", meeting_days: "MW", time_slot_id: "slot-1",
  });
});

it("explains that a discarded half follows its locked partner, and regenerating the pair needs both unlocked", async () => {
  SUBJECT_OF["block-4"] = "CS101";
  vi.mocked(api.listBlocks).mockResolvedValue({ items: pairedBlocks });
  vi.mocked(api.getMasterSchedule).mockResolvedValue({
    ...emptyMaster,
    assignments: [{ ...acceptedBlockOne, record_id: "record-4", class_id: "class-block-4", block_id: "block-4" }],
  });
  succeedEveryRun();
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "Select BSCS 1-A" }));
  await userEvent.click(screen.getByRole("button", { name: "Generate (1)" }));
  await screen.findByText(/Generation finished/);

  await userEvent.click(screen.getByRole("button", { name: "Don't approve" }));
  await userEvent.click(screen.getByRole("button", { name: "Discard it" }));

  expect(screen.getByText(/BSCS 1-B is approved and locked, so generating BSCS 1-A alone keeps the lecture sessions and lab times BSCS 1-B already has\./)).toBeInTheDocument();
  expect(screen.getByText(/\(unlock BSCS 1-B first\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Regenerate pair (A+B)" })).toBeDisabled();
});
