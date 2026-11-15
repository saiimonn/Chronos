import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type Subject } from "../api/client";
import SubjectsPage from "./SubjectsPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    api: {
      ...original.api,
      listSubjects: vi.fn(),
      createSubject: vi.fn(),
      updateSubject: vi.fn(),
      deactivateSubject: vi.fn(),
      listRooms: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { username: "operator", role: "scheduling_committee" }, logout: vi.fn() }),
}));

vi.mock("../hooks/useActiveTerm", () => ({
  useActiveTerm: () => ({ term: null, loading: false }),
}));

const lecture: Subject = {
  subject_code: "CS101",
  subject_title: "Intro to Computing",
  units: 3,
  subject_type: "LEC",
  duration_minutes: 90,
};

const laboratory: Subject = {
  subject_code: "CS102L",
  subject_title: "Data Structures Laboratory",
  units: 1,
  subject_type: "LAB",
  duration_minutes: 150,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [lecture, laboratory] });
  vi.mocked(api.createSubject).mockResolvedValue(lecture);
  vi.mocked(api.updateSubject).mockResolvedValue(lecture);
  vi.mocked(api.deactivateSubject).mockResolvedValue(undefined);
  vi.mocked(api.listRooms).mockResolvedValue({
    items: [
      { room_code: "LB 470", building_name: "LB", capacity: 40, room_type: "laboratory", expanded: false },
      { room_code: "LB 461", building_name: "LB", capacity: 40, room_type: "laboratory", expanded: false },
      { room_code: "LB 201", building_name: "LB", capacity: 40, room_type: "lecture", expanded: false },
    ],
  });
});

function renderPage() {
  render(<MemoryRouter><SubjectsPage /></MemoryRouter>);
}

it("lists catalog subjects with the server-derived duration", async () => {
  renderPage();

  expect(await screen.findByText("CS101")).toBeInTheDocument();
  expect(screen.getByText("Intro to Computing")).toBeInTheDocument();
  const card = screen.getByText("CS101").closest("tr") as HTMLElement;
  expect(screen.getByRole("columnheader", { name: "Duration (derived)" })).toBeInTheDocument();
  expect(within(card).getByText("90 min")).toBeInTheDocument();
  expect(within(card).getByText("LEC")).toBeInTheDocument();
});

it("shows each subject's preferred day patterns, or that it has none", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({
    items: [{ ...lecture, preferred_day_patterns: ["MW", "TTh"] }, { ...laboratory, preferred_day_patterns: [] }],
  });
  renderPage();

  const row = (code: string) => screen.getByText(code).closest("tr") as HTMLElement;
  await screen.findByText("CS101");
  expect(screen.getByRole("columnheader", { name: "Preferred days" })).toBeInTheDocument();
  expect(within(row("CS101")).getByText("MW, TTh")).toBeInTheDocument();
  expect(within(row("CS102L")).getByText("No preference")).toBeInTheDocument();
});

it("reads a subject without the preference field as having no preference", async () => {
  renderPage();

  // Records saved before day patterns existed omit the field entirely.
  expect(within((await screen.findByText("CS101")).closest("tr") as HTMLElement).getByText("No preference")).toBeInTheDocument();
});

it("reports a loading state before the catalog arrives", () => {
  vi.mocked(api.listSubjects).mockReturnValue(new Promise(() => {}));
  renderPage();

  expect(screen.getByText("Loading subjects...")).toBeInTheDocument();
});

it("invites the first record when the catalog is empty", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [] });
  renderPage();

  expect(await screen.findByText(/No subjects recorded yet/)).toBeInTheDocument();
});

it("searches by code and by title, and says when nothing matches", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  const search = screen.getByRole("searchbox", { name: "Search subjects" });
  await user.type(search, "laboratory");
  expect(screen.getByText("CS102L")).toBeInTheDocument();
  expect(screen.queryByText("CS101")).not.toBeInTheDocument();

  await user.clear(search);
  await user.type(search, "cs101");
  expect(screen.getByText("CS101")).toBeInTheDocument();
  expect(screen.queryByText("CS102L")).not.toBeInTheDocument();

  await user.clear(search);
  await user.type(search, "astronomy");
  expect(screen.getByText('No subjects match "astronomy".')).toBeInTheDocument();
});

it("finds a subject by the initials of its title", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({
    items: [{ ...laboratory, subject_code: "CIS 2101", subject_title: "DATA STRUCTURES AND ALGORITHMS" }, lecture],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.type(screen.getByRole("searchbox", { name: "Search subjects" }), "DSA");
  expect(screen.getByText("CIS 2101")).toBeInTheDocument();
  expect(screen.queryByText("CS101")).not.toBeInTheDocument();
});

it("filters by lecture or laboratory type", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.selectOptions(screen.getByRole("combobox", { name: "Type" }), "Laboratory");
  expect(screen.getByText("CS102L")).toBeInTheDocument();
  expect(screen.queryByText("CS101")).not.toBeInTheDocument();

  await user.selectOptions(screen.getByRole("combobox", { name: "Type" }), "Lecture");
  expect(screen.getByText("CS101")).toBeInTheDocument();
  expect(screen.queryByText("CS102L")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByText("CS102L")).toBeInTheDocument();
});

it("filters by course, year, and semester against a single prospectus placement", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({
    items: [
      { ...lecture, placements: [{ program_code: "BSIT", year_level: 1, term_number: 2 }, { program_code: "BSCS", year_level: 2, term_number: 1 }] },
      { ...laboratory, placements: [{ program_code: "BSCS", year_level: 1, term_number: 2 }] },
    ],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.selectOptions(screen.getByRole("combobox", { name: "Course" }), "BSCS");
  expect(screen.getByText("CS101")).toBeInTheDocument();
  expect(screen.getByText("CS102L")).toBeInTheDocument();

  // CS101 is 2nd semester only in BSIT, so BSCS + 2nd semester drops it.
  await user.selectOptions(screen.getByRole("combobox", { name: "Semester" }), "2nd semester");
  expect(screen.queryByText("CS101")).not.toBeInTheDocument();
  expect(screen.getByText("CS102L")).toBeInTheDocument();

  await user.selectOptions(screen.getByRole("combobox", { name: "Year level" }), "2nd year");
  expect(screen.getByText("No subjects in the prospectus for this course, year, and semester.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByText("CS101")).toBeInTheDocument();
  expect(screen.getByText("CS102L")).toBeInTheDocument();
});

it("creates a subject and previews the duration its type derives", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.click(screen.getByRole("button", { name: /add subject/i }));
  const dialog = within(await screen.findByRole("dialog"));

  await user.type(dialog.getByLabelText("Subject code"), "CS103");
  await user.type(dialog.getByLabelText("Subject title"), "Algorithms");
  await user.clear(dialog.getByLabelText("Units"));
  await user.type(dialog.getByLabelText("Units"), "3");

  // Duration is never entered: it follows the type the server derives it from.
  expect(dialog.getByText(/90 minutes/)).toBeInTheDocument();
  expect(dialog.queryByLabelText(/duration/i)).not.toBeInTheDocument();
  await user.selectOptions(dialog.getByLabelText("Subject type"), "LAB");
  expect(dialog.getByText(/150 minutes/)).toBeInTheDocument();

  // A soft day preference: this lab is usually taught Friday-only.
  const friday = dialog.getByRole("button", { name: "Friday" });
  expect(friday).toHaveAttribute("aria-pressed", "false");
  await user.click(friday);
  expect(friday).toHaveAttribute("aria-pressed", "true");

  await user.click(dialog.getByRole("button", { name: "Save" }));

  expect(api.createSubject).toHaveBeenCalledWith({
    subject_code: "CS103",
    subject_title: "Algorithms",
    units: 3,
    subject_type: "LAB",
    preferred_day_patterns: ["F"],
    allowed_room_codes: [],
  });
  expect(api.listSubjects).toHaveBeenCalledTimes(2);
});

it("does not let a LAB subject pick a lecture room", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.click(screen.getByRole("button", { name: /add subject/i }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(await dialog.findByRole("button", { name: "LB 201" })).toBeEnabled();
  await user.selectOptions(dialog.getByLabelText("Subject type"), "LAB");
  expect(dialog.getByRole("button", { name: "LB 201" })).toBeDisabled();
  expect(dialog.getByRole("button", { name: "LB 461" })).toBeEnabled();
});

it("picks days only as one of the allowed patterns", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");
  await user.click(screen.getByRole("button", { name: /add subject/i }));
  const dialog = within(await screen.findByRole("dialog"));
  const day = (name: string) => dialog.getByRole("button", { name });

  // M only exists in MW, so W comes with it and every other day locks.
  await user.click(day("Monday"));
  expect(day("Monday")).toHaveAttribute("aria-pressed", "true");
  expect(day("Wednesday")).toHaveAttribute("aria-pressed", "true");
  for (const name of ["Tuesday", "Thursday", "Friday", "Saturday", "Sunday"]) expect(day(name)).toBeDisabled();
  expect(dialog.getByText("MW")).toBeInTheDocument();

  // Unpicking W clears MW.
  await user.click(day("Wednesday"));
  expect(day("Monday")).toHaveAttribute("aria-pressed", "false");

  // F alone is F; adding Sat makes FSat; Sun cannot join either.
  await user.click(day("Friday"));
  expect(day("Saturday")).toBeEnabled();
  expect(day("Sunday")).toBeDisabled();
  await user.click(day("Saturday"));
  expect(dialog.getByText("FSat")).toBeInTheDocument();

  await user.type(dialog.getByLabelText("Subject code"), "NET1");
  await user.type(dialog.getByLabelText("Subject title"), "Networking");
  await user.click(await dialog.findByRole("button", { name: "LB 470" }));
  await user.click(dialog.getByRole("button", { name: "LB 461" }));
  await user.click(dialog.getByRole("button", { name: "Save" }));

  expect(api.createSubject).toHaveBeenCalledWith(expect.objectContaining({
    preferred_day_patterns: ["FSa"],
    allowed_room_codes: ["LB 461", "LB 470"],
  }));
});

it("keeps a refused create on screen with the reason the API gave", async () => {
  vi.mocked(api.createSubject).mockRejectedValue(
    new ApiError(409, "DUPLICATE_RESOURCE", "A record with these values already exists."),
  );
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  await user.click(screen.getByRole("button", { name: /add subject/i }));
  const dialog = within(await screen.findByRole("dialog"));
  await user.type(dialog.getByLabelText("Subject code"), "CS101");
  await user.type(dialog.getByLabelText("Subject title"), "Intro to Computing");
  await user.click(dialog.getByRole("button", { name: "Save" }));

  expect(await dialog.findByRole("alert")).toHaveTextContent("A record with these values already exists.");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(dialog.getByLabelText("Subject title")).toHaveValue("Intro to Computing");
});

it("edits a subject without letting its code move", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  const card = screen.getByText("CS101").closest("tr") as HTMLElement;
  await user.click(within(card).getByRole("button", { name: /edit/i }));
  const dialog = within(await screen.findByRole("dialog"));

  // The code identifies the record, so it is shown but not editable.
  expect(dialog.getByLabelText("Subject code")).toBeDisabled();
  expect(dialog.getByLabelText("Subject code")).toHaveValue("CS101");

  await user.clear(dialog.getByLabelText("Units"));
  await user.type(dialog.getByLabelText("Units"), "5");
  await user.selectOptions(dialog.getByLabelText("Subject type"), "LAB");
  await user.click(dialog.getByRole("button", { name: "Save" }));

  expect(api.updateSubject).toHaveBeenCalledWith("CS101", {
    subject_title: "Intro to Computing",
    units: 5,
    subject_type: "LAB",
    preferred_day_patterns: [],
    allowed_room_codes: [],
  });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("deactivates a subject in an accessible dialog instead of deleting it", async () => {
  const confirm = vi.spyOn(window, "confirm");
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");
  // The catalog page reads inactive subjects too.
  expect(api.listSubjects).toHaveBeenCalledWith(true);
  expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Deactivate CS101" }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(within(dialog).getByRole("heading", { name: "Deactivate CS101?" })).toBeInTheDocument();
  expect(dialog).toHaveTextContent("Nothing is deleted");

  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

  expect(api.deactivateSubject).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Deactivate CS101" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Deactivate" }));

  expect(api.deactivateSubject).toHaveBeenCalledWith("CS101");
  expect(api.listSubjects).toHaveBeenCalledTimes(2);
});

it("hides inactive subjects until asked, then offers to activate them", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [lecture, { ...laboratory, is_active: false }] });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("CS101");

  expect(screen.queryByText("CS102L")).not.toBeInTheDocument();
  const toggle = screen.getByRole("checkbox", { name: "Show inactive (1)" });
  expect(toggle).not.toBeChecked();

  await user.click(toggle);

  const card = screen.getByText("CS102L").closest("tr") as HTMLElement;
  expect(within(card).getByText("Inactive")).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Deactivate CS102L" })).not.toBeInTheDocument();
  await user.click(within(card).getByRole("button", { name: "Activate CS102L" }));

  expect(api.updateSubject).toHaveBeenCalledWith("CS102L", { is_active: true });
  expect(api.listSubjects).toHaveBeenCalledTimes(2);
});

it("says when only inactive subjects match the search", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [{ ...laboratory, is_active: false }] });
  renderPage();

  expect(await screen.findByText('Only inactive subjects match. Tick "Show inactive" to see them.')).toBeInTheDocument();
});

it("shows why an activation was refused", async () => {
  vi.mocked(api.listSubjects).mockResolvedValue({ items: [{ ...lecture, is_active: false }] });
  vi.mocked(api.updateSubject).mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Could not save."));
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByRole("checkbox", { name: "Show inactive (1)" }));

  await user.click(screen.getByRole("button", { name: "Activate CS101" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not save.");
});

it("reports a failed load instead of showing an empty catalog", async () => {
  vi.mocked(api.listSubjects).mockRejectedValue(
    new ApiError(503, "SERVICE_UNAVAILABLE", "The local API is unreachable."),
  );
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("The local API is unreachable.");
});

it("shows a retryable load error instead of the empty catalog message", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listSubjects).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load subjects."));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load subjects.");
  expect(screen.queryByText(/No subjects recorded yet/)).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("CS101")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps the load error mounted and focused while a retry is in flight and re-announces a second failure", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listSubjects).mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Could not load subjects."));
  renderPage();
  await screen.findByRole("alert");

  let fail: (reason: unknown) => void = () => {};
  vi.mocked(api.listSubjects).mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; }));
  const retry = screen.getByRole("button", { name: "Retry" });
  await user.click(retry);

  const busy = screen.getByRole("button", { name: /Retrying/ });
  expect(busy).toBe(retry);
  expect(busy).toHaveAttribute("aria-disabled", "true");
  expect(busy).toHaveFocus();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("Loading subjects...")).not.toBeInTheDocument();
  expect(screen.queryByText(/No subjects recorded yet/)).not.toBeInTheDocument();
  await user.click(busy);
  expect(api.listSubjects).toHaveBeenCalledTimes(2);

  await act(async () => fail(new ApiError(500, "INTERNAL_ERROR", "Still down.")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Still down.");
  expect(screen.getByRole("button", { name: "Retry" })).toHaveFocus();
});
