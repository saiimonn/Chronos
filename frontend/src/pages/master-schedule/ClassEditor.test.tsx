import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Faculty, Subject, TimeSlot } from "../../api/client";
import ClassEditor, { type OccupiedMeeting } from "./ClassEditor";
import { EMPTY_CATALOG, type Catalog } from "./catalog";

const slot = (id: string, start: string, end: string, ordinal: number, kind = "LEC"): TimeSlot => ({
  time_slot_id: id,
  term_id: "term",
  label: `${kind} ${start}-${end}`,
  start_time: `${start}:00`,
  end_time: `${end}:00`,
  ordinal,
});

// Nothing starts at 12:00 or 21:00, so the 10:30 and 19:30 windows cannot
// hold a 3h meeting.
const timeSlots = [
  slot("s1", "07:30", "09:00", 1),
  slot("s2", "09:00", "10:30", 2),
  slot("s3", "10:30", "12:00", 3),
  slot("s4", "19:30", "21:00", 4),
  slot("l1", "07:30", "10:00", 5, "LAB"),
  slot("l2", "10:00", "12:30", 6, "LAB"),
];

const catalog: Catalog = {
  ...EMPTY_CATALOG,
  subjects: [{ subject_code: "CS101", subject_title: "Intro", units: 3, subject_type: "LEC", duration_minutes: 90 } as Subject],
  timeSlots,
};

function renderEditor(meetingDays: string, options: { catalog?: Catalog; facultyId?: string; slotId?: string; occupied?: OccupiedMeeting[]; termNumber?: number } = {}) {
  render(
    <ClassEditor
      title="Edit"
      intro=""
      submitLabel="Save"
      classId="c1"
      subjectCode="CS101"
      blockId="b1"
      initial={{ faculty_id: options.facultyId ?? "", room_code: "", meeting_days: meetingDays, time_slot_id: options.slotId ?? "s2" }}
      catalog={options.catalog ?? catalog}
      loads={new Map()}
      occupied={options.occupied ?? []}
      onSubmit={vi.fn()}
      onClose={vi.fn()}
      termNumber={options.termNumber}
    />,
  );
}

async function windowOptions() {
  await userEvent.click(screen.getByRole("button", { name: "Show Time window options" }));
  return within(screen.getByRole("listbox", { name: "Time window" })).getAllByRole("option").map((option) => option.textContent);
}

it("offers only whole 3h windows for a Friday-only lecture, updating as the days change", async () => {
  renderEditor("MW");
  expect(screen.getByText(/Meets Monday, Wednesday, 09:00-10:30: 1h 30m each day\./)).toBeInTheDocument();

  // Unticking Monday clears MW: Wednesday alone is no pattern.
  await userEvent.click(screen.getByRole("checkbox", { name: "Monday" }));
  expect(screen.getByRole("checkbox", { name: "Wednesday" })).not.toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: "Friday" }));

  expect(screen.getByText(/Meets Friday, 09:00-12:00: 3h, both weekly sessions back to back\./)).toBeInTheDocument();
  // 10:30 and 19:30 have no following slot: their 3h meeting cannot be held.
  expect(await windowOptions()).toEqual(["LEC 07:30-10:303h", "LEC 09:00-12:003h"]);
  // Even showing all options, a lecture is offered no 5h LAB window.
  await userEvent.click(screen.getByRole("checkbox", { name: "Show all options" }));
  expect(await windowOptions()).toEqual(["LEC 07:30-10:303h", "LEC 09:00-12:003h"]);
});

it("picks meeting days one allowed pattern at a time", async () => {
  renderEditor("");
  await userEvent.click(screen.getByRole("checkbox", { name: "Monday" }));
  expect(screen.getByRole("checkbox", { name: "Wednesday" })).toBeChecked();
  for (const day of ["Tuesday", "Thursday", "Friday", "Saturday", "Sunday"]) {
    expect(screen.getByRole("checkbox", { name: day })).toBeDisabled();
  }
  expect(screen.getByText(/Pattern: MW/)).toBeInTheDocument();

  // Friday alone, then Saturday joins it as FSa; Monday stays closed.
  await userEvent.click(screen.getByRole("checkbox", { name: "Monday" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Friday" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Saturday" }));
  expect(screen.getByText(/Pattern: FSa/)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Monday" })).toBeDisabled();
});

it("packs a summer-term class into MTWTh", async () => {
  renderEditor("", { termNumber: 3 });
  await userEvent.click(screen.getByRole("checkbox", { name: "Tuesday" }));
  expect(screen.getByText(/Pattern: MTWTh/)).toBeInTheDocument();
});

it("splits a Friday-Saturday lecture into 1h30 each day", async () => {
  renderEditor("FSa");
  expect(screen.getByText(/Meets Friday, Saturday, 09:00-10:30: 1h 30m each day\./)).toBeInTheDocument();
  expect(await windowOptions()).toEqual([
    "LEC 07:30-09:001h 30m each day",
    "LEC 09:00-10:301h 30m each day",
    "LEC 10:30-12:001h 30m each day",
    "LEC 19:30-21:001h 30m each day",
  ]);
});

it("treats Saturday-only like Friday-only", async () => {
  renderEditor("Sa");
  expect(screen.getByText(/Meets Saturday, 09:00-12:00: 3h/)).toBeInTheDocument();
});

// Open in every slot except s3 (mask bits follow ordinal order).
const member = {
  faculty_id: "f1",
  full_name: "Cruz, Ana",
  is_active: true,
  is_full_time: true,
  availability_mask: "110111",
} as Faculty;

it("hides time windows the instructor is unavailable or busy in, even showing all options", async () => {
  const busy: OccupiedMeeting = {
    classId: "other", blockId: "b2", label: "IT210 B2", facultyId: "f1", roomCode: null, days: ["F"], start: 7 * 60 + 30, end: 10 * 60 + 30,
  };
  renderEditor("F", { catalog: { ...catalog, faculty: [member] }, facultyId: "f1", slotId: "s1", occupied: [busy] });

  // 07:30 is the saved window, so it stays, marked busy. 09:00-12:00 needs
  // s3 for its second session, and the instructor is unavailable then.
  expect(await windowOptions()).toEqual(["LEC 07:30-10:303h · instructor busy"]);
  await userEvent.click(screen.getByRole("checkbox", { name: "Show all options" }));
  expect(await windowOptions()).toEqual(["LEC 07:30-10:303h · instructor busy"]);
});

it("lists instructors who prefer the subject first", async () => {
  const other = { ...member, faculty_id: "f2", full_name: "Abad, Ben", availability_mask: "111111" } as Faculty;
  const preferring = { ...other, faculty_id: "f3", full_name: "Diaz, Cara", preferred_subject_codes: ["CS101"] } as Faculty;
  renderEditor("MW", { catalog: { ...catalog, faculty: [other, preferring] }, slotId: "s1" });
  await userEvent.click(screen.getByRole("button", { name: "Show Instructor options" }));
  const names = within(screen.getByRole("listbox", { name: "Instructor" })).getAllByRole("option").map((option) => option.textContent);
  expect(names[0]).toMatch(/^Diaz, Cara.*prefers this subject/);
  expect(names[1]).toMatch(/^Abad, Ben/);
  expect(names[1]).not.toMatch(/prefers/);
});

it("lists only the instructor's open windows on two-day patterns", async () => {
  renderEditor("MW", { catalog: { ...catalog, faculty: [member] }, facultyId: "f1", slotId: "s1" });
  expect(await windowOptions()).toEqual([
    "LEC 07:30-09:001h 30m each day",
    "LEC 09:00-10:301h 30m each day",
    "LEC 19:30-21:001h 30m each day",
  ]);
});
