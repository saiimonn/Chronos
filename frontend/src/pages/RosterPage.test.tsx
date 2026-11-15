import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import RosterPage from "./RosterPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listFaculty: vi.fn(),
      createFaculty: vi.fn(),
      updateFaculty: vi.fn(),
      setFacultyStatus: vi.fn(),
      getAvailability: vi.fn(),
      updateAvailability: vi.fn(),
      confirmAvailability: vi.fn(),
      listSubjects: vi.fn(),
      listTimeSlots: vi.fn(),
      getMasterSchedule: vi.fn(),
      listRuleSets: vi.fn(),
      listRuleSetRules: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

const activeTerm = vi.hoisted(() => ({ current: null as null | { term_id: string } }));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: activeTerm.current, loading: false }),
}));

const rivera = { faculty_id: "faculty-1", full_name: "A. Rivera", is_full_time: false, availability_mask: "00", availability_reviewed_term_id: null, is_active: true };
const santos = { faculty_id: "faculty-2", full_name: "B. Santos", is_full_time: true, availability_mask: "00", availability_reviewed_term_id: null, is_active: true };

const slotGrid = [
  { time_slot_id: "slot-1", label: "MWF 08:00", start_time: "08:00:00", end_time: "09:00:00", ordinal: 0 },
  { time_slot_id: "slot-2", label: "MWF 09:00", start_time: "09:00:00", end_time: "10:00:00", ordinal: 1 },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [] });
  activeTerm.current = null;
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, santos] });
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
});

afterEach(() => vi.restoreAllMocks());

it("lists faculty returned by the centralized client", async () => {
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  // No active term: the assigned units are unknown, the maximum still shows.
  const rivera = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  expect(rivera).toHaveTextContent("—/17");
  const santos = screen.getByText("B. Santos").closest("tr") as HTMLElement;
  expect(santos).toHaveTextContent("—/24");
  expect(screen.getByText("2 active (1 full-time, 1 part-time) · 0 inactive")).toBeInTheDocument();
});

it("lists faculty one per table row, sorted by surname, and re-sorts from a column heading", async () => {
  const zamora = { ...rivera, faculty_id: "faculty-z", full_name: "Cate C. Zamora" };
  const abella = { ...santos, faculty_id: "faculty-a", full_name: "Zed Q. Abella Jr." };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [zamora, abella] });

  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  await screen.findByText("Cate C. Zamora");
  const names = () => screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[1].textContent ?? "");
  expect(names()[0]).toMatch(/^(FT|PT) (full|part)-timeZed Q\. Abella Jr\./);
  expect(screen.getByRole("columnheader", { name: /name/i })).toHaveAttribute("aria-sort", "ascending");

  await userEvent.click(screen.getByRole("button", { name: /^name$/i }));
  expect(names()[0]).toMatch(/^(FT|PT) (full|part)-timeCate C\. Zamora/);
  expect(screen.getByRole("columnheader", { name: /name/i })).toHaveAttribute("aria-sort", "descending");
});

it("filters the roster by the search query", async () => {
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.type(screen.getByPlaceholderText("Search faculty"), "santos");

  expect(screen.queryByText("A. Rivera")).not.toBeInTheDocument();
  expect(screen.getByText("B. Santos")).toBeInTheDocument();
});

it("creates faculty through the client and reloads the roster", async () => {
  vi.mocked(api.createFaculty).mockResolvedValue(rivera);
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: /add faculty/i }));
  await userEvent.type(screen.getByLabelText("Full name"), "C. Delgado");
  await userEvent.click(screen.getByRole("checkbox", { name: /full-time/i }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(api.createFaculty).toHaveBeenCalledWith({ full_name: "C. Delgado", is_full_time: true, lecture_only: false, is_active: true });
  expect(api.listFaculty).toHaveBeenCalledTimes(2);
});

it("defaults a new faculty record to Part-Time and offers no workload entry", async () => {
  vi.mocked(api.createFaculty).mockResolvedValue(rivera);
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: /add faculty/i }));
  const dialog = within(screen.getByRole("dialog"));

  expect(dialog.getByRole("checkbox", { name: /full-time/i })).not.toBeChecked();
  expect(dialog.getByText(/Part-Time maximum 17 units\. Full-Time maximum 24 units\./)).toBeInTheDocument();
  expect(dialog.getByText("Part-Time — maximum 17 units.")).toBeInTheDocument();
  expect(dialog.queryByRole("spinbutton")).not.toBeInTheDocument();

  await userEvent.type(dialog.getByLabelText("Full name"), "C. Delgado");
  await userEvent.click(dialog.getByRole("button", { name: "Save" }));

  expect(api.createFaculty).toHaveBeenCalledWith({ full_name: "C. Delgado", is_full_time: false, lecture_only: false, is_active: true });
});

it("seeds the classification control from the edited faculty record", async () => {
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...santos, is_full_time: false });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("B. Santos");

  const card = screen.getByText("B. Santos").closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^edit /i }));

  const toggle = screen.getByRole("checkbox", { name: /full-time/i });
  expect(toggle).toBeChecked();

  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  // The status is untouched, so it is not sent and cannot undo another session's change.
  expect(api.updateFaculty).toHaveBeenCalledWith(santos.faculty_id, {
    full_name: santos.full_name,
    is_full_time: false,
    lecture_only: false,
  });
});

it("sends the status only when an edit changes it", async () => {
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...santos, is_active: false });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("B. Santos");

  const card = screen.getByText("B. Santos").closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^edit /i }));
  await userEvent.click(screen.getByRole("checkbox", { name: /currently teaching/i }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(santos.faculty_id, {
    full_name: santos.full_name,
    is_full_time: true,
    lecture_only: false,
    is_active: false,
  });
});

it("shows units assigned in the active term's master schedule over the maximum", async () => {
  activeTerm.current = { term_id: "term-1" };
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [
    { subject_code: "CIS 1101", subject_title: "Intro", units: 3, subject_type: "LEC", is_active: true },
    { subject_code: "CIS 1102", subject_title: "Lab", units: 2, subject_type: "LAB", is_active: true },
  ] } as never);
  const assignment = (classId: string, offeringId: string, code: string) => ({
    record_id: classId, class_id: classId, offering_id: offeringId, block_id: classId, faculty_id: "faculty-1",
    subject_code: code, room_code: null, meeting_days: "MW", time_slot_id: "slot-1", time_slot: null,
  });
  vi.mocked(api.getMasterSchedule).mockResolvedValue({
    schedule_id: "s-1", term_id: "term-1", schedule_type: "master", created_at: "", finalized_at: null,
    // Two Blocks in one combined session count once.
    assignments: [assignment("c1", "o1", "CIS 1101"), assignment("c2", "o1", "CIS 1101"), assignment("c3", "o2", "CIS 1102")],
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  const rivera = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await waitFor(() => expect(rivera).toHaveTextContent("5/17"));
  expect(screen.getByText("B. Santos").closest("tr")).toHaveTextContent("0/24");
});

it("saves lecture-only from the edit form", async () => {
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...santos, lecture_only: true });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("B. Santos")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^edit /i }));
  await userEvent.click(screen.getByRole("checkbox", { name: /lecture classes only/i }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(santos.faculty_id, {
    full_name: santos.full_name,
    is_full_time: true,
    lecture_only: true,
  });
});

it("marks a subject Never in the same Subjects dialog as Not teaching, clearing its Prefer", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, preferred_subject_codes: ["CIS 1101"] }, santos] });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [
    { subject_code: "CIS 1101", subject_title: "Intro", units: 3, subject_type: "LEC", is_active: true },
    { subject_code: "CIS 1102", subject_title: "Lab", units: 2, subject_type: "LAB", is_active: true },
  ] } as never);
  vi.mocked(api.updateFaculty).mockResolvedValue(rivera);
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: "Subjects for A. Rivera" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(await dialog.findByRole("checkbox", { name: "Prefer CIS 1101 Intro" })).toBeChecked();
  await userEvent.click(dialog.getByRole("checkbox", { name: "Not teaching CIS 1102 Lab" }));
  // A subject is at most one of the two: Not teaching replaces Prefer.
  await userEvent.click(dialog.getByRole("checkbox", { name: "Not teaching CIS 1101 Intro" }));
  expect(dialog.getByRole("checkbox", { name: "Prefer CIS 1101 Intro" })).not.toBeChecked();
  expect(dialog.getByText("0 preferred · 2 not teaching")).toBeInTheDocument();
  await userEvent.click(dialog.getByRole("button", { name: "Save subjects" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(rivera.faculty_id, {
    preferred_subject_codes: [],
    non_preferred_subject_codes: ["CIS 1101", "CIS 1102"],
    lecture_only: false,
  });
});

it("tags subjects mined from the active rule set and prefers them in one click", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, santos] });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [
    { subject_code: "CIS 1101", subject_title: "Intro", units: 3, subject_type: "LEC", is_active: true },
    { subject_code: "CIS 1102", subject_title: "Lab", units: 2, subject_type: "LAB", is_active: true },
  ] } as never);
  vi.mocked(api.listRuleSets).mockResolvedValue({ items: [{ rule_set_id: "rs-1", status: "active" }] } as never);
  vi.mocked(api.listRuleSetRules).mockResolvedValue({ items: [{ consequent: ["CIS 1101"] }] } as never);
  vi.mocked(api.updateFaculty).mockResolvedValue(rivera);
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: "Subjects for A. Rivera" }));
  const dialog = within(await screen.findByRole("dialog"));
  await userEvent.click(await dialog.findByRole("button", { name: "Prefer mined subjects (1)" }));
  expect(api.listRuleSetRules).toHaveBeenCalledWith("rs-1", expect.objectContaining({ patternType: "teacher_subject", facultyId: rivera.faculty_id }));
  expect(dialog.getByRole("checkbox", { name: "Prefer CIS 1101 Intro" })).toBeChecked();
  expect(dialog.getByRole("checkbox", { name: "Prefer CIS 1102 Lab" })).not.toBeChecked();
});

it("sets every lab to Not teaching when lecture classes only is ticked in the Subjects dialog", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, preferred_subject_codes: ["CIS 1102"] }, santos] });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [
    { subject_code: "CIS 1101", subject_title: "Intro", units: 3, subject_type: "LEC", is_active: true },
    { subject_code: "CIS 1102", subject_title: "Lab", units: 2, subject_type: "LAB", is_active: true },
  ] } as never);
  vi.mocked(api.updateFaculty).mockResolvedValue(rivera);
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: "Subjects for A. Rivera" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(await dialog.findByRole("checkbox", { name: "Prefer CIS 1102 Lab" })).toBeChecked();
  await userEvent.click(dialog.getByRole("checkbox", { name: /lecture classes only/i }));

  const labNever = dialog.getByRole("checkbox", { name: "Not teaching CIS 1102 Lab" });
  expect(labNever).toBeChecked();
  expect(labNever).toBeDisabled();
  expect(dialog.getByRole("checkbox", { name: "Prefer CIS 1102 Lab" })).not.toBeChecked();
  expect(dialog.getByRole("checkbox", { name: "Not teaching CIS 1101 Intro" })).toBeEnabled();
  await userEvent.click(dialog.getByRole("button", { name: "Save subjects" }));

  // The labs are implied by the flag, not stored as marks.
  expect(api.updateFaculty).toHaveBeenCalledWith(rivera.faculty_id, {
    preferred_subject_codes: [],
    non_preferred_subject_codes: [],
    lecture_only: true,
  });
});

it("shows only active faculty by default, only inactive or all on request, and labels them", async () => {
  const retired = { ...santos, faculty_id: "faculty-3", full_name: "C. Retired", is_active: false };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, retired] });

  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  expect(await screen.findByText("A. Rivera")).toBeInTheDocument();
  expect(api.listFaculty).toHaveBeenCalledWith(true);
  expect(screen.queryByText("C. Retired")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Inactive (1)" }));
  expect(screen.getByText("C. Retired")).toBeInTheDocument();
  const row = screen.getByText("C. Retired").closest("tr") as HTMLElement;
  expect(within(row).getByText("Inactive")).toHaveAttribute("title", "Not teaching this term; never scheduled");
  expect(within(row).getByRole("button", { name: "Activate C. Retired" })).toBeInTheDocument();
  expect(screen.queryByText("A. Rivera")).not.toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: "All" }));
  expect(screen.getByText("A. Rivera")).toBeInTheDocument();
  expect(screen.getByText("C. Retired")).toBeInTheDocument();
});

it("says when only hidden inactive faculty match a search", async () => {
  const retired = { ...santos, faculty_id: "faculty-3", full_name: "C. Retired", is_active: false };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, retired] });

  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");
  await userEvent.type(screen.getByPlaceholderText("Search faculty"), "Retired");

  expect(screen.getByText(/Only inactive faculty match/)).toBeInTheDocument();
});

it("keeps the dialog open and shows the backend message when a save is refused", async () => {
  vi.mocked(api.createFaculty).mockRejectedValue(
    new ApiError(409, "DUPLICATE_FACULTY", "That faculty member already exists."),
  );
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: /add faculty/i }));
  await userEvent.type(screen.getByLabelText("Full name"), "A. Rivera");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("That faculty member already exists.");
  expect(screen.getByLabelText("Full name")).toBeInTheDocument();
});

it("saves the canonical availability slots chosen in the grid", async () => {
  vi.mocked(api.getAvailability).mockResolvedValue({
    faculty_id: rivera.faculty_id,
    term_id: "term-1",
    slot_grid: slotGrid,
    time_slot_ids: ["slot-1"],
    reviewed: true,
  });
  vi.mocked(api.updateAvailability).mockResolvedValue({
    faculty_id: rivera.faculty_id,
    term_id: "term-1",
    slot_grid: slotGrid,
    time_slot_ids: ["slot-1", "slot-2"],
    reviewed: true,
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  const first = await screen.findByRole("button", { name: "08:00–09:00" });
  const second = screen.getByRole("button", { name: "09:00–10:00" });

  expect(api.getAvailability).toHaveBeenCalledWith(rivera.faculty_id);
  expect(first).toHaveAttribute("aria-pressed", "true");
  expect(second).toHaveAttribute("aria-pressed", "false");

  await userEvent.click(second);
  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", ["slot-1", "slot-2"], []);
  expect(screen.queryByRole("button", { name: "Save availability" })).not.toBeInTheDocument();
});

it("notes review state without asking anyone to review availability", async () => {
  activeTerm.current = { term_id: "term-2" };
  vi.mocked(api.listFaculty).mockResolvedValue({
    items: [{ ...rivera, availability_mask: "" }, { ...santos, availability_reviewed_term_id: "term-2" }],
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  const riveraCard = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  const santosCard = screen.getByText("B. Santos").closest("tr") as HTMLElement;
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
  expect(within(riveraCard).getAllByText("Every slot (default)").length).toBeGreaterThan(0);
  expect(within(santosCard).getAllByText("Reviewed").length).toBeGreaterThan(0);
});

it("confirms an unchanged availability for the new term and reloads the roster", async () => {
  activeTerm.current = { term_id: "term-2" };
  const stored = {
    faculty_id: rivera.faculty_id,
    term_id: "term-2",
    slot_grid: slotGrid,
    time_slot_ids: ["slot-1"],
    reviewed: false,
  };
  vi.mocked(api.getAvailability).mockResolvedValue(stored);
  vi.mocked(api.confirmAvailability).mockResolvedValue({ ...stored, reviewed: true });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  const second = await screen.findByRole("button", { name: "09:00–10:00" });
  const confirm = screen.getByRole("button", { name: "Confirm unchanged" });
  expect(screen.getByText(/Review is optional/)).toBeInTheDocument();

  // An edited selection has to be saved, not confirmed.
  await userEvent.click(second);
  expect(confirm).toBeDisabled();
  await userEvent.click(second);
  await userEvent.click(confirm);

  expect(api.confirmAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-2", ["slot-1"]);
  expect(api.updateAvailability).not.toHaveBeenCalled();
  expect(api.listFaculty).toHaveBeenCalledTimes(3);
});

function availabilityFor(termId: string, timeSlotIds: string[]) {
  return { faculty_id: rivera.faculty_id, term_id: termId, slot_grid: slotGrid, time_slot_ids: timeSlotIds, reviewed: false };
}

it("keeps a stale availability save open and shows the backend refusal", async () => {
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityFor("term-1", ["slot-1"]));
  vi.mocked(api.updateAvailability).mockRejectedValue(
    new ApiError(409, "AVAILABILITY_REVIEW_STALE", "The active term changed; reload and review it again."),
  );
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  await userEvent.click(await screen.findByRole("button", { name: "08:00–09:00" }));
  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", [], []);
  expect(await screen.findByRole("alert")).toHaveTextContent("The active term changed; reload and review it again.");
  expect(screen.getByRole("button", { name: "Save availability" })).toBeEnabled();
  expect(api.listFaculty).toHaveBeenCalledTimes(2);
});

it.each([
  ["before", false],
  ["after", true],
])("confirms only the new term's availability when the old read resolves %s the term change", async (_label, oldReadLate) => {
  activeTerm.current = { term_id: "term-2" };
  let resolveOld: (value: ReturnType<typeof availabilityFor>) => void = () => {};
  const oldRead = new Promise<ReturnType<typeof availabilityFor>>((resolve) => { resolveOld = resolve; });
  vi.mocked(api.getAvailability)
    .mockReturnValueOnce(oldRead)
    .mockResolvedValueOnce(availabilityFor("term-3", ["slot-2"]));
  vi.mocked(api.confirmAvailability).mockResolvedValue({ ...availabilityFor("term-3", ["slot-2"]), reviewed: true });
  const view = render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  if (!oldReadLate) await act(async () => resolveOld(availabilityFor("term-2", ["slot-1"])));

  activeTerm.current = { term_id: "term-3" };
  view.rerender(<MemoryRouter><RosterPage /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: "09:00–10:00" })).toHaveAttribute("aria-pressed", "true");
  if (oldReadLate) await act(async () => resolveOld(availabilityFor("term-2", ["slot-1"])));

  expect(api.getAvailability).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "08:00–09:00" })).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(screen.getByRole("button", { name: "Confirm unchanged" }));
  expect(api.confirmAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-3", ["slot-2"]);
});


it("treats never-saved availability as every slot and needs no review", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, availability_mask: "" }] });
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityFor("term-1", slotGrid.map((slot) => slot.time_slot_id)));
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));

  expect(await screen.findByText(/Available in every time slot by default/)).toBeInTheDocument();
  expect(screen.queryByText(/does not fit/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save availability" })).toBeEnabled();
});

it("requires a new selection when the stored availability is out of step with the term's slots", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, availability_mask: "101" }] });
  vi.mocked(api.getAvailability).mockResolvedValue({
    faculty_id: rivera.faculty_id,
    term_id: "term-1",
    slot_grid: slotGrid,
    time_slot_ids: [],
    reviewed: false,
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));

  expect(await screen.findByText(/does not fit the active term's time slots/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm unchanged" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save availability" })).toBeEnabled();
});

it("offers Confirm unchanged when the roster row is stale but the mask now fits", async () => {
  vi.mocked(api.listFaculty)
    .mockResolvedValueOnce({ items: [{ ...rivera, availability_mask: "" }] })
    .mockResolvedValue({ items: [rivera] });
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityFor("term-1", []));
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));

  expect(await screen.findByRole("button", { name: "Confirm unchanged" })).toBeEnabled();
});

it("shows only the no-slots message and blocks saving when the term has no time slots", async () => {
  vi.mocked(api.getAvailability).mockResolvedValue({ ...availabilityFor("term-1", []), slot_grid: [] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));

  expect(await screen.findByText("No time slots exist for the active term.")).toBeInTheDocument();
  expect(screen.queryByText(/save a new selection|Review is optional|every time slot by default/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm unchanged" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save availability" })).toBeDisabled();
});

it("opens the dialog from the grid without waiting for the roster re-read, then applies its mask", async () => {
  let resolveList: (value: { items: typeof rivera[] }) => void = () => {};
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityFor("term-1", ["slot-1"]));
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  vi.mocked(api.listFaculty).mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  const second = await screen.findByRole("button", { name: "09:00–10:00" });
  await userEvent.click(second);
  expect(second).toHaveAttribute("aria-pressed", "true");

  // Row mask "00" fits, so Confirm is offered while the re-read is pending.
  expect(screen.getByRole("button", { name: "Confirm unchanged" })).toBeInTheDocument();
  await act(async () => resolveList({ items: [{ ...rivera, availability_mask: "101" }] }));

  expect(screen.queryByRole("button", { name: "Confirm unchanged" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "09:00–10:00" })).toHaveAttribute("aria-pressed", "true");
});

it("shows a retryable load error instead of an empty roster and keeps records through a failed refresh", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listFaculty).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load faculty."));
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load faculty.");
  expect(screen.queryByText("No faculty records found.")).not.toBeInTheDocument();

  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera] });
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("A. Rivera")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  vi.mocked(api.updateFaculty).mockResolvedValue({ ...rivera, is_active: false });
  vi.mocked(api.listFaculty).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not load faculty."));
  await user.click(screen.getByRole("button", { name: "Deactivate A. Rivera" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load faculty.");
  expect(screen.getByText("A. Rivera")).toBeInTheDocument();
});

it("words a search with no matches as a search result, not as an empty roster", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await user.type(screen.getByPlaceholderText("Search faculty"), "zzz");
  expect(screen.getByText("No faculty match your search or filter.")).toBeInTheDocument();
  expect(screen.queryByText("No faculty records found.")).not.toBeInTheDocument();
});

it("filters the roster by full-time and part-time classification", async () => {
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");
  const group = within(screen.getByRole("group", { name: "Classification" }));

  await userEvent.click(group.getByRole("button", { name: "Full-time" }));
  expect(screen.queryByText("A. Rivera")).not.toBeInTheDocument();
  expect(screen.getByText("B. Santos")).toBeInTheDocument();

  await userEvent.click(group.getByRole("button", { name: "Part-time" }));
  expect(screen.getByText("A. Rivera")).toBeInTheDocument();
  expect(screen.queryByText("B. Santos")).not.toBeInTheDocument();

  await userEvent.click(group.getByRole("button", { name: "All" }));
  expect(screen.getByText("B. Santos")).toBeInTheDocument();
});

it("deactivates instead of deleting, only after confirmation", async () => {
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...rivera, is_active: false });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");
  expect(screen.queryByRole("button", { name: /remove|delete/i })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Deactivate A. Rivera" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(api.updateFaculty).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: "Deactivate A. Rivera" }));
  expect(screen.getByRole("dialog")).toHaveTextContent(/Nothing is deleted/);
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(rivera.faculty_id, { is_active: false });
  await waitFor(() => expect(api.listFaculty).toHaveBeenCalledTimes(2));
});

it("shows inactive faculty on request and activates them again", async () => {
  const retired = { ...santos, is_active: false };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, retired] });
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...retired, is_active: true });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("button", { name: "Inactive (1)" }));
  await userEvent.click(screen.getByRole("button", { name: "Activate B. Santos" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(santos.faculty_id, { is_active: true });
  await waitFor(() => expect(api.listFaculty).toHaveBeenCalledTimes(2));
});

const lecLab = [
  { time_slot_id: "lec-0730", label: "LEC 07:30-09:00", start_time: "07:30:00", end_time: "09:00:00", ordinal: 0 },
  { time_slot_id: "lec-0800", label: "LEC 08:00-09:30", start_time: "08:00:00", end_time: "09:30:00", ordinal: 1 },
  { time_slot_id: "lec-0830", label: "LEC 08:30-10:00", start_time: "08:30:00", end_time: "10:00:00", ordinal: 2 },
  { time_slot_id: "lab-0730", label: "LAB 07:30-10:00", start_time: "07:30:00", end_time: "10:00:00", ordinal: 3 },
];

async function openLecLab(timeSlotIds: string[], preferredDays: ("MON" | "TUE" | "WED" | "THU" | "FRI" | "SAT")[] = []) {
  vi.mocked(api.getAvailability).mockResolvedValue({
    faculty_id: rivera.faculty_id, term_id: "term-1", slot_grid: lecLab, time_slot_ids: timeSlotIds,
    reviewed: true, preferred_days: preferredDays,
  });
  vi.mocked(api.updateAvailability).mockResolvedValue({
    faculty_id: rivera.faculty_id, term_id: "term-1", slot_grid: lecLab, time_slot_ids: [], reviewed: true,
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  return within(await screen.findByRole("group", { name: "Morning windows" }));
}

it("shows one window per time range covering both the 90- and 150-minute slots", async () => {
  const morning = await openLecLab(["lec-0730"]);

  const rows = ["07:30–08:00", "08:00–08:30", "08:30–09:00", "09:00–09:30", "09:30–10:00"];
  expect(morning.getAllByRole("button").slice(1).map((button) => button.textContent)).toEqual(rows);
  expect(morning.getByRole("button", { name: "08:30–09:00" })).toHaveAttribute("aria-pressed", "true");
  expect(morning.getByRole("button", { name: "09:00–09:30" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText(/1 of 3 90-min windows · 0 of 1 150-min windows available/)).toBeInTheDocument();

  // Marking the rest of the stretch makes the lab and the later lecture fit.
  await userEvent.click(morning.getByRole("button", { name: "09:00–09:30" }));
  await userEvent.click(morning.getByRole("button", { name: "09:30–10:00" }));
  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", ["lec-0730", "lec-0800", "lec-0830", "lab-0730"], []);
});

it("marks a whole stretch with Shift+click and clears it with Clear", async () => {
  const morning = await openLecLab([]);
  const user = userEvent.setup();

  await user.click(morning.getByRole("button", { name: "07:30–08:00" }));
  await user.keyboard("{Shift>}");
  await user.click(morning.getByRole("button", { name: "09:00–09:30" }));
  await user.keyboard("{/Shift}");
  expect(morning.getByRole("button", { name: "08:30–09:00" })).toHaveAttribute("aria-pressed", "true");
  expect(morning.getByRole("button", { name: "09:30–10:00" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText(/2 of 3 90-min windows · 0 of 1 150-min windows/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Clear" }));
  expect(morning.getByRole("button", { name: "07:30–08:00" })).toHaveAttribute("aria-pressed", "false");
});

it("marks a stretch by dragging across the rows", async () => {
  const morning = await openLecLab([]);
  const user = userEvent.setup();
  const first = morning.getByRole("button", { name: "07:30–08:00" });
  const last = morning.getByRole("button", { name: "09:30–10:00" });

  await user.pointer([
    { keys: "[MouseLeft>]", target: first },
    { target: morning.getByRole("button", { name: "08:30–09:00" }) },
    { target: last },
    { keys: "[/MouseLeft]", target: last },
  ]);

  for (const button of morning.getAllByRole("button").slice(1)) {
    expect(button).toHaveAttribute("aria-pressed", "true");
  }
  expect(screen.getByText(/3 of 3 90-min windows · 1 of 1 150-min windows/)).toBeInTheDocument();
});

it("toggles a part of the day from its heading and walks rows with the keyboard", async () => {
  const morning = await openLecLab([]);
  const user = userEvent.setup();

  await user.click(morning.getByRole("button", { name: /Morning/ }));
  expect(morning.getByRole("button", { name: /Morning/ })).toHaveAttribute("aria-pressed", "true");
  expect(morning.getByRole("button", { name: "09:30–10:00" })).toHaveAttribute("aria-pressed", "true");

  morning.getByRole("button", { name: "07:30–08:00" }).focus();
  await user.keyboard(" ");
  expect(morning.getByRole("button", { name: "07:30–08:00" })).toHaveAttribute("aria-pressed", "false");
  await user.keyboard("{ArrowDown}");
  expect(morning.getByRole("button", { name: "08:00–08:30" })).toHaveFocus();
  await user.keyboard("{End}");
  expect(morning.getByRole("button", { name: "09:30–10:00" })).toHaveFocus();
});

it("saves preferred days with the availability without touching the time windows", async () => {
  await openLecLab(["lec-0730"], ["TUE", "THU"]);
  const user = userEvent.setup();

  expect(screen.getByRole("button", { name: "Tuesday" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Thursday" })).toHaveAttribute("aria-pressed", "true");
  // Each day toggles on its own: drop Thursday, add Monday and Saturday.
  await user.click(screen.getByRole("button", { name: "Thursday" }));
  await user.click(screen.getByRole("button", { name: "Saturday" }));
  await user.click(screen.getByRole("button", { name: "Monday" }));
  await user.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", ["lec-0730"], ["MON", "TUE", "SAT"]);
});

it("never widens a stored selection when only the preferred days change", async () => {
  // Lectures at 07:30 and 08:30 cover every row, but the lab spanning them was
  // deliberately left out; re-deriving slots from the rows would add it.
  await openLecLab(["lec-0730", "lec-0830"]);
  const user = userEvent.setup();

  // Monday alone is a valid preference.
  await user.click(screen.getByRole("button", { name: "Monday" }));
  await user.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", ["lec-0730", "lec-0830"], ["MON"]);
});

it("marks a typed From/To time range available in one step", async () => {
  const morning = await openLecLab([]);
  const user = userEvent.setup();
  const range = within(screen.getByRole("group", { name: "Time range" }));

  await user.selectOptions(range.getByLabelText("From"), "7:30 AM");
  await user.selectOptions(range.getByLabelText("To"), "9:00 AM");
  await user.click(range.getByRole("button", { name: "Mark available" }));

  expect(morning.getByRole("button", { name: "08:30–09:00" })).toHaveAttribute("aria-pressed", "true");
  expect(morning.getByRole("button", { name: "09:00–09:30" })).toHaveAttribute("aria-pressed", "false");

  // To only offers times after From.
  await user.selectOptions(range.getByLabelText("From"), "9:00 AM");
  expect(within(range.getByLabelText("To")).queryByRole("option", { name: "8:30 AM" })).toBeNull();
  expect(range.getByLabelText("To")).toHaveDisplayValue("10:00 AM");
  await user.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", ["lec-0730"], []);
});

it("offers every single day as a preferred day", async () => {
  await openLecLab([], ["TUE"]);
  const user = userEvent.setup();

  const choices = within(screen.getByRole("group", { name: "Preferred days" }));
  expect(choices.getAllByRole("button").map((button) => button.textContent)).toEqual(
    ["M", "T", "W", "Th", "F", "Sa", "Su", "No preference"],
  );
  // A single day is kept as saved and every other day stays selectable.
  expect(choices.getByRole("button", { name: "Tuesday" })).toHaveAttribute("aria-pressed", "true");
  expect(choices.getAllByRole("button").every((button) => !button.hasAttribute("disabled"))).toBe(true);

  await user.click(choices.getByRole("button", { name: "No preference" }));
  expect(choices.getByRole("button", { name: "Tuesday" })).toHaveAttribute("aria-pressed", "false");
  await user.click(screen.getByRole("button", { name: "Save availability" }));

  expect(api.updateAvailability).toHaveBeenCalledWith(rivera.faculty_id, "term-1", [], []);
});

it("lists preferred days on the roster row", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, preferred_days: ["THU", "MON", "TUE", "WED"] }] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  const row = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  // The wide-screen column, and the line folded under the name on a phone,
  // both in calendar order.
  expect(within(row).getByText("MTWTh")).toBeInTheDocument();
  expect(row).toHaveTextContent("Prefers MTWTh");
});

const subjectRows = [
  { subject_code: "CIS 1101", subject_title: "Programming 1", units: 3, subject_type: "LEC" as const, duration_minutes: 90 as const, is_active: true },
  { subject_code: "CIS 1102", subject_title: "Programming 1 Lab", units: 1, subject_type: "LAB" as const, duration_minutes: 150 as const, is_active: true },
  { subject_code: "CIS 2101", subject_title: "Data Structures", units: 3, subject_type: "LEC" as const, duration_minutes: 90 as const, is_active: true },
];

it("edits preferred subjects in a filterable checklist dialog", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [{ ...rivera, preferred_subject_codes: ["CIS 2101"] }, santos] });
  vi.mocked(api.listSubjects).mockResolvedValue({ items: subjectRows });
  vi.mocked(api.updateFaculty).mockResolvedValue({ ...rivera, preferred_subject_codes: ["CIS 1101"] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);

  const row = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  expect(row).toHaveTextContent("CIS 2101");
  await userEvent.click(within(row).getByRole("button", { name: "Subjects for A. Rivera" }));

  const dialog = within(await screen.findByRole("dialog", { name: /subjects/i }));
  expect(api.listSubjects).toHaveBeenCalledWith(true);
  expect(await dialog.findByRole("checkbox", { name: /prefer CIS 2101/i })).toBeChecked();

  // The type filter hides the lab; the text filter narrows by title.
  await userEvent.click(dialog.getByRole("button", { name: "Lecture" }));
  expect(dialog.queryByRole("checkbox", { name: /prefer CIS 1102/i })).not.toBeInTheDocument();
  await userEvent.type(dialog.getByRole("textbox", { name: "Filter subjects" }), "programming");
  expect(dialog.queryByRole("checkbox", { name: /prefer CIS 2101/i })).not.toBeInTheDocument();

  await userEvent.click(dialog.getByRole("checkbox", { name: /prefer CIS 1101/i }));
  await userEvent.clear(dialog.getByRole("textbox", { name: "Filter subjects" }));
  await userEvent.click(dialog.getByRole("checkbox", { name: /prefer CIS 2101/i }));
  expect(dialog.getByText("1 preferred · 0 not teaching")).toBeInTheDocument();

  await userEvent.click(dialog.getByRole("button", { name: "Save subjects" }));

  expect(api.updateFaculty).toHaveBeenCalledWith(rivera.faculty_id, { preferred_subject_codes: ["CIS 1101"], non_preferred_subject_codes: [], lecture_only: false });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("narrows the roster by preferred subject, preferred day and free time behind one Filters toggle", async () => {
  activeTerm.current = { term_id: "term-1" };
  vi.mocked(api.getMasterSchedule).mockResolvedValue(null as never);
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [] });
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [
    { ...slotGrid[0], term_id: "term-1" },
    { ...slotGrid[1], term_id: "term-1", start_time: "18:00:00", end_time: "19:30:00" },
  ] });
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [
    { ...rivera, preferred_subject_codes: ["CIS 1101"], preferred_days: ["SAT"], availability_mask: "10" },
    { ...santos, preferred_subject_codes: ["CIS 2101"], preferred_days: ["MON"], availability_mask: "" },
  ] });
  const user = userEvent.setup();
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  // Collapsed by default: the toolbar keeps its single row.
  expect(screen.queryByRole("combobox", { name: "Prefers subject" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /filters/i }));

  await user.selectOptions(screen.getByRole("combobox", { name: "Prefers subject" }), "CIS 2101");
  expect(screen.queryByText("A. Rivera")).not.toBeInTheDocument();
  expect(screen.getByText("B. Santos")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));

  await user.selectOptions(screen.getByRole("combobox", { name: "Prefers day" }), "SAT");
  expect(screen.getByText("A. Rivera")).toBeInTheDocument();
  expect(screen.queryByText("B. Santos")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));

  // Rivera is free only in the morning slot; an unset mask is open everywhere.
  await user.selectOptions(await screen.findByRole("combobox", { name: "Available time" }), "evening");
  expect(screen.queryByText("A. Rivera")).not.toBeInTheDocument();
  expect(screen.getByText("B. Santos")).toBeInTheDocument();
});


// --- Per-day times -----------------------------------------------------------

const availabilityOf = (extra: object = {}) => ({
  faculty_id: rivera.faculty_id,
  term_id: "term-1",
  slot_grid: slotGrid,
  time_slot_ids: ["slot-1", "slot-2"],
  reviewed: true,
  day_time_slot_ids: {},
  ...extra,
});

it("gives Monday its own shorter times and saves them beside the every-day times", async () => {
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityOf());
  vi.mocked(api.updateAvailability).mockResolvedValue(availabilityOf({ day_time_slot_ids: { MON: ["slot-1"] } }));
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  await screen.findByRole("button", { name: "08:00–09:00" });

  await userEvent.click(screen.getByRole("button", { name: "Monday times" }));
  expect(screen.getByText("Monday uses the every-day times.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "08:00–09:00" })).not.toBeInTheDocument();

  // Starts from the every-day times; clearing the 09:00 range shortens Monday only.
  await userEvent.click(screen.getByRole("button", { name: "Give Monday its own times" }));
  await userEvent.click(screen.getByRole("button", { name: "09:00–10:00" }));
  expect(screen.getByRole("button", { name: "Monday times (own)" })).toHaveAttribute("aria-pressed", "true");

  await userEvent.click(screen.getByRole("button", { name: "Every-day times" }));
  expect(screen.getByRole("button", { name: "09:00–10:00" })).toHaveAttribute("aria-pressed", "true");

  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));
  expect(api.updateAvailability).toHaveBeenCalledWith(
    rivera.faculty_id, "term-1", ["slot-1", "slot-2"], [], { MON: ["slot-1"] },
  );
});

it("keeps stored per-day times untouched on a save that never edits them, and can drop one", async () => {
  vi.mocked(api.getAvailability).mockResolvedValue(availabilityOf({ day_time_slot_ids: { FRI: ["slot-2"] } }));
  vi.mocked(api.updateAvailability).mockResolvedValue(availabilityOf());
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const card = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  await screen.findByRole("button", { name: "08:00–09:00" });

  expect(screen.getByText(/except Friday, which has its own times/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));
  expect(api.updateAvailability).toHaveBeenLastCalledWith(rivera.faculty_id, "term-1", ["slot-1", "slot-2"], []);

  await userEvent.click(within(card).getByRole("button", { name: /^availability for /i }));
  await screen.findByRole("button", { name: "08:00–09:00" });
  await userEvent.click(screen.getByRole("button", { name: "Friday times (own)" }));
  await userEvent.click(screen.getByRole("button", { name: "Use the every-day times on Friday" }));
  await userEvent.click(screen.getByRole("button", { name: "Save availability" }));
  expect(api.updateAvailability).toHaveBeenLastCalledWith(rivera.faculty_id, "term-1", ["slot-1", "slot-2"], [], {});
});

it("notes on the roster row which days have their own times", async () => {
  vi.mocked(api.listFaculty).mockResolvedValue({
    items: [{ ...rivera, day_availability_masks: "MON:10;FRI:01" }, santos],
  });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  const row = (await screen.findByText("A. Rivera")).closest("tr") as HTMLElement;
  expect(row).toHaveTextContent("own MF hours");
});

// --- Bulk status -------------------------------------------------------------

it("deactivates every ticked member at once, only after confirmation", async () => {
  vi.mocked(api.setFacultyStatus).mockResolvedValue({ items: [] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");

  await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown faculty" }));
  const bar = screen.getByRole("region", { name: "Selected faculty" });
  expect(bar).toHaveTextContent("2 selected");
  expect(within(bar).getByRole("button", { name: /^Activate/ })).toBeDisabled();

  await userEvent.click(within(bar).getByRole("button", { name: "Deactivate (2)" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveTextContent("Deactivate 2 faculty members?");
  expect(dialog).toHaveTextContent("A. Rivera, B. Santos");
  await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));

  expect(api.setFacultyStatus).toHaveBeenCalledWith(["faculty-1", "faculty-2"], false);
  await waitFor(() => expect(api.listFaculty).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("region", { name: "Selected faculty" })).not.toBeInTheDocument();
});

it("activates only the inactive members among those ticked", async () => {
  const retired = { ...santos, is_active: false };
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [rivera, retired] });
  vi.mocked(api.setFacultyStatus).mockResolvedValue({ items: [] });
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");
  await userEvent.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: "All" }));

  await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown faculty" }));
  const bar = screen.getByRole("region", { name: "Selected faculty" });
  await userEvent.click(within(bar).getByRole("button", { name: "Activate (1)" }));

  expect(api.setFacultyStatus).toHaveBeenCalledWith(["faculty-2"], true);
  await waitFor(() => expect(api.listFaculty).toHaveBeenCalledTimes(2));
});

it("never acts on a ticked member the filters now hide", async () => {
  render(<MemoryRouter><RosterPage /></MemoryRouter>);
  await screen.findByText("A. Rivera");
  await userEvent.click(screen.getByRole("checkbox", { name: "Select A. Rivera" }));
  expect(screen.getByRole("region", { name: "Selected faculty" })).toHaveTextContent("1 selected");

  await userEvent.type(screen.getByPlaceholderText("Search faculty"), "Santos");
  expect(screen.queryByRole("region", { name: "Selected faculty" })).not.toBeInTheDocument();
  expect(api.setFacultyStatus).not.toHaveBeenCalled();
});
