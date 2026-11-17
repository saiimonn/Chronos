// What each schedule-quality metric means and the ideal it is judged against.
// Sources and the evidence behind each ideal are in the private docs:
// docs/SCHEDULE_QUALITY_METRICS.md. Keep the two in step.

import type { ScheduleMetrics } from "../../api/client";

export type MetricKey =
  | "completeness"
  | "conflicts"
  | "roomUtilization"
  | "facultyLoad"
  | "dayBalance"
  | "preference";

export interface MetricHelp {
  description: string;
  formula: string;
  ideal: string;
  // True when the schedule meets the ideal; null when there is nothing to judge.
  passes: (metrics: ScheduleMetrics) => boolean | null;
}

// Universities are observed at about 20-40% room utilization; meeting the low
// end counts as a pass (see the docs).
export const ROOM_UTILIZATION_IDEAL = 0.2;

function atLeast(value: number | null | undefined, ideal: number): boolean | null {
  return value === null || value === undefined ? null : value >= ideal - 1e-9;
}

export const METRIC_HELP: Record<MetricKey, MetricHelp> = {
  completeness: {
    description: "Share of the term's required classes that have a teacher, room and time.",
    formula: "Classes assigned ÷ classes required",
    ideal: "100% (a valid schedule places every class)",
    passes: (m) => atLeast(m.feasibility.completeness, 1),
  },
  conflicts: {
    description: "Clashes the validator finds, such as a teacher, room or Block booked twice at once.",
    formula: "Count of hard-constraint violations",
    ideal: "0 (a valid schedule has no hard conflicts)",
    passes: (m) => m.feasibility.hard_conflict_count === 0,
  },
  roomUtilization: {
    description: "How much of the available room time is used, and how full the rooms are while in use. Seats filled are estimated from Block capacity, since enrolment is not recorded.",
    formula: "Frequency (room-hours used ÷ room-hours available) × occupancy (seats filled ÷ seats offered)",
    ideal: "20% or more (universities are typically observed at 20–40%)",
    passes: (m) => atLeast(m.rooms.find((item) => item.room_type === "all")?.utilization, ROOM_UTILIZATION_IDEAL),
  },
  facultyLoad: {
    description: "Teaching units assigned across all active faculty, against their maximum loads.",
    formula: "Units assigned ÷ maximum units (full-time 24, part-time 17)",
    ideal: "No member above their maximum load",
    passes: (m) => m.faculty.overloaded === 0,
  },
  dayBalance: {
    description: "Blocks whose class hours are spread evenly over Monday to Thursday.",
    formula: "Blocks whose heaviest and lightest days differ by 1.5 h or less ÷ Blocks",
    ideal: "100% of Blocks balanced",
    passes: (m) => atLeast(m.blocks.balanced.value, 1),
  },
  preference: {
    description: "Classes of a subject some teacher prefers that went to a teacher who prefers it.",
    formula: "Sessions taught by a preferring teacher ÷ sessions of a preferred subject",
    ideal: "100% of those sessions",
    passes: (m) => atLeast(m.faculty.preference.value, 1),
  },
};
