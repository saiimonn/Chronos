import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import WeekScheduleGrid, { type WeekGridEntry } from "./WeekScheduleGrid";

const base: WeekGridEntry = {
  id: "a",
  days: ["M", "W"],
  start: "08:00:00",
  end: "09:30:00",
  subjectCode: "CS101",
  subjectTitle: "Intro to Computing",
  room: "LB 483",
  teacher: "A. Rivera",
};

it("draws 30-minute rows, one column per teaching day, and a class on each of its days", () => {
  render(<WeekScheduleGrid label="Week" entries={[base]} dayStart={7 * 60 + 30} dayEnd={12 * 60} />);

  const grid = screen.getByRole("group", { name: "Week" });
  for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]) {
    expect(within(grid).getByRole("list", { name: day })).toBeInTheDocument();
  }
  // 7:30 to 12:00 is nine half-hour rows.
  expect(within(grid).getByText("7:30 AM")).toBeInTheDocument();
  expect(within(grid).getByText("11:30 AM")).toBeInTheDocument();
  expect(within(grid).queryByText("12:00 PM")).not.toBeInTheDocument();
  expect(within(within(grid).getByRole("list", { name: "Monday" })).getByText("CS101")).toBeInTheDocument();
  expect(within(within(grid).getByRole("list", { name: "Tuesday" })).queryByText("CS101")).not.toBeInTheDocument();
  expect(within(grid).getAllByText("8:00 AM – 9:30 AM")).toHaveLength(2);
});

it("puts overlapping classes side by side and flags a conflict", () => {
  const clash = { ...base, id: "b", days: ["M" as const], start: "09:00", end: "10:30", subjectCode: "CS102", conflict: "Room double-booked" };
  render(<WeekScheduleGrid label="Week" entries={[base, clash]} />);

  const monday = within(screen.getByRole("list", { name: "Monday" }));
  const first = monday.getByLabelText(/^CS101/);
  const second = monday.getByLabelText(/^CS102.*conflict: Room double-booked/);
  expect(first.style.width).toBe("calc(50% - 4px)");
  expect(second.style.left).toBe("calc(50% + 2px)");
});

it("makes entries buttons only when they can be selected, never ghosts", async () => {
  const onSelect = vi.fn();
  const ghost = { ...base, id: "g", variant: "ghost" as const };
  render(<WeekScheduleGrid label="Week" entries={[ghost, base]} onSelect={onSelect} selectVerb="Edit" />);

  const buttons = screen.getAllByRole("button");
  expect(buttons).toHaveLength(2);
  await userEvent.click(buttons[0]);
  expect(onSelect).toHaveBeenCalledWith(base);
  expect(screen.getAllByLabelText(/currently approved$/)).toHaveLength(2);
});
