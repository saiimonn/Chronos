import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { ScheduleValidation } from "../../api/client";
import ScheduleEligibility from "./ScheduleEligibility";

const clean: ScheduleValidation = {
  required_class_count: 4,
  assigned_class_count: 4,
  completeness_percentage: 100,
  unassigned_class_ids: [],
  hard_conflict_count: 0,
  conflicts: [],
  export_allowed: true,
};

it("reports finalization eligibility from the server verdict", () => {
  render(<ScheduleEligibility validation={clean} finalized={false} />);
  expect(screen.getByRole("status")).toHaveTextContent("Ready to finalize.");
});

it("reports export eligibility once finalized", () => {
  render(<ScheduleEligibility validation={clean} finalized />);
  expect(screen.getByRole("status")).toHaveTextContent("Ready to export.");
});

it("lists the server's blocker reasons when eligibility is withheld", () => {
  render(
    <ScheduleEligibility
      finalized={false}
      validation={{
        ...clean,
        assigned_class_count: 3,
        completeness_percentage: 75,
        unassigned_class_ids: ["class-4"],
        hard_conflict_count: 1,
        conflicts: [{ code: "ROOM_DOUBLE_BOOKED", resource_key: ["LB 101"], class_ids: ["a", "b"], time_slot_ids: ["s"], message: "Room LB 101 is double-booked." }],
        export_allowed: false,
      }}
    />,
  );
  const panel = screen.getByRole("region", { name: "Schedule eligibility" });
  expect(panel).toHaveTextContent("Finalization blocked by server validation.");
  expect(panel).toHaveTextContent("1 of 4 required classes unassigned (75% complete).");
  expect(panel).toHaveTextContent("1 conflict error:");
  expect(panel).toHaveTextContent("Room LB 101 is double-booked.");
});

it("never infers eligibility from counts the server did not clear", () => {
  render(<ScheduleEligibility validation={{ ...clean, export_allowed: false }} finalized={false} />);
  const panel = screen.getByRole("region", { name: "Schedule eligibility" });
  expect(panel).toHaveTextContent("withheld eligibility without a listed reason");
  expect(panel).not.toHaveTextContent("Ready to finalize.");
});

it("names an empty term as the reason when no classes are required", () => {
  const empty = { ...clean, required_class_count: 0, assigned_class_count: 0, completeness_percentage: 0, export_allowed: false };
  render(<ScheduleEligibility validation={empty} finalized={false} />);
  const panel = screen.getByRole("region", { name: "Schedule eligibility" });
  expect(panel).toHaveTextContent("No required classes in this term.");
  expect(panel).not.toHaveTextContent("without a listed reason");
});

it("never rounds an incomplete schedule up to 100%", () => {
  render(
    <ScheduleEligibility
      finalized={false}
      validation={{ ...clean, required_class_count: 250, assigned_class_count: 249, completeness_percentage: 99.6, unassigned_class_ids: ["c"], export_allowed: false }}
    />,
  );
  expect(screen.getByRole("region", { name: "Schedule eligibility" })).toHaveTextContent("1 of 250 required classes unassigned (99% complete).");
});

it("reports exact percentages the server's float would under-floor", () => {
  render(
    <ScheduleEligibility
      finalized={false}
      // 29 / 100 * 100 is 28.999999999999996 in floating point.
      validation={{ ...clean, required_class_count: 100, assigned_class_count: 29, completeness_percentage: (29 / 100) * 100, unassigned_class_ids: Array.from({ length: 71 }, (_, i) => `c${i}`), export_allowed: false }}
    />,
  );
  expect(screen.getByRole("region", { name: "Schedule eligibility" })).toHaveTextContent("71 of 100 required classes unassigned (29% complete).");
});

it("keeps one live region across verdict changes", () => {
  const blocked = { ...clean, unassigned_class_ids: ["c"], assigned_class_count: 3, completeness_percentage: 75, export_allowed: false };
  const view = render(<ScheduleEligibility validation={blocked} finalized={false} />);
  const live = screen.getByRole("status");
  expect(live).toHaveTextContent("Finalization blocked by server validation.");

  view.rerender(<ScheduleEligibility validation={clean} finalized={false} />);
  expect(screen.getByRole("status")).toBe(live);
  expect(live).toHaveTextContent("Ready to finalize.");

  view.rerender(<ScheduleEligibility validation={null} finalized={false} />);
  expect(screen.getByRole("status")).toBe(live);
  expect(live).toHaveTextContent("eligibility is unknown");
});

it("caps the conflict list and keeps it out of the live region", () => {
  const conflicts = Array.from({ length: 25 }, (_, index) => ({
    code: "ROOM_DOUBLE_BOOKED", resource_key: [`R${index}`], class_ids: [], time_slot_ids: [], message: `Conflict ${index + 1}.`,
  }));
  render(<ScheduleEligibility validation={{ ...clean, hard_conflict_count: 25, conflicts, export_allowed: false }} finalized={false} />);
  const panel = screen.getByRole("region", { name: "Schedule eligibility" });
  expect(panel).toHaveTextContent("Conflict 20.");
  expect(panel).not.toHaveTextContent("Conflict 21.");
  expect(panel).toHaveTextContent("and 5 more.");
  const live = screen.getByRole("status");
  expect(live).toHaveTextContent("Finalization blocked by server validation.");
  expect(live).not.toHaveTextContent("Conflict 1.");
});

it("says eligibility is unknown when validation could not be loaded", () => {
  render(<ScheduleEligibility validation={null} finalized={false} />);
  expect(screen.getByRole("status")).toHaveTextContent("eligibility is unknown");
});
