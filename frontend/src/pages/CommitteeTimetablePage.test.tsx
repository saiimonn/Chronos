import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type Schedule, type TimetableProjection } from "../api/client";
import { publishTermChanged } from "../state/appEvents";
import CommitteeTimetablePage, { MIN_CARD_WIDTH_PX } from "./CommitteeTimetablePage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      getActiveTerm: vi.fn(),
      listSchedules: vi.fn(),
      listBlocks: vi.fn(),
      listRooms: vi.fn(),
      listFaculty: vi.fn(),
      getTimetableProjection: vi.fn(),
      validateSchedule: vi.fn(),
      exportSchedule: vi.fn(),
      deleteSchedule: vi.fn(),
    },
  };
});
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const mockTerm = {
  term_id: "term-1",
  academic_year: "2026-2027",
  term_number: 1,
  is_active: true,
};

const mockBlocks = [
  {
    block_id: "block-1",
    term_id: "term-1",
    program_code: "BSCS",
    year_level: 1,
    section_letter: "A",
    is_expanded: false,
    name: "BSCS-1A",
    capacity: 20,
  },
  {
    block_id: "block-2",
    term_id: "term-1",
    program_code: "BSCS",
    year_level: 2,
    section_letter: "A",
    is_expanded: false,
    name: "BSCS-2A",
    capacity: 20,
  },
];

const mockRooms = [
  {
    room_code: "LB441",
    building_name: "Bunzel",
    capacity: 40,
    room_type: "lecture" as const,
    expanded: false,
  },
  {
    room_code: "LB442",
    building_name: "Bunzel",
    capacity: 20,
    room_type: "laboratory" as const,
    expanded: false,
  },
];

const mockFaculty = [
  {
    faculty_id: "fac-1",
    full_name: "Ada Lovelace",
    is_full_time: true,
    availability_mask: "1",
    availability_reviewed_term_id: null,
    is_active: true,
  },
  {
    faculty_id: "fac-2",
    full_name: "Alan Turing",
    is_full_time: false,
    availability_mask: "1",
    availability_reviewed_term_id: null,
    is_active: true,
  },
];

const mockSchedules: Schedule[] = [
  {
    schedule_id: "sched-candidate-2",
    term_id: "term-1",
    schedule_type: "routine",
    created_at: "2026-09-08T10:00:00Z",
    finalized_at: null,
    assignments: [],
  },
  {
    schedule_id: "sched-candidate-1",
    term_id: "term-1",
    schedule_type: "routine",
    created_at: "2026-09-07T08:00:00Z",
    finalized_at: null,
    assignments: [],
  },
  {
    schedule_id: "sched-master-1",
    term_id: "term-1",
    schedule_type: "master",
    created_at: "2026-09-06T12:00:00Z",
    finalized_at: "2026-09-07T12:00:00Z",
    assignments: [],
  },
];

const mockProjection: TimetableProjection = {
  schedule_id: "sched-candidate-2",
  term_id: "term-1",
  schedule_type: "routine",
  created_at: "2026-09-08T10:00:00Z",
  finalized_at: null,
  finalization_state: "candidate",
  calendar: {
    teaching_days: ["MON", "TUE", "WED", "THU", "FRI", "SAT"],
    opening_time: "07:30:00",
    closing_time: "21:00:00",
    interval_minutes: 30,
  },
  assignments: [
    {
      record_id: "rec-1",
      offering_id: "off-1",
      subject_code: "CS101",
      subject_title: "Intro to Computing",
      group_label: "CS101 G1",
      block_id: "block-1",
      block_name: "BSCS-1A",
      program_code: "BSCS",
      year_level: 1,
      faculty_id: "fac-1",
      faculty_name: "Dr. Alan Turing",
      room_code: "LB441",
      room_type: "lecture",
      room_capacity: 40,
      meeting_days: "MWF",
      time_slot_id: "slot-1",
      time_slot_label: "LEC 07:30-09:00",
      start_time: "07:30:00",
      end_time: "09:00:00",
    },
    {
      record_id: "rec-2",
      offering_id: "off-2",
      subject_code: "CS102",
      subject_title: "Data Structures",
      group_label: "CS102 G1",
      block_id: "block-2",
      block_name: "BSCS-2A",
      program_code: "BSCS",
      year_level: 2,
      faculty_id: "fac-2",
      faculty_name: "Prof. Ada Lovelace",
      room_code: "LB442",
      room_type: "laboratory",
      room_capacity: 20,
      meeting_days: "TTh",
      time_slot_id: "slot-2",
      time_slot_label: "LAB 09:00-11:30",
      start_time: "09:00:00",
      end_time: "11:30:00",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getActiveTerm).mockResolvedValue(mockTerm);
  vi.mocked(api.listSchedules).mockResolvedValue({ items: mockSchedules });
  vi.mocked(api.listBlocks).mockResolvedValue({ items: mockBlocks });
  vi.mocked(api.listRooms).mockResolvedValue({ items: mockRooms });
  vi.mocked(api.listFaculty).mockResolvedValue({ items: mockFaculty });
  vi.mocked(api.getTimetableProjection).mockResolvedValue(mockProjection);
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 2,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: true,
  });
});

it("renders loading state initially", async () => {
  let finishLoad: (term: typeof mockTerm) => void = () => {};
  vi.mocked(api.getActiveTerm).mockReturnValue(
    new Promise((resolve) => {
      finishLoad = resolve;
    }),
  );

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(screen.getByText("Loading committee timetable...")).toBeInTheDocument();

  await act(async () => {
    finishLoad(mockTerm);
  });
});

it("shows no active term state when NO_ACTIVE_TERM is returned", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(404, "NO_ACTIVE_TERM", "No active term."),
  );

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("heading", { name: "No active term selected" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Term Setup" })).toHaveAttribute("href", "/schedule");
});

it("shows no schedules state when active term has no schedules", async () => {
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [] });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(
    await screen.findByRole("heading", { name: "No schedules found for this term" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Master Schedule" })).toHaveAttribute(
    "href",
    "/master-schedule",
  );
});

it("defaults schedule selector to the newest candidate schedule", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const select = (await screen.findByLabelText("Select Schedule")) as HTMLSelectElement;
  // sched-candidate-2 was created 2026-09-08T10:00:00Z (the newest)
  expect(select.value).toBe("sched-candidate-2");
  expect(screen.getByLabelText("Candidate schedule")).toBeInTheDocument();
  // 16px below md so iOS does not zoom on focus.
  expect(select).toHaveClass("text-base", "md:text-sm");
});

it("badges finalized schedules with lock icon and accessible text when selected", async () => {
  const finalizedProjection: TimetableProjection = {
    ...mockProjection,
    schedule_id: "sched-master-1",
    schedule_type: "master",
    finalized_at: "2026-09-07T12:00:00Z",
    finalization_state: "finalized",
  };
  vi.mocked(api.getTimetableProjection).mockResolvedValue(finalizedProjection);

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const user = userEvent.setup();
  const select = await screen.findByLabelText("Select Schedule");
  await user.selectOptions(select, "sched-master-1");

  expect(await screen.findByLabelText("Finalized read-only schedule")).toBeInTheDocument();
  expect(screen.getByText("Finalized (Read-only)")).toBeInTheDocument();
});

it("renders assignments across Monday-Saturday columns spanning their scheduled durations", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  // Column headers
  expect(await screen.findByText("Monday")).toBeInTheDocument();
  expect(screen.getByText("Tuesday")).toBeInTheDocument();
  expect(screen.getByText("Wednesday")).toBeInTheDocument();
  expect(screen.getByText("Thursday")).toBeInTheDocument();
  expect(screen.getByText("Friday")).toBeInTheDocument();
  expect(screen.getByText("Saturday")).toBeInTheDocument();

  // rec-1 meets MWF 07:30-09:00 -> 3 articles rendered
  const mwfCards = screen.getAllByText("CS101 G1");
  expect(mwfCards).toHaveLength(3);

  // rec-2 meets TTh 09:00-11:30 -> 2 articles rendered
  const tthCards = screen.getAllByText("CS102 G1");
  expect(tthCards).toHaveLength(2);

  // Display details: Subject Group, Block, faculty, Room, and time
  expect(screen.getAllByText("BSCS-1A").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Dr. Alan Turing").length).toBeGreaterThan(0);
  expect(screen.getAllByText("LB441").length).toBeGreaterThan(0);
  expect(screen.getAllByText("07:30-09:00").length).toBeGreaterThan(0);
});

it("filters timetable assignments by Block and clears the filter", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const user = userEvent.setup();
  const blockFilter = await screen.findByLabelText("Filter by Block");

  // Filter by block-1
  vi.mocked(api.getTimetableProjection).mockResolvedValueOnce({
    ...mockProjection,
    assignments: [mockProjection.assignments[0]], // only CS101 for block-1
  });

  await user.selectOptions(blockFilter, "block-1");

  expect(api.getTimetableProjection).toHaveBeenCalledWith(
    "sched-candidate-2",
    "term-1",
    expect.objectContaining({ blockId: "block-1" }),
  );

  // Clear filter button appears
  const clearBtn = await screen.findByRole("button", { name: "Clear filter" });
  expect(clearBtn).toBeInTheDocument();

  // Clicking clear resets both filters
  await user.click(clearBtn);
  expect((screen.getByLabelText("Filter by Block") as HTMLSelectElement).value).toBe("");
  expect((screen.getByLabelText("Filter by Room") as HTMLSelectElement).value).toBe("");
});

it("filters timetable assignments by Room and clears the filter", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const user = userEvent.setup();
  const roomFilter = await screen.findByLabelText("Filter by Room");

  vi.mocked(api.getTimetableProjection).mockResolvedValueOnce({
    ...mockProjection,
    assignments: [mockProjection.assignments[1]], // only CS102 for LB442
  });

  await user.selectOptions(roomFilter, "LB442");

  expect(api.getTimetableProjection).toHaveBeenCalledWith(
    "sched-candidate-2",
    "term-1",
    expect.objectContaining({ roomCode: "LB442" }),
  );

  const clearBtn = await screen.findByRole("button", { name: "Clear filter" });
  await user.click(clearBtn);
  expect((screen.getByLabelText("Filter by Room") as HTMLSelectElement).value).toBe("");
});

it("offers an inactive teacher in the Faculty filter only while the shown schedule assigns them", async () => {
  const retired = { ...mockFaculty[1], faculty_id: "fac-3", full_name: "Grace Retired", is_active: false };
  const unassigned = { ...mockFaculty[1], faculty_id: "fac-4", full_name: "Old Nobody", is_active: false };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [...mockFaculty, retired, unassigned] });
  vi.mocked(api.listSchedules).mockResolvedValue({
    items: [
      {
        ...mockSchedules[0],
        assignments: [{
          record_id: "rec-9", class_id: "class-9", offering_id: "off-9", block_id: "block-9", faculty_id: "fac-3",
          subject_code: "CS 101", room_code: null, meeting_days: "M", time_slot_id: "slot-9", time_slot: "07:30-09:00",
        }],
      },
      ...mockSchedules.slice(1),
    ],
  });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const facultyFilter = await screen.findByLabelText("Filter by Faculty");
  expect(api.listFaculty).toHaveBeenCalledWith(true);
  expect(within(facultyFilter).getByRole("option", { name: "Faculty: Grace Retired" })).toBeInTheDocument();
  expect(within(facultyFilter).queryByRole("option", { name: "Faculty: Old Nobody" })).not.toBeInTheDocument();
});

it("filters by Faculty and clears the filter", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const user = userEvent.setup();
  const facultyFilter = await screen.findByLabelText("Filter by Faculty");
  expect(within(facultyFilter).getByRole("option", { name: "Faculty: Alan Turing" })).toBeInTheDocument();

  await user.selectOptions(facultyFilter, "fac-2");

  expect(api.getTimetableProjection).toHaveBeenLastCalledWith(
    "sched-candidate-2",
    "term-1",
    expect.objectContaining({ facultyId: "fac-2", blockId: undefined, roomCode: undefined }),
  );

  await user.click(await screen.findByRole("button", { name: "Clear filter" }));
  expect((screen.getByLabelText("Filter by Faculty") as HTMLSelectElement).value).toBe("");
});

it("shows filter-no-results state when a filter matches zero assignments", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const user = userEvent.setup();
  const blockFilter = await screen.findByLabelText("Filter by Block");

  vi.mocked(api.getTimetableProjection).mockResolvedValueOnce({
    ...mockProjection,
    assignments: [],
  });

  await user.selectOptions(blockFilter, "block-1");

  expect(
    await screen.findByText("No assignments match the selected filter."),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Clear filter" }).length).toBeGreaterThanOrEqual(1);
});

it("flags conflicting assignments with accessible text and icons without relying solely on color", async () => {
  const conflictingProjection: TimetableProjection = {
    ...mockProjection,
    assignments: [
      {
        ...mockProjection.assignments[0],
        faculty_id: "fac-same",
      },
      {
        ...mockProjection.assignments[1],
        faculty_id: "fac-same",
        meeting_days: "M", // meets Monday 09:00-11:30, but let's make it overlap Monday 08:00-09:30
        start_time: "08:00:00",
        end_time: "09:30:00",
      },
    ],
  };
  vi.mocked(api.getTimetableProjection).mockResolvedValue(conflictingProjection);
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 2,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 1,
    conflicts: [
      {
        code: "FACULTY_OVERLAP",
        resource_key: ["fac-same"],
        class_ids: ["class-1", "class-2"],
        time_slot_ids: ["slot-1", "slot-2"],
        message: "Instructor double-booked on Monday",
      },
    ],
    export_allowed: false,
  });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  // Status badge in header
  expect(await screen.findByLabelText("1 conflict errors detected")).toBeInTheDocument();

  // Explicit conflict text in cards
  const conflictBadges = await screen.findAllByTitle(/Instructor double-booked/);
  expect(conflictBadges.length).toBeGreaterThan(0);

  // Accessible article aria-label contains Conflict
  const articles = screen.getAllByRole("article");
  expect(articles[0].getAttribute("aria-label")).toContain("Conflict:");

  // Overlapping cards stay readable: the day column is widened for two
  // side-by-side cards, and each card shows code, Block and room in full.
  const grid = screen.getByRole("region", { name: "Committee Timetable Grid" });
  expect(grid.getAttribute("style")).toContain(`minmax(${2 * MIN_CARD_WIDTH_PX}px, 1fr)`);
  for (const article of articles) {
    expect(article).toHaveClass("border-error");
    const a = mockProjection.assignments.find((x) => article.getAttribute("aria-label")?.startsWith(x.group_label));
    expect(a).toBeDefined();
    expect(within(article).getByText(a!.group_label)).toBeInTheDocument();
    expect(within(article).getByText(a!.block_name)).toBeInTheDocument();
    expect(within(article).getByText("Conflict")).toBeInTheDocument();
  }
});

it("handles API error with role alert and retry button", async () => {
  vi.mocked(api.getTimetableProjection).mockRejectedValue(
    new ApiError(500, "INTERNAL_ERROR", "Server failure fetching projection."),
  );

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText("Server failure fetching projection.")).toBeInTheDocument();
  expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

it("does not flag false conflicts when room capacity is less than 20", async () => {
  const smallRoomProjection: TimetableProjection = {
    ...mockProjection,
    assignments: [
      {
        ...mockProjection.assignments[0],
        room_capacity: 15,
      },
    ],
  };
  vi.mocked(api.getTimetableProjection).mockResolvedValue(smallRoomProjection);
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 1,
    assigned_class_count: 1,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: true,
  });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(await screen.findByText("Conflict-free")).toBeInTheDocument();
  expect(screen.queryByText("Conflict")).not.toBeInTheDocument();
  const articles = screen.getAllByRole("article");
  expect(articles[0].getAttribute("aria-label")).not.toContain("Conflict:");
});

const renderWithValidation = async (assigned: number, required: number) => {
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: required,
    assigned_class_count: assigned,
    completeness_percentage: required ? (assigned / required) * 100 : 0,
    unassigned_class_ids: [],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: false,
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  await screen.findByRole("combobox", { name: /schedule/i });
};

it("shows no conflict badge for a schedule with no assignments", async () => {
  await renderWithValidation(0, 51);
  expect(screen.queryByText("Conflict-free")).not.toBeInTheDocument();
  expect(screen.queryByText("No conflicts so far")).not.toBeInTheDocument();
});

it("shows a neutral badge, not Conflict-free, while classes are unassigned", async () => {
  await renderWithValidation(4, 51);
  expect(await screen.findByText("No conflicts so far")).toBeInTheDocument();
  expect(screen.queryByText("Conflict-free")).not.toBeInTheDocument();
});

it("does not say 'so far' on a finalized schedule that validates incomplete", async () => {
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    schedule_type: "master",
    finalized_at: "2026-09-07T12:00:00Z",
    finalization_state: "finalized",
  });
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 51,
    assigned_class_count: 4,
    completeness_percentage: 8,
    unassigned_class_ids: [],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: false,
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Select Schedule"), "sched-master-1");
  expect(await screen.findByText("No conflict errors")).toBeInTheDocument();
  expect(screen.queryByText("No conflicts so far")).not.toBeInTheDocument();
});

it("shows no conflict badge when validation fails to load", async () => {
  vi.mocked(api.validateSchedule).mockRejectedValue(new Error("boom"));
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  await screen.findByRole("combobox", { name: /schedule/i });
  expect(screen.queryByText("Conflict-free")).not.toBeInTheDocument();
  expect(screen.queryByText("No conflicts so far")).not.toBeInTheDocument();
});

it("preserves conflict indicators on visible cards when filtering by block", async () => {
  const scheduleWithAssignments: Schedule = {
    ...mockSchedules[0],
    assignments: [
      {
        record_id: "rec-1",
        class_id: "class-1",
        offering_id: "off-1",
        block_id: "block-1",
        faculty_id: "fac-1",
        subject_code: "CS101",
        room_code: "LB441",
        meeting_days: "MWF",
        time_slot_id: "slot-1",
        time_slot: null,
      },
      {
        record_id: "rec-2",
        class_id: "class-2",
        offering_id: "off-2",
        block_id: "block-2",
        faculty_id: "fac-2",
        subject_code: "CS102",
        room_code: "LB441",
        meeting_days: "MWF",
        time_slot_id: "slot-1",
        time_slot: null,
      },
    ],
  };
  vi.mocked(api.listSchedules).mockResolvedValue({
    items: [scheduleWithAssignments, ...mockSchedules.slice(1)],
  });
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 2,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 1,
    conflicts: [
      {
        code: "ROOM_DOUBLE_BOOKED",
        resource_key: ["LB441"],
        class_ids: ["class-1", "class-2"],
        time_slot_ids: ["slot-1"],
        message: "Room double-booked on Monday",
      },
    ],
    export_allowed: false,
  });

  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  // Initially both cards show conflict
  expect(await screen.findByLabelText("1 conflict errors detected")).toBeInTheDocument();
  expect((await screen.findAllByTitle(/Room double-booked/)).length).toBeGreaterThan(0);

  // When filtering by block, projection returns only the matching block's assignment
  vi.mocked(api.getTimetableProjection).mockResolvedValueOnce({
    ...mockProjection,
    assignments: [mockProjection.assignments[0]],
  });

  const blockFilter = screen.getByLabelText("Filter by Block");
  await user.selectOptions(blockFilter, "block-1");

  // rec-1 is still visible and STILL has the conflict badge, even though rec-2 is filtered out
  const filteredConflict = await screen.findAllByTitle(/Room double-booked/);
  expect(filteredConflict.length).toBeGreaterThan(0);
});

it("does not flag unrelated assignments of the same faculty on different time slots", async () => {
  const multiSlotProjection: TimetableProjection = {
    ...mockProjection,
    assignments: [
      {
        ...mockProjection.assignments[0],
        faculty_id: "fac-1",
        time_slot_id: "slot-1",
      },
      {
        ...mockProjection.assignments[1],
        faculty_id: "fac-1",
        time_slot_id: "slot-2",
      },
    ],
  };
  vi.mocked(api.getTimetableProjection).mockResolvedValue(multiSlotProjection);
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 2,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 1,
    conflicts: [
      {
        code: "FACULTY_OVERLAP",
        resource_key: ["fac-1"],
        class_ids: [],
        time_slot_ids: ["slot-1"],
        message: "Instructor double-booked at 07:30",
      },
    ],
    export_allowed: false,
  });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(await screen.findByLabelText("1 conflict errors detected")).toBeInTheDocument();

  const articles = screen.getAllByRole("article");
  const rec1Article = articles.find((a) => a.getAttribute("aria-label")?.includes("CS101"));
  const rec2Article = articles.find((a) => a.getAttribute("aria-label")?.includes("CS102"));

  expect(rec1Article?.getAttribute("aria-label")).toContain("Conflict:");
  expect(rec2Article?.getAttribute("aria-label")).not.toContain("Conflict:");
});

it("fetches projection only once on initial mount", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await screen.findByText("Monday");
  expect(api.getTimetableProjection).toHaveBeenCalledTimes(1);
  expect(api.getTimetableProjection).toHaveBeenCalledWith("sched-candidate-2", "term-1", {
    blockId: undefined,
    roomCode: undefined,
  });
});

it("does not render empty or no-active-term states when an API error occurs", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(500, "INTERNAL_ERROR", "Database connection lost"),
  );

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "No active term selected" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "No schedules found for this term" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Select Schedule")).not.toBeInTheDocument();
});

it("renders dynamic calendar hours and intervals from projection metadata", async () => {
  const customCalendarProjection: TimetableProjection = {
    ...mockProjection,
    calendar: {
      teaching_days: ["MON", "TUE", "WED", "THU", "FRI", "SAT"],
      opening_time: "08:00:00",
      closing_time: "17:00:00",
      interval_minutes: 60,
    },
  };
  vi.mocked(api.getTimetableProjection).mockResolvedValue(customCalendarProjection);

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  expect(
    await screen.findByText("Hours: 08:00 – 17:00 (60-min intervals) | Monday – Saturday"),
  ).toBeInTheDocument();
});

it.each([
  ["Filter by Block", "block-1"],
  ["Filter by Room", "LB441"],
])("clears %s and the old finalized timetable when the active term changes", async (filter, value) => {
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [mockSchedules[2]] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    finalized_at: mockSchedules[2].finalized_at,
    finalization_state: "finalized",
  });
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText(filter), value);
  await screen.findByRole("button", { name: "Export CSV" });

  const nextSchedule = { ...mockSchedules[0], schedule_id: "next-schedule", term_id: "term-2" };
  vi.mocked(api.getActiveTerm).mockResolvedValue({ ...mockTerm, term_id: "term-2", term_number: 2 });
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [nextSchedule] });
  vi.mocked(api.listBlocks).mockResolvedValue({ items: [{ ...mockBlocks[0], block_id: "next-block", term_id: "term-2" }] });
  vi.mocked(api.listRooms).mockResolvedValue({ items: [mockRooms[1]] });
  let finish: (projection: TimetableProjection) => void = () => {};
  vi.mocked(api.getTimetableProjection).mockReturnValue(new Promise((resolve) => { finish = resolve; }));

  await act(async () => publishTermChanged());
  expect(api.getTimetableProjection).toHaveBeenLastCalledWith("next-schedule", "term-2", {
    blockId: undefined,
    roomCode: undefined,
  });
  expect(screen.queryByRole("region", { name: "Committee Timetable Grid" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Finalized read-only schedule")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();

  await act(async () => finish({
    ...mockProjection,
    schedule_id: "next-schedule",
    term_id: "term-2",
    assignments: [{ ...mockProjection.assignments[1], block_id: "next-block", group_label: "NEXT G1" }],
  }));
  expect(await screen.findByLabelText("Candidate schedule")).toBeInTheDocument();
  expect(screen.getByLabelText("Select Schedule")).toHaveValue("next-schedule");
  expect(screen.getByLabelText("Filter by Block")).toHaveValue("");
  expect(screen.getByLabelText("Filter by Room")).toHaveValue("");
  expect(screen.getAllByText("NEXT G1")).toHaveLength(2);
  expect(screen.queryByText("CS101 G1")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();
});

it("applies refreshed schedule metadata only with its matching projection", async () => {
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  await screen.findByLabelText("Candidate schedule");
  const finalizedAt = "2026-09-09T12:00:00Z";
  vi.mocked(api.listSchedules).mockResolvedValue({
    items: [{ ...mockSchedules[0], finalized_at: finalizedAt }],
  });
  let finish: (projection: TimetableProjection) => void = () => {};
  vi.mocked(api.getTimetableProjection).mockReturnValue(new Promise((resolve) => { finish = resolve; }));

  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(screen.getByLabelText("Candidate schedule")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();

  await act(async () => finish({ ...mockProjection, finalized_at: finalizedAt, finalization_state: "finalized" }));
  expect(screen.getByLabelText("Finalized read-only schedule")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
});

it("reports a failed background projection refresh without displaying the old timetable", async () => {
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  await screen.findByText("Monday");
  vi.mocked(api.getTimetableProjection).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Projection refresh failed."));

  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Projection refresh failed.");
  expect(screen.queryByRole("region", { name: "Committee Timetable Grid" })).not.toBeInTheDocument();
  expect(screen.queryByText("This schedule contains no assignments yet.")).not.toBeInTheDocument();
});

it("does not restore the previous term's room filter after a failed transition is retried", async () => {
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Filter by Room"), "LB441");
  vi.mocked(api.getActiveTerm).mockResolvedValue({ ...mockTerm, term_id: "term-2", term_number: 2 });
  vi.mocked(api.listSchedules).mockResolvedValue({
    items: [{ ...mockSchedules[0], schedule_id: "next-schedule", term_id: "term-2" }],
  });
  vi.mocked(api.getTimetableProjection).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Try again."));

  await act(async () => publishTermChanged());
  expect(await screen.findByRole("alert")).toHaveTextContent("Try again.");
  await user.click(screen.getByRole("button", { name: "Retry" }));

  await screen.findByLabelText("Filter by Room");
  expect(api.getTimetableProjection).toHaveBeenLastCalledWith("next-schedule", "term-2", {
    blockId: undefined,
    roomCode: undefined,
  });
  expect(screen.getByLabelText("Filter by Room")).toHaveValue("");
});

it.each([
  ["Filter by Block", "block-1"],
  ["Filter by Room", "LB441"],
])("clears a removed %s before a background replacement projection arrives", async (filter, value) => {
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [mockSchedules[2]] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection, schedule_id: "sched-master-1", finalization_state: "finalized",
  });
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText(filter), value);
  await screen.findByRole("button", { name: "Export CSV" });
  vi.mocked(api.listBlocks).mockResolvedValue({ items: [mockBlocks[1]] });
  vi.mocked(api.listRooms).mockResolvedValue({ items: [mockRooms[1]] });
  let finish: (projection: TimetableProjection) => void = () => {};
  vi.mocked(api.getTimetableProjection).mockReturnValue(new Promise((resolve) => { finish = resolve; }));

  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(api.getTimetableProjection).toHaveBeenLastCalledWith("sched-master-1", "term-1", {
    blockId: undefined, roomCode: undefined,
  });
  expect(screen.queryByRole("region", { name: "Committee Timetable Grid" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();

  await act(async () => finish({ ...mockProjection, schedule_id: "sched-master-1", finalization_state: "finalized" }));
  expect(await screen.findByLabelText(filter)).toHaveValue("");
});

it("ignores an old pending projection after an explicit term switch", async () => {
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  await screen.findByText("Monday");
  let finishOld: (projection: TimetableProjection) => void = () => {};
  vi.mocked(api.getTimetableProjection).mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }));
  await act(async () => window.dispatchEvent(new Event("focus")));

  vi.mocked(api.getActiveTerm).mockResolvedValue({ ...mockTerm, term_id: "term-2", term_number: 2 });
  vi.mocked(api.listSchedules).mockResolvedValue({
    items: [{ ...mockSchedules[0], schedule_id: "next-schedule", term_id: "term-2" }],
  });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection, schedule_id: "next-schedule", term_id: "term-2",
    assignments: [{ ...mockProjection.assignments[1], group_label: "NEXT G1" }],
  });

  await act(async () => publishTermChanged());
  expect(screen.getByLabelText("Select Schedule")).toHaveValue("next-schedule");
  await act(async () => finishOld({ ...mockProjection, finalization_state: "finalized" }));
  expect(screen.getAllByText("NEXT G1")).toHaveLength(2);
  expect(screen.queryByText("CS101 G1")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();
});

// jsdom has no object URLs; stub them and the anchor click a download uses.
function stubDownload() {
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  const createObjectURL = vi.fn(() => "blob:export");
  const revokeObjectURL = vi.fn();
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return {
    createObjectURL,
    revokeObjectURL,
    click,
    restore: () => {
      URL.createObjectURL = original.create;
      URL.revokeObjectURL = original.revoke;
      click.mockRestore();
    },
  };
}

async function selectFinalizedMaster() {
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    schedule_type: "master",
    finalized_at: "2026-09-07T12:00:00Z",
    finalization_state: "finalized",
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Select Schedule"), "sched-master-1");
  await screen.findByLabelText("Finalized read-only schedule");
  return user;
}

it("offers no export for a candidate schedule", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await screen.findByLabelText("Candidate schedule");
  expect(screen.queryByRole("button", { name: /Export CSV/ })).not.toBeInTheDocument();
});

it("exports the finalized schedule with the Faculty filter", async () => {
  stubDownload();
  vi.mocked(api.exportSchedule).mockResolvedValue({ blob: new Blob(["x"]), filename: "f.csv" });

  const user = await selectFinalizedMaster();
  await user.selectOptions(screen.getByLabelText("Filter by Faculty"), "fac-1");
  await user.click(await screen.findByRole("button", { name: /Export CSV/ }));

  expect(api.exportSchedule).toHaveBeenCalledWith(
    "sched-master-1",
    "csv",
    expect.objectContaining({ facultyId: "fac-1" }),
  );
});

it("downloads the finalized schedule in the chosen format", async () => {
  const download = stubDownload();
  const blob = new Blob(["xlsx"]);
  vi.mocked(api.exportSchedule).mockResolvedValue({ blob, filename: "chronos_2025-2026_term1_20261113-1430.xlsx" });

  const user = await selectFinalizedMaster();
  await user.click(screen.getByRole("button", { name: /Export XLSX/ }));

  expect(api.exportSchedule).toHaveBeenCalledWith("sched-master-1", "xlsx", {
    blockId: undefined,
    roomCode: undefined,
  });
  expect(screen.getByTestId("export-scope")).toHaveTextContent("Whole schedule");
  expect(download.createObjectURL).toHaveBeenCalledWith(blob);
  expect(download.click).toHaveBeenCalledOnce();
  await vi.waitFor(() => expect(download.revokeObjectURL).toHaveBeenCalledWith("blob:export"), {
    timeout: 2000,
  });
  download.restore();
});

it("shows the backend refusal when export is not allowed", async () => {
  vi.mocked(api.exportSchedule).mockRejectedValue(
    new ApiError(422, "EXPORT_NOT_ALLOWED", "The schedule has unresolved hard conflicts."),
  );

  const user = await selectFinalizedMaster();
  await user.click(screen.getByRole("button", { name: /Export CSV/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The schedule has unresolved hard conflicts.");
});

it("disables export when the server's latest validation withholds it, and enables it when validation is unknown", async () => {
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 2,
    assigned_class_count: 1,
    completeness_percentage: 50,
    unassigned_class_ids: ["class-2"],
    hard_conflict_count: 0,
    conflicts: [],
    export_allowed: false,
  });
  const view = await selectFinalizedMaster();
  expect(await screen.findByText("Export blocked by server validation.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Export CSV/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Export XLSX/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Export PDF/ })).toBeDisabled();
  // The reason is programmatic, not only further down the page.
  expect(screen.getByRole("button", { name: /Export CSV/ })).toHaveAccessibleDescription("Export blocked by server validation.");
  expect(screen.getByRole("button", { name: /Export XLSX/ })).toHaveAccessibleDescription("Export blocked by server validation.");
  expect(screen.getByRole("button", { name: /Export PDF/ })).toHaveAccessibleDescription("Export blocked by server validation.");
  await view.click(screen.getByRole("button", { name: /Export CSV/ }));
  expect(api.exportSchedule).not.toHaveBeenCalled();

  // Unknown is not a refusal: the buttons stay enabled and the server decides.
  vi.mocked(api.validateSchedule).mockRejectedValue(new Error("offline"));
  await view.click(screen.getByRole("button", { name: "Refresh timetable" }));
  expect(await screen.findByText(/eligibility is unknown/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Export CSV/ })).toBeEnabled();
  expect(screen.getByRole("button", { name: /Export PDF/ })).toBeEnabled();
  expect(screen.getByRole("button", { name: /Export CSV/ })).not.toHaveAttribute("aria-describedby");
});

it("blocks a second export while one is in flight", async () => {
  let finish: (value: { blob: Blob; filename: string }) => void = () => {};
  vi.mocked(api.exportSchedule).mockReturnValue(new Promise((resolve) => { finish = resolve; }));

  const user = await selectFinalizedMaster();
  await user.click(screen.getByRole("button", { name: /Export CSV/ }));

  expect(screen.getByRole("button", { name: /Exporting CSV/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Export XLSX/ })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Export XLSX/ }));
  expect(api.exportSchedule).toHaveBeenCalledTimes(1);

  const download = stubDownload();
  await act(async () => finish({ blob: new Blob(["a"]), filename: "f.csv" }));
  expect(screen.getByRole("button", { name: /Export CSV/ })).toBeEnabled();
  download.restore();
});

it("keeps a delayed export refusal off a schedule selected afterwards", async () => {
  let refuse: (error: ApiError) => void = () => {};
  vi.mocked(api.exportSchedule).mockReturnValue(new Promise((_resolve, reject) => { refuse = reject; }));

  const user = await selectFinalizedMaster();
  await user.click(screen.getByRole("button", { name: /Export CSV/ }));
  await user.selectOptions(screen.getByLabelText("Select Schedule"), "sched-candidate-2");
  await act(async () => refuse(new ApiError(422, "EXPORT_NOT_ALLOWED", "The schedule has unresolved hard conflicts.")));

  expect(screen.queryByText("The schedule has unresolved hard conflicts.")).not.toBeInTheDocument();
  expect(api.exportSchedule).toHaveBeenCalledWith("sched-master-1", "csv", {
    blockId: undefined,
    roomCode: undefined,
  });
});

it("exports only the Block or room the timetable is filtered to", async () => {
  const download = stubDownload();
  vi.mocked(api.exportSchedule).mockResolvedValue({ blob: new Blob(["a"]), filename: "f.csv" });

  const user = await selectFinalizedMaster();
  await user.selectOptions(screen.getByLabelText("Filter by Block"), "block-1");
  expect(screen.getByTestId("export-scope")).toHaveTextContent("Block BSCS-1A only");
  await user.click(screen.getByRole("button", { name: /Export CSV/ }));
  expect(api.exportSchedule).toHaveBeenLastCalledWith("sched-master-1", "csv", {
    blockId: "block-1",
    roomCode: undefined,
  });

  await user.selectOptions(screen.getByLabelText("Filter by Room"), "LB441");
  expect(screen.getByTestId("export-scope")).toHaveTextContent("Room LB441 only");
  await user.click(screen.getByRole("button", { name: /Export XLSX/ }));
  expect(api.exportSchedule).toHaveBeenLastCalledWith("sched-master-1", "xlsx", {
    blockId: undefined,
    roomCode: "LB441",
  });
  download.restore();
});

it("deletes the selected candidate only after confirmation, then reloads", async () => {
  const user = userEvent.setup();
  vi.mocked(api.deleteSchedule).mockResolvedValue(undefined);
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await user.click(await screen.findByRole("button", { name: "Delete candidate" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/cannot be undone/)).toBeInTheDocument();
  expect(api.deleteSchedule).not.toHaveBeenCalled();

  vi.mocked(api.listSchedules).mockResolvedValue({ items: mockSchedules.slice(1) });
  await user.click(within(dialog).getByRole("button", { name: "Delete candidate" }));

  expect(api.deleteSchedule).toHaveBeenCalledWith("sched-candidate-2");
  const select = (await screen.findByLabelText("Select Schedule")) as HTMLSelectElement;
  await vi.waitFor(() => expect(select.value).toBe("sched-candidate-1"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps the dialog open and shows the server refusal", async () => {
  const user = userEvent.setup();
  vi.mocked(api.deleteSchedule).mockRejectedValue(
    new ApiError(409, "SCHEDULE_FINALIZED", "A finalized schedule cannot be modified."),
  );
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await user.click(await screen.findByRole("button", { name: "Delete candidate" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Delete candidate" }));

  expect(await within(dialog).findByRole("alert")).toHaveTextContent("A finalized schedule cannot be modified.");
});

it("offers no delete for a finalized schedule", async () => {
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [mockSchedules[2]] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    finalized_at: "2026-09-07T12:00:00Z",
    finalization_state: "finalized",
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await screen.findByLabelText("Finalized read-only schedule");
  expect(screen.queryByRole("button", { name: "Delete candidate" })).not.toBeInTheDocument();
});

it("words the dialog for an unfinalized master schedule and deletes that one", async () => {
  const user = userEvent.setup();
  const master = { ...mockSchedules[2], finalized_at: null };
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [master] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    schedule_type: "master",
    finalization_state: "candidate",
  });
  vi.mocked(api.deleteSchedule).mockResolvedValue(undefined);
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await user.click(await screen.findByRole("button", { name: "Delete master schedule" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/every Block assignment confirmed on it/)).toBeInTheDocument();
  expect(within(dialog).getByText(/starts again from empty/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete master schedule" }));

  expect(api.deleteSchedule).toHaveBeenCalledWith("sched-master-1");
});

it("closes the dialog when the selection moves to another schedule", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await user.click(await screen.findByRole("button", { name: "Delete candidate" }));
  await screen.findByRole("dialog");
  // The modal is inert to the pointer, so change the selection as a background sync would.
  const select = screen.getByLabelText("Select Schedule") as HTMLSelectElement;
  await act(async () => {
    fireEvent.change(select, { target: { value: "sched-candidate-1" } });
  });

  await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.deleteSchedule).not.toHaveBeenCalled();
});

it("offers a day switcher that shows one weekday at narrow widths and keeps every column for the desktop grid", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const switcher = await screen.findByRole("group", { name: "Select day" });
  expect(within(switcher).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
    "Monday, 1 class", "Tuesday, 1 class", "Wednesday, 1 class", "Thursday, 1 class", "Friday, 1 class", "Saturday, no classes",
  ]);
  const grid = screen.getByRole("region", { name: "Committee Timetable Grid" });
  const header = (day: string) => within(grid).getByText(day).closest("div")!;

  // Opens on the first day with classes; the other days are md-only.
  expect(within(switcher).getByRole("button", { name: /^Monday/ })).toHaveAttribute("aria-pressed", "true");
  expect(header("Monday").className).not.toContain("hidden md:block");
  expect(header("Tuesday").className).toContain("hidden md:block print:block");

  await user.click(within(switcher).getByRole("button", { name: /^Tuesday/ }));
  expect(within(switcher).getByRole("button", { name: /^Tuesday/ })).toHaveAttribute("aria-pressed", "true");
  expect(header("Tuesday").className).not.toContain("hidden md:block");
  expect(header("Monday").className).toContain("hidden md:block");
  expect(grid.innerHTML).toContain("md:grid-cols-[var(--tt-cols)]");
  expect(grid.getAttribute("style")).toContain("--tt-cols-mobile: 64px minmax(");
});

it("says when the chosen day has no classes, jumps to one that does, and resets the day when the filter changes", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const switcher = await screen.findByRole("group", { name: "Select day" });
  await user.click(within(switcher).getByRole("button", { name: /^Saturday/ }));
  expect(screen.getByText("No classes on Saturday.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show Monday" }));
  expect(within(switcher).getByRole("button", { name: /^Monday/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText(/^No classes on/)).not.toBeInTheDocument();

  await user.click(within(switcher).getByRole("button", { name: /^Thursday/ }));
  await user.selectOptions(screen.getByLabelText("Filter by Block"), "block-2");
  await vi.waitFor(() =>
    expect(within(screen.getByRole("group", { name: "Select day" })).getByRole("button", { name: /^Monday/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    ),
  );
});

it("collapses the filters behind a toggle at narrow widths", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const toggle = await screen.findByRole("button", { name: "Show filters" });
  const filters = document.getElementById("timetable-filters")!;
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(filters.className).toContain("hidden md:flex");
  await user.click(toggle);
  expect(screen.getByRole("button", { name: "Hide filters" })).toHaveAttribute("aria-expanded", "true");
  expect(filters).not.toHaveClass("hidden");
});

it("calls an unfinalized master an accepted draft and links to finalization on the Master Schedule", async () => {
  const draftMaster: Schedule = {
    ...mockSchedules[2],
    finalized_at: null,
    assignments: [{
      record_id: "r1", class_id: "c1", offering_id: "o1", block_id: "b1", faculty_id: "f1",
      subject_code: "CS101", room_code: null, meeting_days: null, time_slot_id: "s1", time_slot: null,
    }],
  };
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [draftMaster] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: draftMaster.schedule_id,
    schedule_type: "master",
  });

  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("Accepted draft schedule")).toBeInTheDocument();
  expect(screen.queryByText("Candidate Schedule")).not.toBeInTheDocument();
  expect(screen.getByText("Ready to finalize.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Master Schedule to finalize" })).toHaveAttribute("href", "/master-schedule");
});

it("shows an empty master as Empty and a routine candidate with neutral eligibility wording", async () => {
  const emptyMaster: Schedule = { ...mockSchedules[2], finalized_at: null };
  vi.mocked(api.listSchedules).mockResolvedValue({ items: [emptyMaster] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue({ ...mockProjection, schedule_id: emptyMaster.schedule_id, schedule_type: "master" });
  const first = render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  expect(await screen.findByLabelText("Empty master schedule")).toBeInTheDocument();
  first.unmount();

  vi.mocked(api.listSchedules).mockResolvedValue({ items: [mockSchedules[0]] });
  vi.mocked(api.getTimetableProjection).mockResolvedValue(mockProjection);
  render(<MemoryRouter><CommitteeTimetablePage /></MemoryRouter>);
  expect(await screen.findByText("Finalization eligible.")).toBeInTheDocument();
  expect(screen.queryByText("Ready to finalize.")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Open Master Schedule to finalize" })).not.toBeInTheDocument();
});

it("opens the schedule named by the export link instead of the newest candidate", async () => {
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    schedule_id: "sched-master-1",
    schedule_type: "master",
    finalized_at: "2026-09-07T12:00:00Z",
    finalization_state: "finalized",
  });

  render(
    <MemoryRouter initialEntries={["/timetable?schedule=sched-master-1"]}>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  expect(((await screen.findByLabelText("Select Schedule")) as HTMLSelectElement).value).toBe("sched-master-1");
  expect(screen.getByRole("button", { name: "Export PDF" })).toBeInTheDocument();
});

it("keeps the requested schedule when the first projection load fails and is retried", async () => {
  vi.mocked(api.getTimetableProjection)
    .mockRejectedValueOnce(new ApiError(503, "UNAVAILABLE", "Server unavailable."))
    .mockResolvedValue({
      ...mockProjection,
      schedule_id: "sched-master-1",
      schedule_type: "master",
      finalized_at: "2026-09-07T12:00:00Z",
      finalization_state: "finalized",
    });

  render(
    <MemoryRouter initialEntries={["/timetable?schedule=sched-master-1"]}>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
  expect(((await screen.findByLabelText("Select Schedule")) as HTMLSelectElement).value).toBe("sched-master-1");
});

it("keeps the first 07:30 label inside its row and lets faculty names wrap instead of truncate", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );

  await screen.findByText("Monday");
  const firstLabel = screen.getAllByText("07:30").find((el) => el.className.includes("bg-surface-container-lowest"));
  expect(firstLabel).toBeDefined();
  expect(firstLabel).not.toHaveClass("-mt-2.5");
  expect(screen.getByText("09:00", { selector: "span.bg-surface-container-lowest" })).toHaveClass("-mt-2.5");

  const name = screen.getAllByText("Dr. Alan Turing")[0];
  expect(name).not.toHaveClass("truncate");
  expect(name).toHaveClass("line-clamp-2", "break-words");
  expect(name.nextElementSibling).toHaveTextContent("07:30-09:00");
});

it("clamps faculty names by card room: none from 2h30, one line with a conflict at 1h30, single line under 1h30", async () => {
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    assignments: [
      { ...mockProjection.assignments[0], meeting_days: "M", faculty_name: "Short 30", start_time: "07:30:00", end_time: "08:00:00" },
      { ...mockProjection.assignments[0], record_id: "rec-9", meeting_days: "T", faculty_name: "Long 90", start_time: "08:00:00", end_time: "09:30:00" },
      { ...mockProjection.assignments[1], meeting_days: "W", faculty_name: "Long 150" },
    ],
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  await screen.findByText("Monday");

  const short = screen.getByText("Short 30");
  expect(short).toHaveClass("truncate");
  expect(short.nextElementSibling).toHaveTextContent("07:30-08:00");
  expect(short.nextElementSibling).toHaveClass("shrink-0");

  expect(screen.getByText("Long 90")).toHaveClass("line-clamp-2");

  const long = screen.getByText("Long 150");
  expect(long).not.toHaveClass("truncate");
  expect(long.className).not.toMatch(/line-clamp/);
});

it("drops a 1h30 faculty name to one line when the card carries a conflict badge", async () => {
  vi.mocked(api.getTimetableProjection).mockResolvedValue({
    ...mockProjection,
    assignments: [{ ...mockProjection.assignments[0], meeting_days: "M", faculty_name: "Conflicted 90" }],
  });
  vi.mocked(api.validateSchedule).mockResolvedValue({
    required_class_count: 1,
    assigned_class_count: 1,
    completeness_percentage: 100,
    unassigned_class_ids: [],
    hard_conflict_count: 1,
    conflicts: [
      {
        code: "FACULTY_OVERLAP",
        resource_key: ["fac-1"],
        class_ids: ["class-1"],
        time_slot_ids: ["slot-1"],
        message: "Instructor double-booked on Monday",
      },
    ],
    export_allowed: false,
  });
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  await screen.findByText("Monday");
  expect(await screen.findByText("Conflict")).toBeInTheDocument();
  expect(screen.getByText("Conflicted 90")).toHaveClass("line-clamp-1");
});

it("keeps the day header and time column pinned while the grid scrolls on both axes", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const grid = await screen.findByRole("region", { name: "Committee Timetable Grid" });
  expect(grid).toHaveAttribute("tabindex", "0");
  expect(grid).toHaveClass("overflow-auto", "overscroll-x-contain", "print:max-h-none", "print:overflow-visible");
  expect(grid.className).toContain("max-h-[calc(100dvh-");
  expect(grid).toHaveClass("min-h-[16rem]", "scroll-pt-[45px]", "scroll-pl-[68px]", "md:scroll-pl-[84px]", "scroll-pb-1", "scroll-pr-1");

  const header = within(grid).getByText("Monday").closest("div")!.parentElement!;
  expect(header).toHaveClass("sticky", "top-0", "z-30", "bg-surface-container-low");
  const timeHead = within(grid).getByText("Time");
  expect(timeHead).toHaveClass("sticky", "left-0", "z-10", "bg-surface-container-low");

  const timeCol = within(grid).getByText("07:30").closest("div")!.parentElement!;
  expect(timeCol).toHaveClass("sticky", "left-0", "z-20", "bg-surface-container-lowest");

  const label = within(grid).getByText("Monday").parentElement!;
  expect(label).toHaveClass("inline-block", "sticky", "left-[calc(64px+0.5rem)]", "md:left-[calc(80px+0.5rem)]");

  // Hovered and focused cards stay under both sticky edges (z-20 and z-30).
  const card = within(grid).getAllByRole("article")[0];
  expect(card).toHaveClass("z-10", "hover:z-[15]", "focus-visible:z-[16]");
});

it("scrolls a card into view on keyboard focus only", async () => {
  render(
    <MemoryRouter>
      <CommitteeTimetablePage />
    </MemoryRouter>,
  );
  const card = (await screen.findAllByRole("article"))[0];
  const scrollIntoView = vi.fn();
  card.scrollIntoView = scrollIntoView;
  const matches = vi.spyOn(card, "matches");

  matches.mockReturnValue(false); // mouse focus
  fireEvent.focus(card);
  expect(scrollIntoView).not.toHaveBeenCalled();

  matches.mockImplementation((selector) => selector === ":focus-visible"); // keyboard focus
  fireEvent.focus(card); // no relatedTarget: returning to the tab or window
  expect(scrollIntoView).not.toHaveBeenCalled();

  fireEvent.focus(card, { relatedTarget: document.body });
  expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
});
