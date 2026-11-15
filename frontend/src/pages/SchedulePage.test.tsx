import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, onTestFinished, vi } from "vitest";
import { ApiError, api, type Subject } from "../api/client";
import { publishTermChanged, subscribeTermChanged } from "../state/appEvents";
import SchedulePage from "./SchedulePage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listTerms: vi.fn(),
      getActiveTerm: vi.fn(),
      selectTerm: vi.fn(),
      archiveTerm: vi.fn(),
      restoreTerm: vi.fn(),
      createTerm: vi.fn(),
      listSubjects: vi.fn(),
      updateTerm: vi.fn(),
      generateCanonicalSlots: vi.fn(),
      createTimeSlot: vi.fn(),
      updateTimeSlot: vi.fn(),
      listCoursePrograms: vi.fn(),
      createCourseProgram: vi.fn(),
      updateCourseProgram: vi.fn(),
      deactivateCourseProgram: vi.fn(),
      listFaculty: vi.fn(),
      listBlocks: vi.fn(),
      createBlock: vi.fn(),
      updateBlock: vi.fn(),
      deleteBlock: vi.fn(),
      listBlockClasses: vi.fn(),
      listOfferings: vi.fn(),
      createOffering: vi.fn(),
      deleteOffering: vi.fn(),
      addBlockClass: vi.fn(),
      generateCohort: vi.fn(),
      listTimeSlots: vi.fn(),
      getReadiness: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const activeTerm = { term_id: "term-1", academic_year: "2026-2027", term_number: 1, is_active: true };
const dormantTerm = { term_id: "term-2", academic_year: "2027-2028", term_number: 2, is_active: false };
const subject: Subject = {
  subject_code: "CS101",
  subject_title: "Intro to Computing",
  units: 3,
  subject_type: "LEC",
  duration_minutes: 90,
};
const labSubject: Subject = {
  subject_code: "CS220",
  subject_title: "Networks Laboratory",
  units: 5,
  subject_type: "LAB",
  duration_minutes: 150,
};
const program = { program_code: "BSCS", program_name: "BS Computer Science" };
const faculty = { faculty_id: "faculty-1", full_name: "A. Rivera", is_full_time: false, availability_mask: "00", availability_reviewed_term_id: null, is_active: true };

function panelFor(title: string) {
  return screen.getByRole("heading", { name: title }).closest("section") as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, dormantTerm] });
  vi.mocked(api.getActiveTerm).mockResolvedValue(activeTerm);
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [subject, labSubject] });
  vi.mocked(api.listCoursePrograms).mockResolvedValue({ items: [program] });
  vi.mocked(api.listFaculty).mockResolvedValue({ items: [faculty] });
  vi.mocked(api.listBlocks).mockResolvedValue({
    items: [{ block_id: "block-1", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1, section_letter: "A", is_expanded: false, name: "BSCS-1A", capacity: 20 }],
  });
  vi.mocked(api.listBlockClasses).mockResolvedValue({
    items: [{ class_id: "class-1", block_id: "block-1", offering_id: "offering-1", term_id: activeTerm.term_id, subject_code: "CS101" }],
  });
  vi.mocked(api.listOfferings).mockResolvedValue({
    items: [{ offering_id: "offering-1", term_id: activeTerm.term_id, subject_code: "CS101", group_no: 1, group_label: "CS101 G1" }],
  });
  vi.mocked(api.listTimeSlots).mockResolvedValue({
    items: [{ time_slot_id: "slot-1", term_id: activeTerm.term_id, label: "MWF 08:00", start_time: "08:00:00", end_time: "09:30:00", ordinal: 0 }],
  });
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] });
});

afterEach(() => vi.restoreAllMocks());

/** A Block's table row; its accessible name starts with the derived Block name. */
function blockRow(name: string) {
  return screen.getByRole("row", { name: new RegExp(`^${name} `) });
}
function queryBlockRow(name: string) {
  return screen.queryByRole("row", { name: new RegExp(`^${name} `) });
}
function findBlockRow(name: string) {
  return screen.findByRole("row", { name: new RegExp(`^${name} `) });
}

it("loads every term-scoped input for the active term", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "Ready for generation" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Master Schedule" })).toHaveAttribute("href", "/master-schedule");
  for (const call of [api.listBlocks, api.listTimeSlots, api.getReadiness]) {
    expect(call).toHaveBeenCalledWith(activeTerm.term_id);
  }

  expect(within(panelFor("Course Programs")).getByText("BSCS")).toBeInTheDocument();
  expect(within(panelFor("Time slots")).getByText("MWF 08:00")).toBeInTheDocument();
  expect(within(panelFor("Block sections")).getByText("BSCS-1A")).toBeInTheDocument();
  expect(within(panelFor("Block sections")).getByText("20 seats")).toBeInTheDocument();
});

it("summarizes the configured calendar and generated slots read-only without manual slot controls", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Time slots" });

  const panel = panelFor("Time slots");
  expect(within(panel).getByText("Configured Calendar (Read-Only)")).toBeInTheDocument();
  expect(within(panel).getByText("Monday – Sunday")).toBeInTheDocument();
  expect(within(panel).getByText("07:30 – 21:00 (30-min intervals)")).toBeInTheDocument();
  expect(within(panel).getByText("1 slots generated")).toBeInTheDocument();
  expect(within(panel).getByText("MWF 08:00")).toBeInTheDocument();
  expect(within(panel).getByText("Ordinal 0")).toBeInTheDocument();

  // No manual inputs or edit/delete actions exist
  expect(within(panel).queryByLabelText("Slot label")).not.toBeInTheDocument();
  expect(within(panel).queryByLabelText("Start time")).not.toBeInTheDocument();
  expect(within(panel).queryByLabelText("End time")).not.toBeInTheDocument();
  expect(within(panel).queryByLabelText("Order in day")).not.toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Add slot" })).not.toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
});

it("renders readiness blockers reported by the backend", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: activeTerm.term_id,
    ready: false,
    blockers: [{ code: "INSUFFICIENT_TEACHING_CAPACITY", message: "Faculty capacity is insufficient for the offered units.", details: {} }],
    warnings: [],
  });

  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "Setup incomplete" })).toBeInTheDocument();
  expect(screen.getByText("Faculty capacity is insufficient for the offered units.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument();
});

it("hides the Master Schedule link after a failed refresh until a read succeeds, but not after a failed save", async () => {
  const ready = { term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] };
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.createCourseProgram).mockRejectedValue(new ApiError(409, "DUPLICATE_RESOURCE", "That program code already exists."));
  const programs = within(panelFor("Course Programs"));
  await userEvent.type(programs.getByLabelText("Course Program code"), "BSCS");
  await userEvent.type(programs.getByLabelText("Course Program name"), "BS Computer Science");
  await userEvent.click(programs.getByRole("button", { name: /add/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That program code already exists.");
  expect(screen.getByRole("link", { name: "Go to Master Schedule" })).toBeInTheDocument();

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument());

  vi.mocked(api.getReadiness).mockResolvedValue(ready);
  fireEvent(window, new Event("focus"));
  expect(await screen.findByRole("link", { name: "Go to Master Schedule" })).toBeInTheDocument();
});

it("skips term-scoped requests and asks for a term when none is active", async () => {
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(409, "NO_ACTIVE_TERM", "No active term is selected."),
  );

  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(await screen.findByText(/Select an active term before adding term-scoped scheduling inputs\./)).toBeInTheDocument();
  expect(api.listBlocks).not.toHaveBeenCalled();
  expect(api.getReadiness).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  // Terms remain manageable so the operator can recover from this state.
  expect(screen.getByRole("heading", { name: "Academic terms" })).toBeInTheDocument();
});

it("groups captured terms as read-only history and keeps them out of the pickers", async () => {
  const captured = { term_id: "term-9", academic_year: "2019-2020", term_number: 1, is_active: false, is_captured: true };
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, dormantTerm, captured] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });

  const panel = panelFor("Academic terms");
  const group = within(panel).getByText("Historical terms (1)").closest("details") as HTMLElement;
  expect(group).not.toHaveAttribute("open");
  expect(within(group).getByText("Term 1, AY 2019-2020")).toBeInTheDocument();
  expect(within(group).queryByRole("button")).not.toBeInTheDocument();
  const years = within(panel).getByLabelText("Academic year");
  expect(within(years).getAllByRole("option").map((option) => option.textContent)).toEqual(["AY 2027-2028", "AY 2026-2027"]);
});

it("picks a term with an academic-year dropdown and a term dropdown", async () => {
  const third = { term_id: "term-3", academic_year: "2027-2028", term_number: 1, is_active: false };
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, dormantTerm, third] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  const panel = within(panelFor("Academic terms"));

  // Starts on the active term, which cannot be set active again or archived.
  expect(panel.getByLabelText("Academic year")).toHaveValue("2026-2027");
  expect(panel.getByLabelText("Term")).toHaveValue(activeTerm.term_id);
  expect(panel.getByRole("button", { name: "Active" })).toBeDisabled();
  expect(panel.getByRole("button", { name: "Archive Term 1, AY 2026-2027" })).toBeDisabled();
  expect(panel.getByText(/Active term:/)).toHaveTextContent("Active term: Term 1, AY 2026-2027");

  await userEvent.selectOptions(panel.getByLabelText("Academic year"), "2027-2028");
  const terms = panel.getByLabelText("Term");
  expect(within(terms).getAllByRole("option").map((option) => option.textContent)).toEqual(["Term 1", "Term 2"]);
  expect(terms).toHaveValue(third.term_id);
  await userEvent.selectOptions(terms, dormantTerm.term_id);
  expect(panel.getByRole("button", { name: "Set active" })).toBeEnabled();
});

it("shows older academic years disabled and cannot set them active", async () => {
  const older = { term_id: "term-old", academic_year: "2025-2026", term_number: 2, is_active: false };
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, older] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  const years = within(panelFor("Academic terms")).getByLabelText("Academic year");

  expect(within(years).getByRole("option", { name: "AY 2025-2026 (older, read-only)" })).toBeDisabled();
  expect(within(years).getByRole("option", { name: "AY 2026-2027" })).toBeEnabled();
});

it("refuses to show the setup of an active term from before AY 2026-2027", async () => {
  const older = { term_id: "term-old", academic_year: "2025-2026", term_number: 2, is_active: true };
  vi.mocked(api.listTerms).mockResolvedValue({ items: [older, { ...dormantTerm }] });
  vi.mocked(api.getActiveTerm).mockResolvedValue(older);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(await screen.findByText("Unable to display older terms before AY 2026-2027")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Generate from curriculum" })).not.toBeInTheDocument();
  expect(api.listBlocks).not.toHaveBeenCalled();
  expect(api.getReadiness).not.toHaveBeenCalled();
  // The way out stays available: a current term can still be set active.
  const panel = within(panelFor("Academic terms"));
  expect(panel.getByLabelText("Academic year")).toHaveValue("2025-2026");
  await userEvent.selectOptions(panel.getByLabelText("Academic year"), "2027-2028");
  expect(panel.getByRole("button", { name: "Set active" })).toBeEnabled();
});

it("keeps internal ticket references out of the setup copy", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  expect(document.body.textContent).not.toMatch(/CHR-\d+/);
});

it("announces a term change so every active-term consumer reloads", async () => {
  const listener = vi.fn();
  const unsubscribe = subscribeTermChanged(listener);
  vi.mocked(api.selectTerm).mockResolvedValue({ ...dormantTerm, is_active: true });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });

  const panel = within(panelFor("Academic terms"));
  await userEvent.selectOptions(panel.getByLabelText("Academic year"), "2027-2028");
  await userEvent.click(panel.getByRole("button", { name: "Set active" }));

  expect(api.selectTerm).toHaveBeenCalledWith(dormantTerm.term_id);
  expect(listener).toHaveBeenCalledOnce();
  expect(api.listTerms).toHaveBeenCalledTimes(2);
  unsubscribe();
});

it("hands subject records to the dedicated catalog page", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });

  // Term Setup still consumes the catalog for offerings, but no longer
  // edits it: CHR-SUB-005 moved that to its own page.
  expect(screen.queryByRole("heading", { name: "Subjects" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Subject code")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /subjects catalog/i })).toHaveAttribute("href", "/subjects");
  expect(api.listSubjects).toHaveBeenCalled();
  await userEvent.click(within(panelFor("Subject offerings")).getByRole("combobox", { name: "Subject" }));
  expect(screen.getByRole("option", { name: /CS101/ })).toBeInTheDocument();
});

it("finds a subject by title and shows its catalog units read-only", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Subject offerings" });
  const offerings = within(panelFor("Subject offerings"));

  const picker = offerings.getByRole("combobox", { name: "Subject" });
  await userEvent.type(picker, "intro to");
  await userEvent.click(offerings.getByRole("option", { name: /CS101/ }));

  expect(picker).toHaveValue("CS101 — Intro to Computing");
  expect(offerings.getByLabelText("Units")).toHaveTextContent("3 units");
  // Units are read from the catalog record, never typed next to it.
  expect(offerings.queryByRole("spinbutton", { name: "Units" })).not.toBeInTheDocument();
});

it("re-derives the displayed units when the selected subject changes", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Subject offerings" });
  const offerings = within(panelFor("Subject offerings"));
  const picker = offerings.getByRole("combobox", { name: "Subject" });

  await userEvent.click(picker);
  await userEvent.click(offerings.getByRole("option", { name: /CS101/ }));
  expect(offerings.getByLabelText("Units")).toHaveTextContent("3 units");

  await userEvent.click(picker);
  await userEvent.click(offerings.getByRole("option", { name: /CS220/ }));

  expect(offerings.getByLabelText("Units")).toHaveTextContent("5 units");
});

it("creates an offering from subject identity alone", async () => {
  vi.mocked(api.createOffering).mockResolvedValue({
    offering_id: "offering-2", term_id: activeTerm.term_id, subject_code: "CS220", group_no: 1, group_label: "CS220 G1",
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Subject offerings" });
  const offerings = within(panelFor("Subject offerings"));

  await userEvent.click(offerings.getByRole("combobox", { name: "Subject" }));
  await userEvent.click(offerings.getByRole("option", { name: /CS220/ }));
  await userEvent.click(offerings.getByRole("button", { name: /add offering/i }));

  expect(api.createOffering).toHaveBeenCalledWith(activeTerm.term_id, "CS220");
  expect(api.listOfferings).toHaveBeenCalledTimes(2);
});

it("refuses to submit an offering until a catalog record is chosen", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Subject offerings" });
  const offerings = within(panelFor("Subject offerings"));

  // A code typed without picking a record is not an identity the form accepts.
  await userEvent.type(offerings.getByRole("combobox", { name: "Subject" }), "CS999");

  expect(offerings.getByRole("button", { name: /add offering/i })).toBeDisabled();
  expect(offerings.getByLabelText("Units")).toHaveTextContent("Select a subject");
});

it("picks a Block's offering by its server-spelled group label", async () => {
  vi.mocked(api.addBlockClass).mockResolvedValue({
    class_id: "class-2", block_id: "block-1", offering_id: "offering-1", term_id: activeTerm.term_id, subject_code: "CS101",
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block subjects" });
  const blockSubjects = within(panelFor("Block subjects"));

  await userEvent.type(blockSubjects.getByRole("combobox", { name: "Block" }), "1a");
  await userEvent.click(blockSubjects.getByRole("option", { name: "BSCS-1A" }));
  // Title initials find the offering: "itc" is "Intro to Computing".
  await userEvent.type(blockSubjects.getByRole("combobox", { name: "Subject offering" }), "itc");
  expect(blockSubjects.getAllByRole("option")).toHaveLength(1);
  await userEvent.click(blockSubjects.getByRole("option", { name: /CS101 G1/ }));
  await userEvent.click(blockSubjects.getByRole("button", { name: /add class/i }));

  expect(api.addBlockClass).toHaveBeenCalledWith("block-1", "offering-1");
});

it("does not announce a term change for a catalog edit", async () => {
  const listener = vi.fn();
  const unsubscribe = subscribeTermChanged(listener);
  vi.mocked(api.createCourseProgram).mockResolvedValue({ program_code: "BSIT", program_name: "BS Information Technology" });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });
  const programs = within(panelFor("Course Programs"));

  await userEvent.type(programs.getByLabelText("Course Program code"), "BSIT");
  await userEvent.type(programs.getByLabelText("Course Program name"), "BS Information Technology");
  await userEvent.click(programs.getByRole("button", { name: /add/i }));

  expect(api.createCourseProgram).toHaveBeenCalledWith({
    program_code: "BSIT",
    program_name: "BS Information Technology",
  });
  expect(listener).not.toHaveBeenCalled();
  unsubscribe();
});

it("surfaces a refused mutation without dropping the loaded setup", async () => {
  vi.mocked(api.createCourseProgram).mockRejectedValue(
    new ApiError(409, "DUPLICATE_RESOURCE", "That program code already exists."),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });
  const programs = within(panelFor("Course Programs"));

  await userEvent.type(programs.getByLabelText("Course Program code"), "BSCS");
  await userEvent.type(programs.getByLabelText("Course Program name"), "BS Computer Science");
  await userEvent.click(programs.getByRole("button", { name: /add/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("That program code already exists.");
  expect(screen.getByRole("heading", { name: "Course Programs" })).toBeInTheDocument();
});

it("deactivates a Course Program instead of deleting it and can activate it again", async () => {
  const retired = { program_code: "BSIT", program_name: "BS Information Technology", is_active: false };
  vi.mocked(api.listCoursePrograms).mockResolvedValue({ items: [program, retired] });
  vi.mocked(api.deactivateCourseProgram).mockResolvedValue(undefined);
  vi.mocked(api.updateCourseProgram).mockResolvedValue({ ...retired, is_active: true });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });
  const programs = within(panelFor("Course Programs"));

  expect(api.listCoursePrograms).toHaveBeenCalledWith(true);
  // Hidden by default, and never offered to the Block pickers.
  expect(programs.queryByText("BSIT")).not.toBeInTheDocument();
  expect(within(panelFor("Generate from curriculum")).queryByRole("option", { name: "BSIT" })).not.toBeInTheDocument();
  expect(programs.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();

  await userEvent.click(programs.getByLabelText("Show inactive (1)"));
  expect(programs.getByText("Inactive")).toBeInTheDocument();
  await userEvent.click(programs.getByRole("button", { name: "Activate" }));
  expect(api.updateCourseProgram).toHaveBeenCalledWith("BSIT", { is_active: true });

  await userEvent.click(programs.getByRole("button", { name: "Deactivate" }));
  expect(await screen.findByText(/nothing is deleted/)).toBeInTheDocument();
});

it("offers no way to delete a term, only to archive one", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });

  expect(within(panelFor("Academic terms")).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
});

it("confirms before archiving a term and refreshes the list", async () => {
  vi.mocked(api.archiveTerm).mockResolvedValue({ ...dormantTerm, is_archived: true });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  const panel = within(panelFor("Academic terms"));
  await userEvent.selectOptions(panel.getByLabelText("Academic year"), "2027-2028");
  const reads = vi.mocked(api.listTerms).mock.calls.length;

  await userEvent.click(panel.getByRole("button", { name: "Archive Term 2, AY 2027-2028" }));

  expect(api.archiveTerm).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog", { name: "Archive Term 2, AY 2027-2028?" });
  expect(dialog).toHaveTextContent("stay on file");
  await userEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
  expect(api.archiveTerm).toHaveBeenCalledWith(dormantTerm.term_id);
  await waitFor(() => expect(vi.mocked(api.listTerms).mock.calls.length).toBeGreaterThan(reads));
  expect(api.listTerms).toHaveBeenCalledWith({ includeArchived: true });
});

it("lists archived terms apart from the pickers and restores one", async () => {
  const archived = { ...dormantTerm, is_archived: true };
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, archived] });
  vi.mocked(api.restoreTerm).mockResolvedValue(dormantTerm);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  const panel = within(panelFor("Academic terms"));

  expect(within(panel.getByLabelText("Academic year")).getAllByRole("option")).toHaveLength(1);
  const group = within(panel.getByText("Archived terms (1)").closest("details") as HTMLElement);
  await userEvent.click(group.getByRole("button", { name: "Restore Term 2, AY 2027-2028" }));

  expect(api.restoreTerm).toHaveBeenCalledWith(dormantTerm.term_id);
});

async function openAddTerm() {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });
  await userEvent.click(within(panelFor("Academic terms")).getByRole("button", { name: "Add term" }));
  return within(screen.getByRole("dialog", { name: "Add term" }));
}

it("adds a term from a dialog and preselects it", async () => {
  const created = { term_id: "term-new", academic_year: "2028-2029", term_number: 2, is_active: false };
  vi.mocked(api.createTerm).mockResolvedValue(created);
  const dialog = await openAddTerm();

  await userEvent.type(dialog.getByLabelText("Academic year"), "2028-2029");
  await userEvent.selectOptions(dialog.getByLabelText("Term"), "2");
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, dormantTerm, created] });
  await userEvent.click(dialog.getByRole("button", { name: "Add term" }));

  expect(api.createTerm).toHaveBeenCalledWith({ academic_year: "2028-2029", term_number: 2 });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const panel = within(panelFor("Academic terms"));
  await waitFor(() => expect(panel.getByLabelText("Term")).toHaveValue(created.term_id));
  expect(panel.getByLabelText("Academic year")).toHaveValue("2028-2029");
});

it.each([
  ["2028", "Use the format YYYY-YYYY"],
  ["2028-2030", "The second year must follow the first"],
  ["2025-2026", "Terms before AY 2026-2027 are already done"],
  ["2026-2027", "Term 1 of AY 2026-2027 already exists."],
])("refuses %s in the add-term dialog without calling the server", async (year, message) => {
  const dialog = await openAddTerm();

  await userEvent.type(dialog.getByLabelText("Academic year"), year);
  await userEvent.click(dialog.getByRole("button", { name: "Add term" }));

  expect(dialog.getByRole("alert")).toHaveTextContent(message);
  expect(dialog.getByLabelText("Academic year")).toHaveAttribute("aria-invalid", "true");
  expect(dialog.getByRole("button", { name: "Add term" })).toBeDisabled();
  expect(api.createTerm).not.toHaveBeenCalled();
});

it("points to Restore when the new term duplicates an archived one", async () => {
  vi.mocked(api.listTerms).mockResolvedValue({ items: [activeTerm, { ...dormantTerm, is_archived: true }] });
  const dialog = await openAddTerm();

  await userEvent.type(dialog.getByLabelText("Academic year"), "2027-2028");
  await userEvent.selectOptions(dialog.getByLabelText("Term"), "2");
  await userEvent.click(dialog.getByRole("button", { name: "Add term" }));

  expect(dialog.getByRole("alert")).toHaveTextContent("is archived. Restore it from Archived terms.");
});

it("keeps the add-term dialog open with the server's refusal and blocks a double submit", async () => {
  let reject: (error: unknown) => void = () => {};
  vi.mocked(api.createTerm).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
  const dialog = await openAddTerm();

  await userEvent.type(dialog.getByLabelText("Academic year"), "2029-2030");
  await userEvent.click(dialog.getByRole("button", { name: "Add term" }));
  expect(dialog.getByRole("button", { name: "Adding..." })).toBeDisabled();
  await act(async () => reject(new ApiError(409, "DUPLICATE_RESOURCE", "Term 1 of AY 2029-2030 already exists.")));

  expect(await dialog.findByRole("alert")).toHaveTextContent("Term 1 of AY 2029-2030 already exists.");
  expect(api.createTerm).toHaveBeenCalledOnce();
  expect(screen.getByRole("dialog", { name: "Add term" })).toBeInTheDocument();
});

it("leaves a page error and the data alone when a delete is cancelled, and refetches after a real delete", async () => {
  vi.mocked(api.createCourseProgram).mockRejectedValue(
    new ApiError(409, "DUPLICATE_RESOURCE", "That program code already exists."),
  );
  vi.mocked(api.deleteBlock).mockResolvedValue(undefined);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const programs = within(panelFor("Course Programs"));
  await userEvent.type(programs.getByLabelText("Course Program code"), "BSCS");
  await userEvent.type(programs.getByLabelText("Course Program name"), "BS Computer Science");
  await userEvent.click(programs.getByRole("button", { name: /add/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That program code already exists.");
  const block = () => within(screen.getByRole("row", { name: /BSCS-1A/ }));

  const reads = vi.mocked(api.listTerms).mock.calls.length;
  await userEvent.click(block().getByRole("button", { name: "Delete BSCS-1A" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("alert")).toHaveTextContent("That program code already exists.");
  expect(api.listTerms).toHaveBeenCalledTimes(reads);

  await userEvent.click(block().getByRole("button", { name: "Delete BSCS-1A" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(vi.mocked(api.listTerms).mock.calls.length).toBeGreaterThan(reads));
});

it("edits a catalog record through one form instead of a chain of prompts", async () => {
  const prompt = vi.spyOn(window, "prompt");
  vi.mocked(api.updateCourseProgram).mockResolvedValue({ ...program, program_name: "BS Computer Studies" });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });

  await userEvent.click(within(panelFor("Course Programs")).getByRole("button", { name: "Edit" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getByLabelText("Course Program name")).toHaveValue("BS Computer Science");

  await userEvent.clear(dialog.getByLabelText("Course Program name"));
  await userEvent.type(dialog.getByLabelText("Course Program name"), "BS Computer Studies");
  await userEvent.click(dialog.getByRole("button", { name: "Save changes" }));

  expect(api.updateCourseProgram).toHaveBeenCalledWith("BSCS", { program_name: "BS Computer Studies" });
  expect(prompt).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.listCoursePrograms).toHaveBeenCalledTimes(2);
});

it("provides an actionable state to generate slots when missing and refreshes readiness", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValueOnce({ items: [] });
  vi.mocked(api.getReadiness).mockResolvedValueOnce({
    term_id: activeTerm.term_id,
    ready: false,
    blockers: [{ code: "NO_TIME_SLOTS", message: "The term has no time slots.", details: {} }],
    warnings: [],
  });

  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Time slots" });

  expect(screen.getByText("No time slots generated for the active term.")).toBeInTheDocument();
  expect(screen.getByText("The term has no time slots.")).toBeInTheDocument();

  const generateBtn = screen.getByRole("button", { name: "Generate canonical slots" });
  expect(generateBtn).toBeInTheDocument();

  vi.mocked(api.generateCanonicalSlots).mockResolvedValue({
    items: [{
      time_slot_id: "slot-canonical-1",
      term_id: activeTerm.term_id,
      label: "LEC 07:30-09:00",
      start_time: "07:30:00",
      end_time: "09:00:00",
      ordinal: 0,
    }],
  });
  vi.mocked(api.listTimeSlots).mockResolvedValueOnce({
    items: [{
      time_slot_id: "slot-canonical-1",
      term_id: activeTerm.term_id,
      label: "LEC 07:30-09:00",
      start_time: "07:30:00",
      end_time: "09:00:00",
      ordinal: 0,
    }],
  });
  vi.mocked(api.getReadiness).mockResolvedValueOnce({
    term_id: activeTerm.term_id,
    ready: true,
    blockers: [],
    warnings: [],
  });

  await userEvent.click(generateBtn);

  expect(api.generateCanonicalSlots).toHaveBeenCalledWith(activeTerm.term_id);
  expect(await screen.findByRole("heading", { name: "Ready for generation" })).toBeInTheDocument();
  expect(await screen.findByText("LEC 07:30-09:00")).toBeInTheDocument();
});

it("displays an actionable retry state when slot generation fails", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValueOnce({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockRejectedValue(
    new ApiError(500, "GENERATION_FAILED", "Failed to materialize calendar slots."),
  );

  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Time slots" });

  const generateBtn = screen.getByRole("button", { name: "Generate canonical slots" });
  await userEvent.click(generateBtn);

  expect(await screen.findByRole("alert")).toHaveTextContent("Failed to materialize calendar slots.");
  const retryBtn = within(screen.getByRole("alert")).getByRole("button", { name: "Retry" });
  expect(retryBtn).toBeInTheDocument();

  vi.mocked(api.generateCanonicalSlots).mockResolvedValueOnce({ items: [] });
  await userEvent.click(retryBtn);
  expect(api.generateCanonicalSlots).toHaveBeenCalledTimes(2);
});

it("abandoning an edit leaves the record untouched", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });

  await userEvent.click(within(panelFor("Course Programs")).getByRole("button", { name: "Edit" }));
  const dialog = within(await screen.findByRole("dialog"));
  await userEvent.clear(dialog.getByLabelText("Course Program name"));
  await userEvent.type(dialog.getByLabelText("Course Program name"), "Half-entered name");
  await userEvent.keyboard("{Escape}");

  expect(api.updateCourseProgram).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps a refused edit open with its reason instead of emptying the form", async () => {
  vi.mocked(api.updateCourseProgram).mockRejectedValue(
    new ApiError(422, "VALIDATION_ERROR", "The program name must not be blank."),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Course Programs" });

  await userEvent.click(within(panelFor("Course Programs")).getByRole("button", { name: "Edit" }));
  const dialog = within(await screen.findByRole("dialog"));
  await userEvent.clear(dialog.getByLabelText("Course Program name"));
  await userEvent.type(dialog.getByLabelText("Course Program name"), "Renamed program");
  await userEvent.click(dialog.getByRole("button", { name: "Save changes" }));

  expect(await dialog.findByRole("alert")).toHaveTextContent("The program name must not be blank.");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(dialog.getByLabelText("Course Program name")).toHaveValue("Renamed program");
});

it("announces a term change after the term editor saves", async () => {
  const listener = vi.fn();
  const unsubscribe = subscribeTermChanged(listener);
  vi.mocked(api.updateTerm).mockResolvedValue({ ...activeTerm, term_number: 2 });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Academic terms" });

  await userEvent.click(within(panelFor("Academic terms")).getByRole("button", { name: "Edit Term 1, AY 2026-2027" }));
  const dialog = within(await screen.findByRole("dialog"));
  await userEvent.clear(dialog.getByLabelText("Term number"));
  await userEvent.type(dialog.getByLabelText("Term number"), "2");
  await userEvent.click(dialog.getByRole("button", { name: "Save changes" }));

  expect(api.updateTerm).toHaveBeenCalledWith(activeTerm.term_id, { academic_year: "2026-2027", term_number: 2 });
  expect(listener).toHaveBeenCalledOnce();
  unsubscribe();
});

it("lists each Block as a table row built from its derived cohort identity", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const blocks = within(panelFor("Block sections"));

  const table = blocks.getByRole("table");
  const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(headers).toEqual(expect.arrayContaining(["Block", "Program", "Year", "Section", "Seats", "Subject offerings", "Paired with"]));
  const row = within(blocks.getByRole("row", { name: /BSCS-1A/ }));
  expect(row.getByRole("rowheader")).toHaveTextContent("BSCS-1A");
  const cells = row.getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells).toEqual(expect.arrayContaining(["BSCS", "1", "A", "20 seats", "CS101"]));
  expect(row.getByRole("button", { name: "Add classes to BSCS-1A" })).toBeInTheDocument();
  expect(row.getByRole("button", { name: "Edit BSCS-1A" })).toBeInTheDocument();
});

it("searches and filters Blocks by Course Program and year level", async () => {
  vi.mocked(api.listBlocks).mockResolvedValue({
    items: [
      { block_id: "block-1", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1, section_letter: "A", is_expanded: false, name: "BSCS-1A", capacity: 20 },
      { block_id: "block-2", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 2, section_letter: "A", is_expanded: false, name: "BSCS-2A", capacity: 20 },
      { block_id: "block-3", term_id: activeTerm.term_id, program_code: "BSIT", year_level: 1, section_letter: "B", is_expanded: false, name: "BSIT-1B", capacity: 20 },
    ],
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const blocks = within(panelFor("Block sections"));
  const names = () => blocks.getAllByRole("rowheader").map((cell) => cell.querySelector("span")?.textContent);
  expect(names()).toEqual(["BSCS-1A", "BSCS-2A", "BSIT-1B"]);

  await userEvent.selectOptions(blocks.getByLabelText("Filter by year level"), "1");
  expect(names()).toEqual(["BSCS-1A", "BSIT-1B"]);
  await userEvent.selectOptions(blocks.getByLabelText("Filter by Course Program"), "BSIT");
  expect(names()).toEqual(["BSIT-1B"]);
  expect(blocks.getByText("1 of 3 Blocks")).toBeInTheDocument();

  await userEvent.click(blocks.getByRole("button", { name: "Clear filters" }));
  await userEvent.type(blocks.getByLabelText("Search Block sections"), "cs101");
  // Subject codes are searchable too; only BSCS-1A takes CS101.
  expect(names()).toEqual(["BSCS-1A"]);
  await userEvent.clear(blocks.getByLabelText("Search Block sections"));
  await userEvent.type(blocks.getByLabelText("Search Block sections"), "nothing");
  expect(blocks.getByText("No Block Sections match your search or filters.")).toBeInTheDocument();
});

it("lets a long unbroken Block name, program code and subject code wrap inside the table", async () => {
  const code = "VERYLONGPROGRAMCODE1";
  const name = `${code}-1A`;
  vi.mocked(api.listCoursePrograms).mockResolvedValue({ items: [{ program_code: code, program_name: "Long" }] });
  vi.mocked(api.listBlocks).mockResolvedValue({
    items: [{ block_id: "block-1", term_id: activeTerm.term_id, program_code: code, year_level: 1, section_letter: "A", is_expanded: false, name, capacity: 40 }],
  });
  vi.mocked(api.listBlockClasses).mockResolvedValue({
    items: [{ class_id: "class-1", block_id: "block-1", offering_id: "offering-1", term_id: activeTerm.term_id, subject_code: "VERYLONGSUBJECTCODE" }],
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });

  const row = within(screen.getByRole("row", { name: new RegExp(name) }));
  expect(row.getByText(name)).toHaveClass("break-words");
  expect(row.getByText(code)).toHaveClass("break-words");
  expect(row.getByText("VERYLONGSUBJECTCODE")).toHaveClass("break-words");
  expect(row.getByText("40 seats")).toBeInTheDocument();

  const blocks = within(panelFor("Block sections"));
  await userEvent.selectOptions(blocks.getByLabelText("Course Program"), code);
  expect(blocks.getByLabelText("Block name")).toHaveClass("min-w-0", "break-words");
});

it("keeps every Subject out of the Block form and derives the name instead", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const blocks = within(panelFor("Block sections"));

  expect(blocks.queryByRole("combobox", { name: /subject/i })).not.toBeInTheDocument();
  expect(blocks.queryByLabelText(/^Subject/)).not.toBeInTheDocument();

  const years = within(blocks.getByLabelText("Year level")).getAllByRole("option");
  expect(years.map((option) => option.textContent)).toEqual(["Year 1", "Year 2", "Year 3", "Year 4", "Year 5"]);

  // The letter is one character, and the derived name reads it back uppercase.
  const letter = blocks.getByLabelText("Section letter");
  expect(letter).toHaveAttribute("maxLength", "1");
  expect(blocks.getByLabelText("Block name")).toHaveTextContent("Choose a Course Program and section letter");

  await userEvent.selectOptions(blocks.getByLabelText("Course Program"), "BSCS");
  await userEvent.selectOptions(blocks.getByLabelText("Year level"), "3");
  await userEvent.type(letter, "b");

  expect(letter).toHaveValue("B");
  expect(blocks.getByLabelText("Block name")).toHaveTextContent("BSCS-3B");
});

it("creates a Block from program, year and letter alone", async () => {
  vi.mocked(api.createBlock).mockResolvedValue({
    block_id: "block-2", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 3,
    section_letter: "B", is_expanded: false, name: "BSCS-3B", capacity: 20,
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const blocks = within(panelFor("Block sections"));

  expect(blocks.getByRole("button", { name: /add block/i })).toBeDisabled();
  await userEvent.selectOptions(blocks.getByLabelText("Course Program"), "BSCS");
  await userEvent.selectOptions(blocks.getByLabelText("Year level"), "3");
  await userEvent.type(blocks.getByLabelText("Section letter"), "b");
  await userEvent.click(blocks.getByRole("button", { name: /add block/i }));

  expect(api.createBlock).toHaveBeenCalledWith({
    term_id: activeTerm.term_id,
    program_code: "BSCS",
    year_level: 3,
    section_letter: "B",
    is_expanded: false,
  });
  expect(api.listBlocks).toHaveBeenCalledTimes(2);
});

it("generates a cohort from program, year and a number of sections, then reports it", async () => {
  vi.mocked(api.generateCohort).mockResolvedValue({
    term_id: activeTerm.term_id, program_code: "BSCS", year_level: 2, term_number: 1,
    curriculum_id: "curriculum-1", curriculum_label: "BSCS 2023-2024",
    required_subject_codes: ["CS101"],
    blocks: [
      { block_id: "block-2", name: "BSCS-2A", section_letter: "A", created: false, note: null,
        classes: [{ class_id: "class-2", offering_id: "offering-2", subject_code: "CS101", group_no: 1, group_label: "CS101 G1", created: false }] },
      { block_id: "block-3", name: "BSCS-2B", section_letter: "B", created: true, note: null,
        classes: [{ class_id: "class-3", offering_id: "offering-3", subject_code: "CS101", group_no: 2, group_label: "CS101 G2", created: true }] },
    ],
    blocks_created: 1, offerings_created: 1, classes_created: 1,
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Generate from curriculum" });
  const cohort = within(panelFor("Generate from curriculum"));

  expect(cohort.getByRole("button", { name: /generate blocks/i })).toBeDisabled();
  await userEvent.selectOptions(cohort.getByLabelText("Course Program"), "BSCS");
  await userEvent.selectOptions(cohort.getByLabelText("Year level"), "2");
  await userEvent.clear(cohort.getByLabelText("Number of sections"));
  await userEvent.type(cohort.getByLabelText("Number of sections"), "2");
  expect(cohort.getByText(/^A, B/)).toBeInTheDocument();
  await userEvent.click(cohort.getByRole("button", { name: /generate blocks/i }));

  expect(api.generateCohort).toHaveBeenCalledWith(activeTerm.term_id, {
    program_code: "BSCS",
    year_level: 2,
    section_letters: ["A", "B"],
  });
  const report = await cohort.findByRole("status");
  expect(report).toHaveTextContent("1 Blocks, 1 offerings, 1 classes created.");
  expect(report).toHaveTextContent("already existed");
  expect(report).toHaveTextContent("CS101 G2 (new)");
  expect(report).toHaveClass("break-words");
  expect(api.listBlocks).toHaveBeenCalledTimes(2);
});

it("reports a cohort with nothing required this term instead of an error", async () => {
  vi.mocked(api.generateCohort).mockResolvedValue({
    term_id: activeTerm.term_id, program_code: "BSCS", year_level: 3, term_number: 3,
    curriculum_id: "curriculum-1", curriculum_label: "BSCS 2023-2024",
    required_subject_codes: [],
    blocks: [{ block_id: "block-2", name: "BSCS-3A", section_letter: "A", created: true, classes: [],
      note: "The BSCS 2023-2024 curriculum requires no subjects of year 3 in term 3." }],
    blocks_created: 1, offerings_created: 0, classes_created: 0,
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Generate from curriculum" });
  const cohort = within(panelFor("Generate from curriculum"));

  await userEvent.selectOptions(cohort.getByLabelText("Course Program"), "BSCS");
  await userEvent.click(cohort.getByRole("button", { name: /generate blocks/i }));

  expect(await cohort.findByRole("status")).toHaveTextContent("requires no subjects of year 3 in term 3");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

function cohortReportFor(termId: string, letter = "B") {
  return {
    term_id: termId, program_code: "BSCS", year_level: 1, term_number: 1,
    curriculum_id: "curriculum-1", curriculum_label: "BSCS 2023-2024",
    required_subject_codes: ["CS101"],
    blocks: [
      { block_id: `block-${letter}`, name: `BSCS-1${letter}`, section_letter: letter, created: true, note: null,
        classes: [{ class_id: "class-2", offering_id: "offering-2", subject_code: "CS101", group_no: 2, group_label: "CS101 G2", created: true }] },
    ],
    blocks_created: 1, offerings_created: 1, classes_created: 1,
  };
}

async function submitCohortForm(letter = "B") {
  await screen.findByRole("heading", { name: "Generate from curriculum" });
  const cohort = within(panelFor("Generate from curriculum"));
  await userEvent.selectOptions(cohort.getByLabelText("Course Program"), "BSCS");
  // Sections are counted from A, so letter N is the Nth section.
  await userEvent.clear(cohort.getByLabelText("Number of sections"));
  await userEvent.type(cohort.getByLabelText("Number of sections"), String(letter.charCodeAt(0) - 64));
  await userEvent.click(cohort.getByRole("button", { name: /generate blocks/i }));
  return cohort;
}

async function switchActiveTermTo(term: typeof activeTerm) {
  vi.mocked(api.getActiveTerm).mockResolvedValue(term);
  await act(async () => publishTermChanged());
  await vi.waitFor(() => expect(api.listBlocks).toHaveBeenCalledWith(term.term_id));
}

it("discards a cohort report that arrives after the active term changed", async () => {
  let resolveReport!: (report: ReturnType<typeof cohortReportFor>) => void;
  vi.mocked(api.generateCohort).mockReturnValue(new Promise((resolve) => { resolveReport = resolve; }));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const cohort = await submitCohortForm();
  expect(api.generateCohort).toHaveBeenCalledWith(activeTerm.term_id, expect.anything());

  await switchActiveTermTo({ ...dormantTerm, is_active: true });
  await act(async () => resolveReport(cohortReportFor(activeTerm.term_id)));

  expect(cohort.queryByRole("status")).not.toBeInTheDocument();
});

it("hides a cohort report once the term it was generated in is no longer active", async () => {
  vi.mocked(api.generateCohort).mockResolvedValue(cohortReportFor(activeTerm.term_id));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const cohort = await submitCohortForm();
  expect(await cohort.findByRole("status")).toHaveTextContent("CS101 G2 (new)");

  await switchActiveTermTo({ ...dormantTerm, is_active: true });
  expect(cohort.queryByRole("status")).not.toBeInTheDocument();

  // Returning to the first term must not bring the old operation report back.
  vi.mocked(api.listBlocks).mockClear();
  await switchActiveTermTo(activeTerm);
  expect(cohort.queryByRole("status")).not.toBeInTheDocument();
});

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (reason: unknown) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

it("keeps the newest cohort report when same-term requests finish out of order", async () => {
  const older = deferred<ReturnType<typeof cohortReportFor>>();
  const newer = deferred<ReturnType<typeof cohortReportFor>>();
  vi.mocked(api.generateCohort).mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await submitCohortForm("B");
  const cohort = await submitCohortForm("C");

  await act(async () => newer.resolve(cohortReportFor(activeTerm.term_id, "C")));
  await act(async () => older.resolve(cohortReportFor(activeTerm.term_id, "B")));

  const report = await cohort.findByRole("status");
  expect(report).toHaveTextContent("BSCS-1C");
  expect(report).not.toHaveTextContent("BSCS-1B");
});

it("drops the error of a superseded same-term request", async () => {
  const older = deferred<ReturnType<typeof cohortReportFor>>();
  vi.mocked(api.generateCohort)
    .mockReturnValueOnce(older.promise)
    .mockResolvedValueOnce(cohortReportFor(activeTerm.term_id, "C"));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await submitCohortForm("B");
  const cohort = await submitCohortForm("C");
  expect(await cohort.findByRole("status")).toHaveTextContent("BSCS-1C");

  await act(async () => older.reject(new ApiError(409, "CONFLICT", "Older request failed.")));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(cohort.getByRole("status")).toHaveTextContent("BSCS-1C");
});

it("keeps a newer request's error when a superseded request then succeeds", async () => {
  const older = deferred<ReturnType<typeof cohortReportFor>>();
  vi.mocked(api.generateCohort)
    .mockReturnValueOnce(older.promise)
    .mockRejectedValueOnce(new ApiError(409, "CONFLICT", "Newer request failed."));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await submitCohortForm("B");
  const cohort = await submitCohortForm("C");
  expect(await screen.findByRole("alert")).toHaveTextContent("Newer request failed.");

  vi.mocked(api.listBlocks).mockClear();
  await act(async () => older.resolve(cohortReportFor(activeTerm.term_id, "B")));

  // The older request's writes are still picked up, without clearing the error.
  await vi.waitFor(() => expect(api.listBlocks).toHaveBeenCalledWith(activeTerm.term_id));
  expect(screen.getByRole("alert")).toHaveTextContent("Newer request failed.");
  expect(cohort.queryByRole("status")).not.toBeInTheDocument();
});

it("drops the error of a request from a term that is no longer active", async () => {
  const stale = deferred<ReturnType<typeof cohortReportFor>>();
  vi.mocked(api.generateCohort).mockReturnValue(stale.promise);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await submitCohortForm();

  await switchActiveTermTo({ ...dormantTerm, is_active: true });
  await act(async () => stale.reject(new ApiError(409, "CONFLICT", "Term A request failed.")));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("names the program and entry year when no curriculum covers a cohort", async () => {
  vi.mocked(api.generateCohort).mockRejectedValue(
    new ApiError(409, "NO_APPLICABLE_CURRICULUM", "No curriculum on file covers this cohort's entry year.",
      { program_code: "BSCS", entry_year: "2019-2020" }),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Generate from curriculum" });
  const cohort = within(panelFor("Generate from curriculum"));

  await userEvent.selectOptions(cohort.getByLabelText("Course Program"), "BSCS");
  await userEvent.click(cohort.getByRole("button", { name: /generate blocks/i }));

  expect(await screen.findByRole("alert")).toHaveClass("break-words");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "No curriculum on file for BSCS covers students who entered in 2019-2020.",
  );
  expect(cohort.queryByRole("status")).not.toBeInTheDocument();
});

it("says so when the active term has no Blocks yet", async () => {
  vi.mocked(api.listBlocks).mockResolvedValue({ items: [] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });

  expect(within(panelFor("Block sections")).getByText("No Block Sections for this term yet.")).toBeInTheDocument();
  expect(within(panelFor("Block sections")).queryByRole("table")).not.toBeInTheDocument();
});

it("surfaces a duplicate cohort as the reason a Block was refused", async () => {
  vi.mocked(api.createBlock).mockRejectedValue(
    new ApiError(409, "DUPLICATE_RESOURCE", "A record with these values already exists."),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });
  const blocks = within(panelFor("Block sections"));

  await userEvent.selectOptions(blocks.getByLabelText("Course Program"), "BSCS");
  await userEvent.type(blocks.getByLabelText("Section letter"), "A");
  await userEvent.click(blocks.getByRole("button", { name: /add block/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("A record with these values already exists.");
  expect(blockRow("BSCS-1A")).toBeInTheDocument();
});

it("names the records that block a Block deletion", async () => {
  vi.mocked(api.deleteBlock).mockRejectedValue(
    new ApiError(409, "DEPENDENT_RECORDS_EXIST", "Other records depend on this one and must be removed first.", {
      dependents: { schedule_records: 3 },
    }),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });

  await userEvent.click(within(blockRow("BSCS-1A")).getByRole("button", { name: "Delete BSCS-1A" }));

  expect(screen.getByRole("dialog", { name: "Delete Block BSCS-1A?" })).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Blocking records: 3 schedule records.");
});

it("names the Block classes that block an offering deletion", async () => {
  vi.mocked(api.deleteOffering).mockRejectedValue(
    new ApiError(409, "DEPENDENT_RECORDS_EXIST", "Other records depend on this one and must be removed first.", {
      dependents: { block_classes: 1 },
    }),
  );
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Subject offerings" });

  const panel = screen.getByRole("heading", { name: "Subject offerings" }).closest("section") as HTMLElement;
  await userEvent.click(within(panel).getByRole("button", { name: "Delete" }));

  expect(screen.getByRole("dialog", { name: "Delete offering CS101 G1?" })).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  expect(api.deleteOffering).toHaveBeenCalledWith("offering-1");
  expect(await screen.findByRole("alert")).toHaveTextContent("Blocking records: 1 block classes.");
});

it("edits only the expansion of an existing Block", async () => {
  vi.mocked(api.updateBlock).mockResolvedValue({
    block_id: "block-1", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1,
    section_letter: "A", is_expanded: true, name: "BSCS-1A", capacity: 25,
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Block sections" });

  await userEvent.click(within(blockRow("BSCS-1A")).getByRole("button", { name: "Edit BSCS-1A" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Edit BSCS-1A (BSCS, Year 1, Section A)" }));
  // The cohort tuple keys the overlap constraint, so no edit can rewrite it.
  expect(dialog.queryByLabelText("Course Program")).not.toBeInTheDocument();
  expect(dialog.queryByLabelText("Section letter")).not.toBeInTheDocument();

  await userEvent.click(dialog.getByLabelText("Expanded (25 seats)"));
  await userEvent.click(dialog.getByRole("button", { name: "Save changes" }));

  expect(api.updateBlock).toHaveBeenCalledWith("block-1", true);
});

it("shows each Block's letter partner of the same program and year", async () => {
  const block = { block_id: "block-1", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1, section_letter: "A", is_expanded: false, name: "BSCS-1A", capacity: 20 };
  vi.mocked(api.listBlocks).mockResolvedValue({
    items: [
      block,
      { ...block, block_id: "block-2", section_letter: "B", name: "BSCS-1B" },
      { ...block, block_id: "block-3", section_letter: "C", name: "BSCS-1C" },
      { ...block, block_id: "block-4", year_level: 2, section_letter: "B", name: "BSCS-2B" },
    ],
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(within(await findBlockRow("BSCS-1A")).getAllByRole("cell").at(-2)).toHaveTextContent("BSCS-1B");
  expect(within(blockRow("BSCS-1B")).getAllByRole("cell").at(-2)).toHaveTextContent("BSCS-1A");
  // C pairs with D, and a Block of another year is never a partner.
  expect(within(blockRow("BSCS-1C")).getAllByRole("cell").at(-2)).toHaveTextContent("—");
  expect(within(blockRow("BSCS-2B")).getAllByRole("cell").at(-2)).toHaveTextContent("—");
});

it("periodically synchronizes term-scoped block sections every 5 seconds", async () => {
  vi.useFakeTimers();
  try {
    render(<MemoryRouter><SchedulePage /></MemoryRouter>);

    // Flush initial mount
    await act(async () => {
      await Promise.resolve();
    });

    expect(blockRow("BSCS-1A")).toBeInTheDocument();
    expect(queryBlockRow("BSCS-1B")).not.toBeInTheDocument();

    // Another session adds block BSCS-1B
    vi.mocked(api.listBlocks).mockResolvedValue({
      items: [
        { block_id: "block-1", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1, section_letter: "A", is_expanded: false, name: "BSCS-1A", capacity: 20 },
        { block_id: "block-2", term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1, section_letter: "B", is_expanded: false, name: "BSCS-1B", capacity: 20 },
      ],
    });
    vi.mocked(api.listBlockClasses).mockImplementation(async (blockId: string) => {
      if (blockId === "block-1") {
        return { items: [{ class_id: "class-1", block_id: "block-1", offering_id: "offering-1", term_id: activeTerm.term_id, subject_code: "CS101" }] };
      }
      return { items: [] };
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(blockRow("BSCS-1B")).toBeInTheDocument();
    expect(screen.queryByText("Loading setup data...")).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("puts curriculum generation ahead of a collapsed advanced section", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const generate = await screen.findByRole("heading", { name: "Generate from curriculum" });
  const advanced = screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement;

  expect(advanced.open).toBe(false);
  expect(generate.compareDocumentPosition(advanced) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(advanced.contains(document.getElementById("time-slots"))).toBe(true);
  expect(advanced.contains(document.getElementById("generate-from-curriculum"))).toBe(false);
});

it("opens the advanced section by itself when time slots are missing", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Generate from curriculum" });

  expect((screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement).open).toBe(true);
});

it("links each readiness blocker to where it is fixed", async () => {
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: activeTerm.term_id,
    ready: false,
    blockers: [
      { code: "NO_ROOMS", message: "No rooms are defined.", details: {} },
      { code: "NO_BLOCK_CLASSES", message: "The term has no Block classes.", details: {} },
      { code: "SOMETHING_NEW", message: "An unmapped blocker.", details: {} },
    ],
    warnings: [],
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  const checklist = await screen.findByRole("list", { name: "Setup checklist" });
  expect(within(checklist).getByRole("link", { name: "Add a room" })).toHaveAttribute("href", "/rooms");
  expect(within(checklist).getByRole("link", { name: "Generate classes" })).toHaveAttribute("href", "/schedule#generate-from-curriculum");
  expect(within(checklist).getByText("An unmapped blocker.").querySelector("a")).toBeNull();
  const item = within(checklist).getByText("An unmapped blocker.");
  expect(item).toHaveClass("break-words");
  expect(item.parentElement?.parentElement).toHaveClass("min-w-0");
});

it("opens the closed advanced section and scrolls to a time-slots fix link", async () => {
  const scrollIntoView = stubScrollIntoView();
  render(<MemoryRouter initialEntries={["/schedule#time-slots"]}><SchedulePage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Generate from curriculum" });

  await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  expect((screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement).open).toBe(true);
  expect(scrollIntoView.mock.contexts[0]).toBe(document.getElementById("time-slots"));
  // A router Link leaves keyboard focus behind, so the target takes it.
  expect(document.getElementById("time-slots")).toHaveFocus();
});

it("keeps the advanced section open after a fix link when generating slots", async () => {
  stubScrollIntoView();
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockResolvedValue({
    items: [{ time_slot_id: "slot-9", term_id: activeTerm.term_id, label: "LEC 07:30-09:00", start_time: "07:30:00", end_time: "09:00:00", ordinal: 0 }],
  });
  render(<MemoryRouter initialEntries={["/schedule#time-slots"]}><SchedulePage /></MemoryRouter>);
  const advanced = (await screen.findByText("Advanced setup")).closest("details") as HTMLDetailsElement;
  await waitFor(() => expect(document.getElementById("time-slots")).toHaveFocus());

  vi.mocked(api.listTimeSlots).mockResolvedValue({
    items: [{ time_slot_id: "slot-9", term_id: activeTerm.term_id, label: "LEC 07:30-09:00", start_time: "07:30:00", end_time: "09:00:00", ordinal: 0 }],
  });
  await userEvent.click(screen.getByRole("button", { name: "Generate canonical slots" }));

  await screen.findByText("LEC 07:30-09:00");
  expect(advanced.open).toBe(true);
});

it("does not pull focus out of a field when a pending fix link resolves late", async () => {
  const scrollIntoView = stubScrollIntoView();
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(409, "NO_ACTIVE_TERM", "No active term is selected."),
  );
  render(<MemoryRouter initialEntries={["/schedule#time-slots"]}><SchedulePage /></MemoryRouter>);
  const year = await screen.findByLabelText("Academic year");
  act(() => year.focus());

  vi.mocked(api.getActiveTerm).mockResolvedValue(activeTerm);
  act(() => publishTermChanged());
  await screen.findByRole("heading", { name: "Time slots" });
  // The section still opens for the late navigation; only focus and scroll are withheld.
  await waitFor(() => expect((screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement).open).toBe(true));

  expect(year).toHaveFocus();
  expect(scrollIntoView).not.toHaveBeenCalled();
});

it("keeps a fix-link navigation pending until the target exists", async () => {
  const scrollIntoView = stubScrollIntoView();
  vi.mocked(api.getActiveTerm).mockRejectedValue(
    new ApiError(409, "NO_ACTIVE_TERM", "No active term is selected."),
  );
  render(<MemoryRouter initialEntries={["/schedule#time-slots"]}><SchedulePage /></MemoryRouter>);
  await screen.findByText(/Select an active term before adding term-scoped scheduling inputs\./);
  expect(scrollIntoView).not.toHaveBeenCalled();

  vi.mocked(api.getActiveTerm).mockResolvedValue(activeTerm);
  act(() => publishTermChanged());

  await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  expect((screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement).open).toBe(true);
});

it("keys repeated identical blockers without a duplicate key warning", async () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const blocker = { code: "NO_ROOMS", message: "No rooms are defined.", details: {} };
  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: false, blockers: [blocker, blocker], warnings: [] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  const checklist = await screen.findByRole("list", { name: "Setup checklist" });
  expect(within(checklist).getAllByText("No rooms are defined.")).toHaveLength(2);
  expect(error.mock.calls.flat().some((arg) => String(arg).includes("same key"))).toBe(false);
});

function stubScrollIntoView() {
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  onTestFinished(() => { delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView; });
  return scrollIntoView;
}

async function openAddClasses() {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const card = await findBlockRow("BSCS-1A");
  await userEvent.click(within(card).getByRole("button", { name: "Add classes to BSCS-1A" }));
  return within(screen.getByRole("dialog"));
}

function stubNewOfferings() {
  vi.mocked(api.listBlockClasses).mockResolvedValue({ items: [] });
  vi.mocked(api.createOffering).mockImplementation(async (termId, subjectCode) => ({
    offering_id: `offering-${subjectCode}`, term_id: termId, subject_code: subjectCode, group_no: 2, group_label: `${subjectCode} G2`,
  }));
}

const linkedClass = { class_id: "class-2", block_id: "block-1", offering_id: "offering-x", term_id: activeTerm.term_id, subject_code: "CS101" };

it("adds several subjects to a Block with one new offering each", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockResolvedValue(linkedClass);

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS220/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 2 classes" }));

  expect(api.addBlockClass).toHaveBeenCalledWith("block-1", "offering-CS101");
  expect(api.addBlockClass).toHaveBeenCalledWith("block-1", "offering-CS220");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps a failed subject open and retries it with the offering already created", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass)
    .mockResolvedValueOnce(linkedClass)
    .mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS220/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 2 classes" }));

  const results = await dialog.findByRole("list", { name: "Results" });
  expect(within(results).getByText("CS101: Added")).toBeInTheDocument();
  expect(within(results).getByText("CS220: That class was refused.")).toBeInTheDocument();

  vi.mocked(api.addBlockClass).mockResolvedValueOnce(linkedClass);
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));

  expect(api.createOffering).toHaveBeenCalledTimes(2);
  expect(api.addBlockClass).toHaveBeenLastCalledWith("block-1", "offering-CS220");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("does not submit when Enter is pressed in the subject search", async () => {
  stubNewOfferings();

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.type(dialog.getByLabelText("Find a subject"), "CS22{Enter}");

  expect(api.createOffering).not.toHaveBeenCalled();
  expect(dialog.getByRole("checkbox", { name: /CS220/ })).toBeInTheDocument();
  // CS101 is filtered out of view but still selected, so it stays listed.
  expect(dialog.getByText("CS101")).toBeInTheDocument();
});

it("announces a failure and moves focus to it", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  const status = await dialog.findByRole("status");
  expect(status).toHaveTextContent("CS101: That class was refused.");
  expect(await dialog.findByRole("button", { name: "Retry 1 failed" })).toHaveFocus();
});

it("does not submit when Enter is pressed on a subject checkbox", async () => {
  stubNewOfferings();

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  dialog.getByRole("checkbox", { name: /CS220/ }).focus();
  await userEvent.keyboard("{Enter}");

  expect(api.createOffering).not.toHaveBeenCalled();
});

it("stays open over an unticked failure when another subject then succeeds", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass)
    .mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."))
    .mockResolvedValueOnce(linkedClass);

  const dialog = await openAddClasses();
  const failing = dialog.getByRole("checkbox", { name: /CS101/ });
  await userEvent.click(failing);
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByText("CS101: That class was refused.");
  await userEvent.click(failing);
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS220/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  await dialog.findByText("CS220: Added");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(dialog.getByText(/left a new class group unlinked/)).toBeInTheDocument();
});

it("reports the group it stranded when another session linked the subject during a retry", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByText("CS101: That class was refused.");
  vi.mocked(api.listBlockClasses).mockResolvedValueOnce({ items: [linkedClass] });
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));

  expect(await dialog.findByText(/Another session added this first/)).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(dialog.getByRole("button", { name: "Close" })).toHaveFocus();
});

it("treats a retry that finds its own group linked as added, after a lost response", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockRejectedValueOnce(new TypeError("Failed to fetch"));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByRole("button", { name: "Retry 1 failed" });
  vi.mocked(api.listBlockClasses).mockResolvedValueOnce({ items: [{ ...linkedClass, offering_id: "offering-CS101" }] });
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.createOffering).toHaveBeenCalledTimes(1);
});

it("warns of a possible extra group when a create's response was lost and the retry succeeds", async () => {
  stubNewOfferings();
  vi.mocked(api.createOffering).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  vi.mocked(api.addBlockClass).mockResolvedValue(linkedClass);

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await userEvent.click(await dialog.findByRole("button", { name: "Retry 1 failed" }));

  expect(await dialog.findByText(/may have created an extra class group for CS101/)).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("keeps the lost-create warning when a retry finds the subject already linked", async () => {
  stubNewOfferings();
  vi.mocked(api.createOffering).mockRejectedValueOnce(new TypeError("Failed to fetch"));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByRole("button", { name: "Retry 1 failed" });
  vi.mocked(api.listBlockClasses).mockResolvedValueOnce({ items: [linkedClass] });
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));

  expect(await dialog.findByText(/extra class group for CS101/)).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("keeps the lost-create warning through a failed re-read and a later refusal", async () => {
  stubNewOfferings();
  vi.mocked(api.createOffering).mockRejectedValueOnce(new TypeError("Failed to fetch"));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByRole("button", { name: "Retry 1 failed" });

  vi.mocked(api.listBlockClasses).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));
  await dialog.findByText(/Retry when it is back/);
  expect(dialog.getByText(/extra class group for CS101/)).toBeInTheDocument();

  vi.mocked(api.createOffering).mockRejectedValueOnce(new ApiError(409, "TERM_NOT_ACTIVE", "The term is not active."));
  await userEvent.click(dialog.getByRole("button", { name: "Retry 1 failed" }));
  await dialog.findByText("CS101: The term is not active.");
  expect(dialog.getByText(/extra class group for CS101/)).toBeInTheDocument();
});

it("keeps the advanced section open through a retry after a collapse and reopen", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockRejectedValue(new ApiError(500, "FAILED", "Generation failed."));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("button", { name: "Generate canonical slots" }));
  const retry = within(await screen.findByRole("alert")).getByRole("button", { name: "Retry" });
  const summary = screen.getByText("Advanced setup");
  const advanced = summary.closest("details") as HTMLDetailsElement;
  await userEvent.click(summary);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve)); });
  await userEvent.click(summary);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve)); });

  await userEvent.click(retry);

  expect(advanced.open).toBe(true);
});

it("drops a slot-generation error when the active term changes", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockRejectedValue(new ApiError(500, "FAILED", "Generation failed."));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("button", { name: "Generate canonical slots" }));
  await screen.findByRole("alert");

  vi.mocked(api.getActiveTerm).mockResolvedValue({ ...dormantTerm, is_active: true });
  vi.mocked(api.listTimeSlots).mockResolvedValue({
    items: [{ time_slot_id: "slot-2", term_id: dormantTerm.term_id, label: "MWF 09:00", start_time: "09:00:00", end_time: "10:30:00", ordinal: 0 }],
  });
  act(() => publishTermChanged());

  // The reset runs in an effect after the new slots render, so wait for it.
  await screen.findByText("MWF 09:00");
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect((screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement).open).toBe(false);
});

it("creates a new group on retry when the one it made was deleted", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass)
    .mockRejectedValueOnce(new ApiError(409, "REFERENCE_NOT_FOUND", "The requested subject offering was not found."))
    .mockResolvedValueOnce(linkedClass);

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await userEvent.click(await dialog.findByRole("button", { name: "Retry 1 failed" }));

  expect(api.createOffering).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("says Retry when it is back when only the re-read could not reach the server", async () => {
  stubNewOfferings();
  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  vi.mocked(api.listBlockClasses).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  expect(await dialog.findByText(/Retry when it is back/)).toBeInTheDocument();
  expect(api.createOffering).not.toHaveBeenCalled();
});

it("points to Course Programs from generation when none exist", async () => {
  vi.mocked(api.listCoursePrograms).mockResolvedValue({ items: [] });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);

  expect(await screen.findByRole("link", { name: "Add a Course Program" })).toHaveAttribute("href", "#course-programs");
});

it("opens the advanced section to show a slot-generation failure after a collapse", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockRejectedValue(new ApiError(500, "FAILED", "Generation failed."));
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Generate canonical slots" });
  const summary = screen.getByText("Advanced setup");
  const advanced = summary.closest("details") as HTMLDetailsElement;
  await userEvent.click(summary);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve)); });
  expect(advanced.open).toBe(false);

  await act(async () => { generate.click(); });

  expect(await screen.findByRole("alert")).toHaveTextContent("Generation failed.");
  expect(advanced.open).toBe(true);
});

it("creates nothing when the Block is gone by the time the user submits", async () => {
  stubNewOfferings();
  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  vi.mocked(api.listBlockClasses).mockRejectedValueOnce(new ApiError(404, "NOT_FOUND", "The requested Block Section was not found."));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  expect(await dialog.findByText("CS101: The requested Block Section was not found.")).toBeInTheDocument();
  expect(api.createOffering).not.toHaveBeenCalled();
});

it("does not create a second group for a subject another session already linked", async () => {
  stubNewOfferings();
  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  vi.mocked(api.listBlockClasses).mockResolvedValueOnce({ items: [linkedClass] });
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  expect(api.createOffering).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps the left-behind notice after a failed subject is unticked", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."));

  const dialog = await openAddClasses();
  const box = dialog.getByRole("checkbox", { name: /CS101/ });
  await userEvent.click(box);
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByText("CS101: That class was refused.");
  await userEvent.click(box);

  expect(dialog.getByText("CS101: That class was refused.")).toBeInTheDocument();
  expect(dialog.getByText(/left a new class group unlinked/)).toBeInTheDocument();
  expect(dialog.getByRole("button", { name: "Close" })).toBeInTheDocument();
});

it("counts every write in the button once a new subject joins a retry", async () => {
  stubNewOfferings();
  vi.mocked(api.addBlockClass).mockRejectedValueOnce(new ApiError(409, "CONFLICT", "That class was refused."));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));
  await dialog.findByRole("button", { name: "Retry 1 failed" });
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS220/ }));

  expect(dialog.getByRole("button", { name: "Add 2 classes" })).toBeInTheDocument();
});

it("warns that an unreachable server may already have saved a subject", async () => {
  stubNewOfferings();
  vi.mocked(api.createOffering).mockRejectedValueOnce(new TypeError("Failed to fetch"));

  const dialog = await openAddClasses();
  await userEvent.click(dialog.getByRole("checkbox", { name: /CS101/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Add 1 class" }));

  expect(await dialog.findByText(/this may have been saved/)).toBeInTheDocument();
});

it("points to the catalog when there are no subjects at all", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [] });

  const dialog = await openAddClasses();

  expect(dialog.getByRole("link", { name: "Subjects catalog" })).toHaveAttribute("href", "/subjects");
  expect(dialog.queryByText(/already takes every subject/)).not.toBeInTheDocument();
});

it("closes the advanced section again once the missing slots exist", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.generateCanonicalSlots).mockResolvedValue({ items: [] } as never);
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Generate canonical slots" });
  const advanced = screen.getByText("Advanced setup").closest("details") as HTMLDetailsElement;
  expect(advanced.open).toBe(true);

  vi.mocked(api.listTimeSlots).mockResolvedValue({
    items: [{ time_slot_id: "slot-1", term_id: activeTerm.term_id, label: "MWF 08:00", start_time: "08:00:00", end_time: "09:30:00", ordinal: 0 }],
  });
  await userEvent.click(generate);

  await within(advanced).findByText("MWF 08:00");
  expect(advanced.open).toBe(false);
});

it("reopens a collapsed advanced section from a checklist link even while it is auto-open", async () => {
  vi.mocked(api.listTimeSlots).mockResolvedValue({ items: [] });
  vi.mocked(api.getReadiness).mockResolvedValue({
    term_id: activeTerm.term_id, ready: false, warnings: [],
    blockers: [{ code: "NO_TIME_SLOTS", message: "The term has no time slots.", details: {} }],
  });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const link = await screen.findByRole("link", { name: "Generate time slots" });
  const summary = screen.getByText("Advanced setup");
  const advanced = summary.closest("details") as HTMLDetailsElement;

  await userEvent.click(summary);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve)); });
  expect(advanced.open).toBe(false);

  await userEvent.click(link);

  expect(advanced.open).toBe(true);
});

it("does not offer a subject the Block already takes", async () => {
  const dialog = await openAddClasses();

  expect(dialog.queryByRole("checkbox", { name: /CS101/ })).not.toBeInTheDocument();
  expect(dialog.getByRole("checkbox", { name: /CS220/ })).toBeInTheDocument();
});

// jsdom has no layout, so assert the sizing classes: 44px below md, 24px from md up.
it("gives Term Setup actions 44px touch targets below md and 24px from md up, with a gap", async () => {
  const sized = (el: HTMLElement) => {
    expect(el.className).toContain("min-h-11");
    expect(el.className).toContain("md:min-h-6");
  };
  const dialog = await openAddClasses();
  const page = within(document.body);
  // flex on <summary> drops the disclosure triangle, so size it with padding.
  const summary = page.getByText("Advanced setup");
  expect(summary.className).not.toContain("flex");
  expect(summary.className).toContain("py-3");
  sized(dialog.getByRole("button", { name: "Cancel" }));
  sized(dialog.getByRole("button", { name: "Add classes" }));
});

function blocksOf(count: number) {
  return Array.from({ length: count }, (_, n) => ({
    block_id: `block-${n + 1}`, term_id: activeTerm.term_id, program_code: "BSCS", year_level: 1,
    section_letter: String.fromCharCode(65 + n), is_expanded: false, name: `BSCS-1${String.fromCharCode(65 + n)}`, capacity: 20,
  }));
}

it("renders every Block row with no jump link at 8 Blocks", async () => {
  vi.mocked(api.listBlocks).mockResolvedValue({ items: blocksOf(8) });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await findBlockRow("BSCS-1A");

  expect(within(panelFor("Block sections")).getAllByRole("rowheader")).toHaveLength(8);
  expect(screen.queryByRole("link", { name: "Jump to Advanced setup" })).not.toBeInTheDocument();
});

it("renders all 11 Block rows and a jump link to Advanced setup above them", async () => {
  vi.mocked(api.listBlocks).mockResolvedValue({ items: blocksOf(11) });
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  const link = await screen.findByRole("link", { name: "Jump to Advanced setup" });

  expect(within(panelFor("Block sections")).getAllByRole("rowheader")).toHaveLength(11);
  expect(link).toHaveAttribute("href", "#course-programs");
  expect(link.compareDocumentPosition(within(panelFor("Block sections")).getByRole("table")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(link.className).toContain("min-h-11");
  expect(link.className).toContain("md:min-h-6");
  expect(screen.queryByRole("button", { name: /Show all|Show fewer/ })).not.toBeInTheDocument();
});

it("clears a load error when a background read succeeds", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  const loadAlert = await screen.findByRole("alert");
  expect(loadAlert).toHaveTextContent("Down.");
  expect(loadAlert).toHaveClass("break-words");

  vi.mocked(api.getReadiness).mockResolvedValue({ term_id: activeTerm.term_id, ready: true, blockers: [], warnings: [] });
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("re-announces a retried refresh that fails the same way", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  const first = await screen.findByRole("alert");

  await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await waitFor(() => expect(screen.getByRole("alert")).not.toBe(first));
  expect(screen.getByRole("alert")).toHaveTextContent("Down.");
});

it("hides the Master Schedule link without an alert when a background poll fails", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.getReadiness).mockRejectedValue(new ApiError(500, "SERVER_ERROR", "Down."));
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Go to Master Schedule" })).not.toBeInTheDocument());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps a failed save's error through a successful background read", async () => {
  render(<MemoryRouter><SchedulePage /></MemoryRouter>);
  await screen.findByRole("link", { name: "Go to Master Schedule" });

  vi.mocked(api.createCourseProgram).mockRejectedValue(new ApiError(409, "DUPLICATE_RESOURCE", "That program code already exists."));
  const programs = within(panelFor("Course Programs"));
  await userEvent.type(programs.getByLabelText("Course Program code"), "BSCS");
  await userEvent.type(programs.getByLabelText("Course Program name"), "BS Computer Science");
  await userEvent.click(programs.getByRole("button", { name: /add/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That program code already exists.");

  vi.mocked(api.getReadiness).mockClear();
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(api.getReadiness).toHaveBeenCalled());
  expect(screen.getByRole("alert")).toHaveTextContent("That program code already exists.");
});
