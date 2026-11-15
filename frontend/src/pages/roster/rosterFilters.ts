import type { Faculty, TeachingDay, TimeSlot } from "../../api/client";
import { toSeconds } from "./timeWindows";

export type DayPart = "morning" | "afternoon" | "evening";

export const DAY_PART_LABEL: Record<DayPart, string> = {
  morning: "Morning (before 12 PM)",
  afternoon: "Afternoon (12–5 PM)",
  evening: "Evening (from 5 PM)",
};

/** Roster filters beyond search and classification; "" means "any". */
export interface RosterFilters {
  subject: string;
  day: TeachingDay | "";
  dayPart: DayPart | "";
}

export const NO_ROSTER_FILTERS: RosterFilters = { subject: "", day: "", dayPart: "" };

export const activeFilterCount = (filters: RosterFilters) =>
  [filters.subject, filters.day, filters.dayPart].filter(Boolean).length;

const NOON = 12 * 3600;
const FIVE_PM = 17 * 3600;

// Same cut points as the availability dialog's Morning/Afternoon/Evening.
function partOf(slot: Pick<TimeSlot, "start_time">): DayPart {
  const start = toSeconds(slot.start_time);
  return start < NOON ? "morning" : start < FIVE_PM ? "afternoon" : "evening";
}

/**
 * The parts of the day in which a member has at least one available slot.
 *
 * Mirrors the backend AvailabilityMaskCodec: one bit per slot in
 * (ordinal, id) order, and an empty mask means "open in every slot". A mask
 * that does not fit the grid (readiness flags it) returns null: unknown.
 */
export function freeDayParts(
  mask: string,
  grid: readonly Pick<TimeSlot, "time_slot_id" | "ordinal" | "start_time">[],
): Set<DayPart> | null {
  const ordered = [...grid].sort((a, b) => a.ordinal - b.ordinal || (a.time_slot_id < b.time_slot_id ? -1 : a.time_slot_id > b.time_slot_id ? 1 : 0));
  if (mask === "") return new Set(ordered.map(partOf));
  if (mask.length !== ordered.length || /[^01]/.test(mask)) return null;
  return new Set(ordered.filter((_, index) => mask[index] === "1").map(partOf));
}

/** True when the member passes every set filter. A day-part filter needs the slot grid. */
export function matchesRosterFilters(
  item: Faculty,
  filters: RosterFilters,
  grid: readonly Pick<TimeSlot, "time_slot_id" | "ordinal" | "start_time">[] | null,
): boolean {
  if (filters.subject && !(item.preferred_subject_codes ?? []).includes(filters.subject)) return false;
  if (filters.day && !(item.preferred_days ?? []).includes(filters.day)) return false;
  if (filters.dayPart && grid) {
    if (!freeDayParts(item.availability_mask, grid)?.has(filters.dayPart)) return false;
  }
  return true;
}
