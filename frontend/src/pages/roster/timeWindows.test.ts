import { describe, expect, it } from "vitest";
import {
  buildRanges,
  coverageByLength,
  dayParts,
  rangeBoundaries,
  rangesBetween,
  rangesFromSlots,
  slotsFromRanges,
  timeOfDay,
  toSeconds,
  type GridSlot,
} from "./timeWindows";

const slot = (id: string, start: string, end: string, ordinal: number): GridSlot => ({
  time_slot_id: id, label: id, start_time: start, end_time: end, ordinal,
});

// The canonical shape: a 90- and a 150-minute window at every half hour.
const grid = [
  slot("lec-0730", "07:30:00", "09:00:00", 0),
  slot("lec-0800", "08:00:00", "09:30:00", 1),
  slot("lec-0830", "08:30:00", "10:00:00", 2),
  slot("lab-0730", "07:30:00", "10:00:00", 3),
];

describe("time windows", () => {
  it("cuts the day at every slot boundary", () => {
    expect(buildRanges(grid).map((range) => range.label)).toEqual([
      "07:30–08:00", "08:00–08:30", "08:30–09:00", "09:00–09:30", "09:30–10:00",
    ]);
  });

  it("makes a slot available only when every range it spans is", () => {
    const ranges = buildRanges(grid);
    expect(slotsFromRanges(new Set([0, 1, 2]), grid, ranges)).toEqual(["lec-0730"]);
    expect(slotsFromRanges(new Set([0, 1, 2, 3, 4]), grid, ranges)).toEqual(["lec-0730", "lec-0800", "lec-0830", "lab-0730"]);
    expect(slotsFromRanges(new Set(), grid, ranges)).toEqual([]);
  });

  it("reads a lab-only selection as the stretch it covers, filling in the lectures inside", () => {
    const ranges = buildRanges(grid);
    const selected = rangesFromSlots(["lab-0730"], grid, ranges);
    expect([...selected].sort()).toEqual([0, 1, 2, 3, 4]);
    expect(slotsFromRanges(selected, grid, ranges)).toEqual(["lec-0730", "lec-0800", "lec-0830", "lab-0730"]);
  });

  it("keeps ranges a few seconds apart distinct and skips gaps no slot covers", () => {
    const ranges = buildRanges([
      slot("a", "08:00:00", "09:00:00", 0),
      slot("b", "08:00:30", "09:00:00", 1),
      slot("c", "13:00:00", "14:00:00", 2),
    ]);
    expect(ranges.map((range) => range.label)).toEqual(["08:00–08:00:30", "08:00:30–09:00", "13:00–14:00"]);
    expect(dayParts(ranges).map((part) => [part.key, part.indices])).toEqual([["morning", [0, 1]], ["afternoon", [2]]]);
  });

  it("counts available windows by length", () => {
    expect(coverageByLength(["lec-0730", "lab-0730"], grid)).toEqual([
      { minutes: 90, available: 1, total: 3 },
      { minutes: 150, available: 1, total: 1 },
    ]);
  });

  it("reads boundaries as clock times and selects the rows wholly inside a range", () => {
    const ranges = buildRanges(grid);
    expect(rangeBoundaries(ranges).map(timeOfDay)).toEqual([
      "7:30 AM", "8:00 AM", "8:30 AM", "9:00 AM", "9:30 AM", "10:00 AM",
    ]);
    expect(timeOfDay(toSeconds("12:00:00"))).toBe("12:00 PM");
    expect(timeOfDay(toSeconds("13:30:00"))).toBe("1:30 PM");
    expect(rangesBetween(ranges, toSeconds("07:30:00"), toSeconds("09:00:00"))).toEqual([0, 1, 2]);
    expect(rangesBetween(ranges, toSeconds("09:00:00"), toSeconds("09:30:00"))).toEqual([3]);
  });
});
