import type { Availability } from "../../api/client";

export type GridSlot = Availability["slot_grid"][number];

/**
 * One selectable stretch of the teaching day, e.g. 07:30–08:00.
 *
 * The availability mask has one bit per canonical slot, and the canonical
 * grid has a 90-minute (LEC) and a 150-minute (LAB) slot at every start time.
 * Asking an operator to tick both kinds separately is tedious and lets the two
 * disagree, so the roster edits *time ranges* instead: the day is cut at every
 * slot boundary, and a slot is available exactly when every range it spans is.
 * One range therefore stands for both lengths at once, and a lab can never be
 * offered in a stretch where a lecture of the same hours is not.
 */
export interface TimeRange {
  start: number; // seconds since midnight
  end: number;
  label: string;
}

export function toSeconds(time: string): number {
  const [hours, minutes, seconds = 0] = time.split(":").map(Number);
  return hours * 3600 + minutes * 60 + seconds;
}

function clock(total: number): string {
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const base = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  // Seconds only when present, so two ranges a few seconds apart never read alike.
  return seconds ? `${base}:${String(seconds).padStart(2, "0")}` : base;
}

/** The ranges between consecutive slot boundaries that some slot covers. */
export function buildRanges(slots: readonly GridSlot[]): TimeRange[] {
  const spans = slots
    .map((slot) => [toSeconds(slot.start_time), toSeconds(slot.end_time)] as const)
    .filter(([start, end]) => end > start);
  const cuts = [...new Set(spans.flat())].sort((a, b) => a - b);
  const ranges: TimeRange[] = [];
  for (let index = 0; index + 1 < cuts.length; index += 1) {
    const start = cuts[index];
    const end = cuts[index + 1];
    if (spans.some(([from, to]) => from <= start && end <= to)) {
      ranges.push({ start, end, label: `${clock(start)}–${clock(end)}` });
    }
  }
  return ranges;
}

/** A boundary time as an operator reads it, e.g. 27000 -> "7:30 AM". */
export function timeOfDay(total: number): string {
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const suffix = hours < 12 ? "AM" : "PM";
  return `${hours % 12 === 0 ? 12 : hours % 12}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

/** Every distinct start and end time, in order, for the From/To pickers. */
export function rangeBoundaries(ranges: readonly TimeRange[]): number[] {
  return [...new Set(ranges.flatMap((range) => [range.start, range.end]))].sort((a, b) => a - b);
}

/** Indices of the ranges that lie wholly inside [from, to]. */
export function rangesBetween(ranges: readonly TimeRange[], from: number, to: number): number[] {
  return ranges.flatMap((range, index) => (from <= range.start && range.end <= to ? [index] : []));
}

/** Indices of the ranges a slot spans; empty for a zero-length slot. */
export function rangesOfSlot(slot: GridSlot, ranges: readonly TimeRange[]): number[] {
  const start = toSeconds(slot.start_time);
  const end = toSeconds(slot.end_time);
  return ranges.flatMap((range, index) => (start <= range.start && range.end <= end ? [index] : []));
}

/** The ranges a stored slot selection marks free: any range a chosen slot covers. */
export function rangesFromSlots(
  slotIds: readonly string[],
  slots: readonly GridSlot[],
  ranges: readonly TimeRange[],
): Set<number> {
  const chosen = new Set(slotIds);
  const selected = new Set<number>();
  for (const slot of slots) {
    if (chosen.has(slot.time_slot_id)) rangesOfSlot(slot, ranges).forEach((index) => selected.add(index));
  }
  return selected;
}

/** Every slot, in grid order, whose whole span lies in the selected ranges. */
export function slotsFromRanges(
  selected: ReadonlySet<number>,
  slots: readonly GridSlot[],
  ranges: readonly TimeRange[],
): string[] {
  return slots
    .filter((slot) => {
      const covered = rangesOfSlot(slot, ranges);
      return covered.length > 0 && covered.every((index) => selected.has(index));
    })
    .map((slot) => slot.time_slot_id);
}

export interface DayPart {
  key: "morning" | "afternoon" | "evening";
  label: string;
  indices: number[];
}

const NOON = 12 * 3600;
const FIVE_PM = 17 * 3600;

/** Morning (before 12:00), afternoon (12:00–17:00), evening (from 17:00); empty parts dropped. */
export function dayParts(ranges: readonly TimeRange[]): DayPart[] {
  const parts: DayPart[] = [
    { key: "morning", label: "Morning", indices: [] },
    { key: "afternoon", label: "Afternoon", indices: [] },
    { key: "evening", label: "Evening", indices: [] },
  ];
  ranges.forEach((range, index) => {
    const part = range.start < NOON ? 0 : range.start < FIVE_PM ? 1 : 2;
    parts[part].indices.push(index);
  });
  return parts.filter((part) => part.indices.length > 0);
}

/** How many slots of each length the selection makes available. */
export function coverageByLength(
  selectedSlotIds: readonly string[],
  slots: readonly GridSlot[],
): { minutes: number; available: number; total: number }[] {
  const chosen = new Set(selectedSlotIds);
  const byLength = new Map<number, { available: number; total: number }>();
  for (const slot of slots) {
    const minutes = Math.round((toSeconds(slot.end_time) - toSeconds(slot.start_time)) / 60);
    const entry = byLength.get(minutes) ?? { available: 0, total: 0 };
    entry.total += 1;
    if (chosen.has(slot.time_slot_id)) entry.available += 1;
    byLength.set(minutes, entry);
  }
  return [...byLength.entries()]
    .sort(([a], [b]) => a - b)
    .map(([minutes, entry]) => ({ minutes, ...entry }));
}
