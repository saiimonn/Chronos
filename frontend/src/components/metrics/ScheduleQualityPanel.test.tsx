import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type ScheduleMetrics } from "../../api/client";
import ScheduleQualityPanel, { percent } from "./ScheduleQualityPanel";

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return {
    ...original,
    api: { ...original.api, getMasterSchedule: vi.fn(), getScheduleMetrics: vi.fn() },
  };
});

const ratio = (numerator: number, denominator: number) => ({
  numerator,
  denominator,
  value: denominator ? numerator / denominator : null,
});

const metrics: ScheduleMetrics = {
  schedule_id: "s-1",
  feasibility: { required_class_count: 120, assigned_class_count: 120, completeness: 1, hard_conflict_count: 0, valid: true },
  rooms: [
    { room_type: "all", rooms_total: 10, rooms_used: 9, frequency: ratio(300, 475), occupancy: ratio(4000, 5000), utilization: (300 / 475) * 0.8 },
    { room_type: "lecture", rooms_total: 6, rooms_used: 6, frequency: ratio(200, 285), occupancy: ratio(1, 1), utilization: null },
    { room_type: "laboratory", rooms_total: 4, rooms_used: 3, frequency: ratio(100, 190), occupancy: ratio(1, 1), utilization: null },
  ],
  faculty: {
    faculty_active: 20, faculty_teaching: 18, load: ratio(360, 440), mean_load_ratio: 0.85, load_cv: 0.1,
    overloaded: 0, preference: ratio(30, 40), mean_teaching_days: 3.5, mean_idle_hours_per_day: 1,
  },
  blocks: { blocks_scheduled: 16, mean_spread_hours: 1.2, balanced: ratio(12, 16), mean_idle_hours_per_day: 0.8, mean_days_per_week: 4, max_daily_hours: 7 },
  time: { sessions: 100, evening: ratio(5, 100), weekend: ratio(0, 100), starts_by_slot: [], peak_slot_share: 0.2 },
  window_hours_per_day: 9.5,
  class_days: 5,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getMasterSchedule).mockResolvedValue({ schedule_id: "s-1" } as never);
  vi.mocked(api.getScheduleMetrics).mockResolvedValue(metrics);
});

function renderPanel() {
  return render(<MemoryRouter><ScheduleQualityPanel termId="term-1" /></MemoryRouter>);
}

it("colours each value by its ideal and keeps the detail below it", async () => {
  renderPanel();
  const rooms = await screen.findByRole("article", { name: "Room utilization" });
  const value = within(rooms).getByText(percent(metrics.rooms[0].utilization, 1));
  expect(value).toHaveClass("text-secondary");
  expect(rooms).toHaveAttribute("data-status", "pass");
  expect(value.nextElementSibling).toHaveTextContent("9 of 10 rooms used");

  const completeness = screen.getByRole("article", { name: "Assignment completeness" });
  expect(within(completeness).getByText("100.0%")).toHaveClass("text-secondary");
  expect(api.getScheduleMetrics).toHaveBeenCalledWith("s-1");
});

it("colours a value red when it misses its ideal", async () => {
  vi.mocked(api.getScheduleMetrics).mockResolvedValue({
    ...metrics,
    faculty: { ...metrics.faculty, overloaded: 2 },
  });
  renderPanel();
  const load = await screen.findByRole("article", { name: "Faculty load" });
  expect(within(load).getByText(percent(metrics.faculty.load.value, 1))).toHaveClass("text-error");
  expect(load).toHaveAttribute("data-status", "fail");
});

it("shows only the three thesis metrics; day balance and preference stay API-only", async () => {
  renderPanel();
  await screen.findByRole("article", { name: "Room utilization" });
  expect(screen.getAllByRole("article").map((card) => card.getAttribute("aria-label"))).toEqual([
    "Assignment completeness",
    "Room utilization",
    "Faculty load",
  ]);
  expect(screen.queryByRole("article", { name: "Balanced Block weeks" })).not.toBeInTheDocument();
  expect(screen.queryByRole("article", { name: "Subject preference met" })).not.toBeInTheDocument();
});

it("puts the description, formula and ideal in the help tooltip, with no sources", async () => {
  renderPanel();
  const help = await screen.findByRole("button", { name: "About Room utilization" });
  const tip = document.getElementById(help.getAttribute("aria-describedby") ?? "");
  expect(tip).toHaveTextContent(/Ideal: 20% or more/);
  expect(tip).toHaveTextContent(/Formula: Frequency/);
  expect(screen.queryByText(/Beyrouthy|doi\.org|Sources/)).not.toBeInTheDocument();
});

it("has no hard-conflict card: accepted schedules are conflict-free", async () => {
  renderPanel();
  await screen.findByRole("article", { name: "Room utilization" });
  expect(screen.queryByRole("article", { name: "Hard conflicts" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(3);
});

it("explains when the term has no master schedule", async () => {
  vi.mocked(api.getMasterSchedule).mockResolvedValue(null);
  renderPanel();
  expect(await screen.findByText(/No master schedule yet/)).toBeInTheDocument();
  expect(api.getScheduleMetrics).not.toHaveBeenCalled();
});
