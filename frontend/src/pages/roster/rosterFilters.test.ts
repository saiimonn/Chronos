import { describe, expect, it } from "vitest";
import type { Faculty } from "../../api/client";
import { NO_ROSTER_FILTERS, activeFilterCount, freeDayParts, matchesRosterFilters } from "./rosterFilters";

// Listed out of order on purpose: the mask follows ordinal order.
const grid = [
  { time_slot_id: "c", ordinal: 2, start_time: "17:30:00" },
  { time_slot_id: "a", ordinal: 0, start_time: "07:30:00" },
  { time_slot_id: "b", ordinal: 1, start_time: "13:00:00" },
];

const member: Faculty = {
  faculty_id: "f1",
  full_name: "A. Rivera",
  is_full_time: true,
  is_active: true,
  preferred_days: ["MON", "WED"],
  preferred_subject_codes: ["CIS 1101"],
  availability_mask: "100",
  availability_reviewed_term_id: null,
};

describe("freeDayParts", () => {
  it("treats an unset mask as open in every slot", () => {
    expect(freeDayParts("", grid)).toEqual(new Set(["morning", "afternoon", "evening"]));
  });

  it("reads one bit per slot in ordinal order", () => {
    expect(freeDayParts("001", grid)).toEqual(new Set(["evening"]));
    expect(freeDayParts("110", grid)).toEqual(new Set(["morning", "afternoon"]));
  });

  it("returns null for a mask that does not fit the grid", () => {
    expect(freeDayParts("10", grid)).toBeNull();
    expect(freeDayParts("1x0", grid)).toBeNull();
  });
});

describe("matchesRosterFilters", () => {
  it("passes everyone when no filter is set", () => {
    expect(matchesRosterFilters(member, NO_ROSTER_FILTERS, grid)).toBe(true);
    expect(activeFilterCount(NO_ROSTER_FILTERS)).toBe(0);
  });

  it("matches on preferred subject and preferred day", () => {
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, subject: "CIS 1101" }, grid)).toBe(true);
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, subject: "CIS 2101" }, grid)).toBe(false);
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, day: "WED" }, grid)).toBe(true);
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, day: "FRI" }, grid)).toBe(false);
  });

  it("matches on free time of day from the availability mask", () => {
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, dayPart: "morning" }, grid)).toBe(true);
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, dayPart: "evening" }, grid)).toBe(false);
    expect(matchesRosterFilters({ ...member, availability_mask: "1" }, { ...NO_ROSTER_FILTERS, dayPart: "morning" }, grid)).toBe(false);
  });

  it("ignores the time filter while the slot grid is unknown", () => {
    expect(matchesRosterFilters(member, { ...NO_ROSTER_FILTERS, dayPart: "evening" }, null)).toBe(true);
  });

  it("combines filters with AND", () => {
    const filters = { subject: "CIS 1101", day: "MON" as const, dayPart: "evening" as const };
    expect(activeFilterCount(filters)).toBe(3);
    expect(matchesRosterFilters(member, filters, grid)).toBe(false);
  });
});
